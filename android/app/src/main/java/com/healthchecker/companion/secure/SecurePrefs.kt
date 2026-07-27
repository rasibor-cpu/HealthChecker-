package com.healthchecker.companion.secure

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import com.healthchecker.companion.util.SafeLog

/**
 * Keystore-backed secure preferences for companion credentials and cursors.
 * Never logs token values.
 */
class SecurePrefs(context: Context) {
    private val prefs: SharedPreferences

    init {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        prefs = EncryptedSharedPreferences.create(
            context,
            "hc_companion_secure",
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
        )
    }

    fun getHostUrl(): String? = prefs.getString(KEY_HOST, null)
    fun setHostUrl(url: String) = prefs.edit().putString(KEY_HOST, url.trimEnd('/')).apply()

    fun getDeviceId(): String? = prefs.getString(KEY_DEVICE_ID, null)
    fun getDeviceToken(): String? = prefs.getString(KEY_TOKEN, null)

    fun setPairing(deviceId: String, token: String) {
        prefs.edit()
            .putString(KEY_DEVICE_ID, deviceId)
            .putString(KEY_TOKEN, token)
            .apply()
        SafeLog.i("pairing_saved device_id_prefix=" + deviceId.take(8))
    }

    fun clearPairing() {
        prefs.edit().remove(KEY_DEVICE_ID).remove(KEY_TOKEN).apply()
        SafeLog.i("pairing_cleared")
    }

    fun getChangesToken(): String? = prefs.getString(KEY_CHANGES, null)
    fun setChangesToken(token: String) = prefs.edit().putString(KEY_CHANGES, token).apply()

    fun setLastAttempt(iso: String) = prefs.edit().putString(KEY_LAST_ATTEMPT, iso).apply()
    fun getLastAttempt(): String? = prefs.getString(KEY_LAST_ATTEMPT, null)
    fun setLastSuccess(iso: String) = prefs.edit().putString(KEY_LAST_SUCCESS, iso).apply()
    fun getLastSuccess(): String? = prefs.getString(KEY_LAST_SUCCESS, null)
    fun setLastError(msg: String?) = prefs.edit().putString(KEY_LAST_ERROR, msg).apply()
    fun getLastError(): String? = prefs.getString(KEY_LAST_ERROR, null)
    fun setQueuedCount(n: Int) = prefs.edit().putInt(KEY_QUEUED, n).apply()
    fun getQueuedCount(): Int = prefs.getInt(KEY_QUEUED, 0)

    companion object {
        private const val KEY_HOST = "host_url"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_TOKEN = "device_token"
        private const val KEY_CHANGES = "hc_changes_token"
        private const val KEY_LAST_ATTEMPT = "last_attempt_at"
        private const val KEY_LAST_SUCCESS = "last_success_at"
        private const val KEY_LAST_ERROR = "last_error"
        private const val KEY_QUEUED = "queued_count"
    }
}
