package com.healthchecker.companion.secure

import android.content.SharedPreferences
import com.healthchecker.companion.util.SafeLog

/**
 * Draft vs active host separation and atomic pairing writes over a SharedPreferences backend.
 * Active [KEY_HOST] is the only delivery destination. Draft never feeds Sync/WorkManager.
 */
class CompanionHostStore(private val prefs: SharedPreferences) {

    sealed class PairingIntegrity {
        data object Unpaired : PairingIntegrity()
        data class Paired(val activeHost: String, val deviceId: String, val hasToken: Boolean) : PairingIntegrity()
        data class Inconsistent(val reason: String) : PairingIntegrity()
    }

    fun getActiveHostUrl(): String? = prefs.getString(KEY_HOST, null)?.trim()?.takeIf { it.isNotEmpty() }

    /** Trusted consumer origin retained across logout so the next account can sign in. */
    fun getConsumerOrigin(): String? =
        prefs.getString(KEY_CONSUMER_ORIGIN, null)?.trim()?.takeIf { it.isNotEmpty() }

    fun getDraftHostUrl(): String? = prefs.getString(KEY_DRAFT_HOST, null)?.trim()?.takeIf { it.isNotEmpty() }

    /** UI editing value: draft if present, otherwise active (display only — not trusted for delivery). */
    fun displayHostForEditing(): String = getDraftHostUrl() ?: getActiveHostUrl().orEmpty()

    fun setDraftHostUrl(url: String?) {
        val editor = prefs.edit()
        val cleaned = url?.trim()?.trimEnd('/')?.takeIf { it.isNotEmpty() }
        if (cleaned == null) editor.remove(KEY_DRAFT_HOST) else editor.putString(KEY_DRAFT_HOST, cleaned)
        editor.apply()
    }

    fun getDeviceId(): String? = prefs.getString(KEY_DEVICE_ID, null)?.takeIf { it.isNotEmpty() }

    fun getDeviceToken(): String? = prefs.getString(KEY_TOKEN, null)?.takeIf { it.isNotEmpty() }

    /**
     * Atomically persist successful pairing: active host + device id + token.
     * Clears draft. Uses commit() so a failed write does not leave partial state applied.
     * Never logs token, device id, or host values.
     */
    fun commitPairedSession(activeHost: String, deviceId: String, token: String): Boolean {
        val host = activeHost.trim().trimEnd('/')
        val id = deviceId.trim()
        val tok = token.trim()
        if (host.isEmpty() || id.isEmpty() || tok.isEmpty()) {
            SafeLog.w("pairing_commit_rejected_incomplete")
            return false
        }
        val ok = prefs.edit()
            .putString(KEY_HOST, host)
            .putString(KEY_CONSUMER_ORIGIN, host)
            .putString(KEY_DEVICE_ID, id)
            .putString(KEY_TOKEN, tok)
            .remove(KEY_DRAFT_HOST)
            .commit()
        if (ok) {
            SafeLog.i("pairing_saved")
        } else {
            SafeLog.w("pairing_commit_failed")
        }
        return ok
    }

    fun clearPairingCredentials() {
        prefs.edit()
            .remove(KEY_HOST)
            .remove(KEY_DRAFT_HOST)
            .remove(KEY_DEVICE_ID)
            .remove(KEY_TOKEN)
            .commit()
        SafeLog.i("pairing_cleared")
    }

    /**
     * Legacy-safe integrity:
     * - No host and no token → unpaired
     * - Host + token → paired (existing installs remain usable; host_url is active)
     * - Host without token, or token without host → fail closed (do not treat draft as active)
     */
    fun assessPairingIntegrity(): PairingIntegrity {
        val host = getActiveHostUrl()
        val token = getDeviceToken()
        val deviceId = getDeviceId()
        val hasHost = !host.isNullOrBlank()
        val hasToken = !token.isNullOrBlank()
        return when {
            !hasHost && !hasToken -> PairingIntegrity.Unpaired
            hasHost && hasToken -> PairingIntegrity.Paired(
                activeHost = host!!,
                deviceId = deviceId.orEmpty(),
                hasToken = true,
            )
            else -> PairingIntegrity.Inconsistent("pairing_state_inconsistent")
        }
    }

    companion object {
        const val KEY_HOST = "host_url"
        const val KEY_CONSUMER_ORIGIN = "consumer_origin"
        const val KEY_DRAFT_HOST = "draft_host_url"
        const val KEY_DEVICE_ID = "device_id"
        const val KEY_TOKEN = "device_token"
    }
}
