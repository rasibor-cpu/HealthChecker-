package com.healthchecker.companion.host

import com.healthchecker.companion.BuildConfig
import com.healthchecker.companion.healthconnect.CompanionObservation
import com.healthchecker.companion.secure.SecurePrefs
import com.healthchecker.companion.sync.PendingBatch
import com.healthchecker.companion.sync.PendingBatchAck
import com.healthchecker.companion.util.SafeLog
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.util.concurrent.TimeUnit

/**
 * Authenticated delivery client for HC-303A/B host endpoints.
 * TLS required unless debug local-dev cleartext is explicitly enabled.
 * Reuses persisted batch identity across retries until durable acknowledgement.
 * New Health Connect readings are not merged into a frozen pending batch; they remain
 * unread in Health Connect until the pending identity is cleared and a subsequent fetch runs.
 */
class HostClient(
    private val prefs: SecurePrefs,
    private val client: OkHttpClient = OkHttpClient.Builder()
        .callTimeout(60, TimeUnit.SECONDS)
        .build()
) {
    data class DeliveryAck(
        val ok: Boolean,
        val status: String,
        val cursorAdvanced: Boolean,
        val nextCursorToken: String?,
        val error: String?,
        val ackBatchId: String? = null
    )

    fun confirmPairing(hostUrl: String, pairCode: String, deviceLabel: String): String? {
        val normalized = PairingInputs.normalize(hostUrl, pairCode)
        if (normalized is PairingInputs.Result.Invalid) {
            SafeLog.w("pair_confirm_rejected reason=" + normalized.reason)
            return normalized.reason
        }
        val inputs = normalized as PairingInputs.Result.Normalized
        val url = inputs.hostUrl + "/api/companion/pair/confirm"
        val body = JSONObject()
            .put("pair_code", inputs.pairCode)
            .put("device_label", deviceLabel)
            .put("platform", "android")
            .put("app_version", BuildConfig.VERSION_NAME)
        val req = Request.Builder()
            .url(url)
            .post(body.toString().toRequestBody(JSON))
            .header("Content-Type", "application/json")
            .applyLocalDevHeader(inputs.hostUrl)
            .build()
        return try {
            client.newCall(req).execute().use { resp ->
                val text = resp.body?.string().orEmpty()
                SafeLog.i("pair_confirm http=" + resp.code)
                val json = JSONObject(text)
                if (!json.optBoolean("ok")) {
                    return json.optJSONArray("errors")?.join(", ") ?: "pair_failed"
                }
                val deviceId = json.getString("device_id")
                val token = json.getString("device_token")
                // Atomic promote: active host + identity + token in one encrypted commit.
                if (!prefs.commitPairedSession(inputs.hostUrl, deviceId, token)) {
                    return "pairing_persist_failed"
                }
                null
            }
        } catch (t: Throwable) {
            SafeLog.e("pair_confirm_transport_failed", t)
            throw t
        }
    }

    fun deliver(
        observations: List<CompanionObservation>,
        nextChangesToken: String?,
        healthConnectStatus: JSONObject,
        permissions: JSONObject,
        workmanager: JSONObject,
        queued: Int,
        deletedRecordIds: List<String> = emptyList()
    ): DeliveryAck {
        when (val integrity = prefs.assessPairingIntegrity()) {
            is com.healthchecker.companion.secure.CompanionHostStore.PairingIntegrity.Inconsistent -> {
                SafeLog.w("delivery_blocked reason=" + integrity.reason)
                return DeliveryAck(false, integrity.reason, false, null, integrity.reason)
            }
            is com.healthchecker.companion.secure.CompanionHostStore.PairingIntegrity.Unpaired -> {
                return DeliveryAck(false, "not_paired", false, null, "not_paired")
            }
            is com.healthchecker.companion.secure.CompanionHostStore.PairingIntegrity.Paired -> Unit
        }
        // Delivery uses active host only — draft is ignored.
        val host = prefs.getHostUrl()
        val token = prefs.getDeviceToken()
        val gate = ProductionConfigGate.validateDeliveryConfig(host, token)
        if (!gate.ok) {
            return DeliveryAck(false, gate.error ?: "config_gate", false, null, gate.error)
        }
        assertTlsOrLocalDev(host!!)

        val pending = when (val load = prefs.loadPendingBatch()) {
            is SecurePrefs.PendingBatchLoad.Corrupt -> {
                SafeLog.w("pending_batch_corrupt")
                return DeliveryAck(false, "pending_batch_corrupt", false, null, "pending_batch_corrupt")
            }
            is SecurePrefs.PendingBatchLoad.Loaded -> load.batch
            is SecurePrefs.PendingBatchLoad.Empty -> PendingBatch.create(
                observations = observations,
                nextChangesToken = nextChangesToken,
                deletedRecordIds = deletedRecordIds
            ).also { prefs.setPendingBatch(it) }
        }

        val arr = JSONArray(pending.observationsJson)
        val deletions = JSONArray()
        pending.deletedRecordIds().forEach { deletions.put(it) }

        val body = JSONObject()
            .put("batch_id", pending.batchId)
            .put("nonce", pending.nonce)
            .put("sent_at", Instant.now().toString())
            .put("observations", arr)
            .put("deletions", deletions)
            .put("next_cursor", JSONObject().put("changes_token", pending.nextChangesToken ?: JSONObject.NULL))
            .put("health_connect_status", healthConnectStatus)
            .put("permissions", permissions)
            .put("workmanager", workmanager)
            .put("queued_observations", queued)

        val req = Request.Builder()
            .url(host.trimEnd('/') + "/api/companion/observations")
            .post(body.toString().toRequestBody(JSON))
            .header("Authorization", "Bearer $token")
            .header("Content-Type", "application/json")
            .applyLocalDevHeader(host)
            .build()

        return try {
            client.newCall(req).execute().use { resp ->
                val text = resp.body?.string().orEmpty()
                // Never log request/response bodies — length only.
                SafeLog.i("deliver http=" + resp.code + " bytes=" + text.length)
                val json = runCatching { JSONObject(text) }.getOrElse {
                    return DeliveryAck(false, "malformed_response", false, null, "bad_json")
                }
                val status = json.optString("status")
                val ok = json.optBoolean("ok") || status == "duplicate_ack"
                val advanced = json.optBoolean("cursor_advanced")
                val ackBatchId = json.optString("batch_id").ifBlank { null }
                val cursorObj = json.optJSONObject("cursor")
                val tokenOut = cursorObj?.optString("changes_token")
                if (PendingBatchAck.shouldClearPending(
                        pendingBatchId = pending.batchId,
                        ackOk = ok,
                        cursorAdvanced = advanced,
                        ackBatchId = ackBatchId,
                        status = status
                    )
                ) {
                    prefs.setPendingBatch(null)
                }
                DeliveryAck(
                    ok = ok,
                    status = status,
                    cursorAdvanced = advanced && ackBatchId == pending.batchId,
                    nextCursorToken = if (advanced && ackBatchId == pending.batchId) tokenOut else null,
                    error = if (ok) null else json.optJSONArray("errors")?.join(", "),
                    ackBatchId = ackBatchId
                )
            }
        } catch (t: Throwable) {
            SafeLog.e("deliver_transport_failed", t)
            DeliveryAck(false, "transport_error", false, null, t.javaClass.simpleName)
        }
    }

    private fun assertTlsOrLocalDev(host: String) {
        val gate = ProductionConfigGate.validateDeliveryConfig(
            hostUrl = host,
            deviceToken = prefs.getDeviceToken() ?: "present"
        )
        if (!gate.ok && (
                gate.error == "host_url_scheme_invalid" ||
                    gate.error == "tls_required_outside_local_dev"
                )
        ) {
            throw IllegalStateException(gate.error)
        }
    }

    private fun Request.Builder.applyLocalDevHeader(host: String): Request.Builder {
        val origin = when (val parsed = PairingInputs.normalizeOrigin(host)) {
            is PairingInputs.OriginResult.Ok -> parsed.origin
            else -> host
        }
        val httpUrl = origin.toHttpUrlOrNull()
        val localCleartext = httpUrl != null &&
            httpUrl.scheme == "http" &&
            LocalCleartextHostPolicy.isPermitted(httpUrl.host)
        if (BuildConfig.DEBUG && BuildConfig.ALLOW_CLEARTEXT_LOCAL_DEV && localCleartext) {
            header("X-HC-Local-Dev", "true")
            header("X-Forwarded-Proto", "http")
        } else if (httpUrl?.scheme == "https" || origin.startsWith("https://", ignoreCase = true)) {
            header("X-Forwarded-Proto", "https")
        }
        return this
    }

    companion object {
        private val JSON = "application/json; charset=utf-8".toMediaType()
    }
}
