package com.healthchecker.companion.host

import com.healthchecker.companion.BuildConfig
import com.healthchecker.companion.secure.SecurePrefs
import com.healthchecker.companion.util.SafeLog
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * Authenticated delivery client for HC-303A/B host endpoints.
 * TLS required unless debug local-dev cleartext is explicitly enabled.
 * Reuses persisted batch identity across retries until durable acknowledgement.
 * New Health Connect readings are not merged into a frozen pending batch; they remain
 * unread in Health Connect until the pending identity is cleared and a subsequent fetch runs.
 *
 * HC-306I-R11 timeouts:
 * Permanent-host chunk delivery can exceed 60s when per-chunk monitoring runs after
 * durable store (FINANCE R10: SocketTimeoutException after accepted 180-record chunks).
 * Timeouts are finite and retryable against the same stable chunk identity; a timeout
 * never implies rejection or non-receipt, and pending progress advances only on durable ack.
 */
class HostClient(
    private val prefs: SecurePrefs,
    private val client: OkHttpClient = OkHttpClient.Builder()
        // Overall call bound for permanent-host chunk + monitoring transactions.
        .callTimeout(CALL_TIMEOUT_SECONDS, TimeUnit.SECONDS)
        // Align read/write with call bound so default 10s OkHttp read timeout cannot
        // abort while the host is still processing a durable chunk.
        .readTimeout(CALL_TIMEOUT_SECONDS, TimeUnit.SECONDS)
        .writeTimeout(WRITE_TIMEOUT_SECONDS, TimeUnit.SECONDS)
        .connectTimeout(CONNECT_TIMEOUT_SECONDS, TimeUnit.SECONDS)
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
        batchId: String,
        nonce: String,
        observationsJson: String,
        nextChangesToken: String?,
        healthConnectStatusJson: String,
        permissionsJson: String,
        workmanagerJson: String,
        queued: Int,
        deletedRecordIdsJson: String = "[]",
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
        val body = DeliveryEnvelope.build(
            batchId = batchId,
            nonce = nonce,
            observations = JSONArray(observationsJson),
            deletedRecordIds = JSONArray(deletedRecordIdsJson),
            nextChangesToken = nextChangesToken,
            healthConnectStatus = JSONObject(healthConnectStatusJson),
            permissions = JSONObject(permissionsJson),
            workmanager = JSONObject(workmanagerJson),
            queued = queued,
            // Non-final chunks intentionally omit next_cursor so the host keeps prior cursor.
            includeNextCursor = nextChangesToken != null,
        )

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
                DeliveryAck(
                    ok = ok,
                    status = status,
                    cursorAdvanced = advanced && ackBatchId == batchId,
                    nextCursorToken = if (advanced && ackBatchId == batchId) tokenOut else null,
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

        /** Finite permanent-host delivery bound (HC-306I-R11). Not infinite. */
        const val CALL_TIMEOUT_SECONDS: Long = 180L
        const val CONNECT_TIMEOUT_SECONDS: Long = 30L
        const val WRITE_TIMEOUT_SECONDS: Long = 60L
    }
}
