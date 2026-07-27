package com.healthchecker.companion.host

import com.healthchecker.companion.BuildConfig
import com.healthchecker.companion.healthconnect.CompanionObservation
import com.healthchecker.companion.secure.SecurePrefs
import com.healthchecker.companion.util.SafeLog
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.util.UUID
import java.util.concurrent.TimeUnit

/**
 * Authenticated delivery client for HC-303A host endpoints.
 * TLS required unless debug local-dev cleartext is explicitly enabled.
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
        val error: String?
    )

    fun confirmPairing(hostUrl: String, pairCode: String, deviceLabel: String): String? {
        val url = hostUrl.trimEnd('/') + "/api/companion/pair/confirm"
        val body = JSONObject()
            .put("pair_code", pairCode)
            .put("device_label", deviceLabel)
            .put("platform", "android")
            .put("app_version", BuildConfig.VERSION_NAME)
        val req = Request.Builder()
            .url(url)
            .post(body.toString().toRequestBody(JSON))
            .header("Content-Type", "application/json")
            .applyLocalDevHeader(hostUrl)
            .build()
        client.newCall(req).execute().use { resp ->
            val text = resp.body?.string().orEmpty()
            SafeLog.i("pair_confirm http=" + resp.code)
            val json = JSONObject(text)
            if (!json.optBoolean("ok")) return json.optJSONArray("errors")?.join(", ") ?: "pair_failed"
            val deviceId = json.getString("device_id")
            val token = json.getString("device_token")
            prefs.setHostUrl(hostUrl)
            prefs.setPairing(deviceId, token)
            return null
        }
    }

    fun deliver(
        observations: List<CompanionObservation>,
        nextChangesToken: String?,
        healthConnectStatus: JSONObject,
        permissions: JSONObject,
        workmanager: JSONObject,
        queued: Int
    ): DeliveryAck {
        val host = prefs.getHostUrl() ?: return DeliveryAck(false, "no_host", false, null, "host_url_missing")
        val token = prefs.getDeviceToken() ?: return DeliveryAck(false, "unauthorized", false, null, "not_paired")
        assertTlsOrLocalDev(host)

        val batchId = UUID.randomUUID().toString()
        val nonce = UUID.randomUUID().toString()
        val arr = JSONArray()
        observations.forEach { obs ->
            val o = JSONObject()
            obs.toMap().forEach { (k, v) -> o.put(k, v ?: JSONObject.NULL) }
            arr.put(o)
        }
        val body = JSONObject()
            .put("batch_id", batchId)
            .put("nonce", nonce)
            .put("sent_at", Instant.now().toString())
            .put("observations", arr)
            .put("next_cursor", JSONObject().put("changes_token", nextChangesToken ?: JSONObject.NULL))
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

        client.newCall(req).execute().use { resp ->
            val text = resp.body?.string().orEmpty()
            SafeLog.i("deliver http=" + resp.code + " bytes=" + text.length)
            val json = runCatching { JSONObject(text) }.getOrElse {
                return DeliveryAck(false, "malformed_response", false, null, "bad_json")
            }
            val ok = json.optBoolean("ok") || json.optString("status") == "duplicate_ack"
            val advanced = json.optBoolean("cursor_advanced")
            val cursorObj = json.optJSONObject("cursor")
            val tokenOut = cursorObj?.optString("changes_token")
            return DeliveryAck(
                ok = ok,
                status = json.optString("status"),
                cursorAdvanced = advanced,
                nextCursorToken = if (advanced) tokenOut else null,
                error = if (ok) null else json.optJSONArray("errors")?.join(", ")
            )
        }
    }

    private fun assertTlsOrLocalDev(host: String) {
        val isHttps = host.startsWith("https://", ignoreCase = true)
        val isLocalHttp = host.startsWith("http://127.") || host.startsWith("http://10.") ||
            host.startsWith("http://192.168.") || host.startsWith("http://localhost")
        if (!isHttps) {
            if (!(BuildConfig.DEBUG && BuildConfig.ALLOW_CLEARTEXT_LOCAL_DEV && isLocalHttp)) {
                throw IllegalStateException("tls_required_outside_local_dev")
            }
        }
    }

    private fun Request.Builder.applyLocalDevHeader(host: String): Request.Builder {
        val isLocalHttp = host.startsWith("http://127.") || host.startsWith("http://10.") ||
            host.startsWith("http://192.168.") || host.startsWith("http://localhost")
        if (BuildConfig.DEBUG && BuildConfig.ALLOW_CLEARTEXT_LOCAL_DEV && isLocalHttp) {
            header("X-HC-Local-Dev", "true")
            header("X-Forwarded-Proto", "http")
        } else if (host.startsWith("https://")) {
            header("X-Forwarded-Proto", "https")
        }
        return this
    }

    companion object {
        private val JSON = "application/json; charset=utf-8".toMediaType()
    }
}
