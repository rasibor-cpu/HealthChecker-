package com.healthchecker.companion.secure

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import com.healthchecker.companion.sync.PendingBatch
import com.healthchecker.companion.sync.SyncMutex
import com.healthchecker.companion.util.SafeLog

/**
 * Keystore-backed secure preferences for companion credentials and cursors.
 * Never logs token values or device identifiers.
 *
 * Host URL roles:
 * - [getHostUrl] / active `host_url`: trusted paired delivery destination only
 * - [getDraftHostUrl]: user/debug edit buffer; ignored by Sync and WorkManager
 */
class SecurePrefs(context: Context) {
    private val prefs: SharedPreferences
    private val hostStore: CompanionHostStore
    val syncMutex: SyncMutex

    sealed class PendingBatchLoad {
        data object Empty : PendingBatchLoad()
        data class Loaded(val batch: PendingBatch) : PendingBatchLoad()
        data object Corrupt : PendingBatchLoad()
    }

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
        hostStore = CompanionHostStore(prefs)
        syncMutex = SyncMutex(prefs)
    }

    /** Active paired delivery host only. Never returns the draft. */
    fun getHostUrl(): String? = hostStore.getActiveHostUrl()

    fun getDraftHostUrl(): String? = hostStore.getDraftHostUrl()

    fun displayHostForEditing(): String = hostStore.displayHostForEditing()

    fun setDraftHostUrl(url: String?) = hostStore.setDraftHostUrl(url)

    fun getDeviceId(): String? = hostStore.getDeviceId()

    fun getDeviceToken(): String? = hostStore.getDeviceToken()

    fun assessPairingIntegrity(): CompanionHostStore.PairingIntegrity = hostStore.assessPairingIntegrity()

    /**
     * Atomic successful-pair write. Prefer this over separate host/token updates.
     * @return false if the encrypted commit failed (fail closed — prior state unchanged).
     */
    fun commitPairedSession(activeHost: String, deviceId: String, token: String): Boolean =
        hostStore.commitPairedSession(activeHost, deviceId, token)

    fun clearPairing() = hostStore.clearPairingCredentials()

    /**
     * Clear every user-bound companion artifact after server-confirmed logout or
     * revocation. A subsequent account must pair as a new identity and cannot
     * inherit a prior user's cursor or queued Health Connect delivery.
     */
    fun clearUserScopedState() {
        hostStore.clearPairingCredentials()
        prefs.edit()
            .remove(KEY_CHANGES)
            .remove(KEY_CHANGES_SCOPE)
            .remove(KEY_PENDING_BATCH)
            .remove(KEY_LAST_ATTEMPT)
            .remove(KEY_LAST_SUCCESS)
            .remove(KEY_LAST_ERROR)
            .remove(KEY_PARTIAL_WARNING)
            .remove(KEY_LAST_QUERY_PERFORMED)
            .remove(KEY_QUEUED)
            .commit()
    }

    fun getChangesToken(): String? = prefs.getString(KEY_CHANGES, null)

    fun getChangesTokenScope(): String? = prefs.getString(KEY_CHANGES_SCOPE, null)

    /** Atomic persist of changes token + scope after durable host ack. */
    fun persistChangesCursor(token: String, scope: String): Boolean {
        if (token.isBlank() || scope.isBlank()) return false
        return prefs.edit()
            .putString(KEY_CHANGES, token)
            .putString(KEY_CHANGES_SCOPE, scope)
            .commit()
    }

    fun clearChangesCursor() {
        prefs.edit()
            .remove(KEY_CHANGES)
            .remove(KEY_CHANGES_SCOPE)
            .commit()
    }

    @Deprecated("Use persistChangesCursor after durable ack", ReplaceWith("persistChangesCursor(token, scope)"))
    fun setChangesToken(token: String) {
        // Fail closed: never persist a token without an accompanying scope.
        if (token.isBlank()) {
            clearChangesCursor()
        }
    }

    fun setLastAttempt(iso: String) = prefs.edit().putString(KEY_LAST_ATTEMPT, iso).apply()
    fun getLastAttempt(): String? = prefs.getString(KEY_LAST_ATTEMPT, null)
    fun setLastSuccess(iso: String) = prefs.edit().putString(KEY_LAST_SUCCESS, iso).apply()
    fun getLastSuccess(): String? = prefs.getString(KEY_LAST_SUCCESS, null)
    fun setLastError(msg: String?) = prefs.edit().putString(KEY_LAST_ERROR, msg).apply()
    fun getLastError(): String? = prefs.getString(KEY_LAST_ERROR, null)
    fun setPartialPermissionWarning(active: Boolean) =
        prefs.edit().putBoolean(KEY_PARTIAL_WARNING, active).apply()
    fun getPartialPermissionWarning(): Boolean = prefs.getBoolean(KEY_PARTIAL_WARNING, false)
    fun setLastQueryPerformed(performed: Boolean) =
        prefs.edit().putBoolean(KEY_LAST_QUERY_PERFORMED, performed).apply()
    fun getLastQueryPerformed(): Boolean = prefs.getBoolean(KEY_LAST_QUERY_PERFORMED, false)
    fun setQueuedCount(n: Int) = prefs.edit().putInt(KEY_QUEUED, n).apply()
    fun getQueuedCount(): Int = prefs.getInt(KEY_QUEUED, 0)

    /** Prefer [loadPendingBatch] — corruption is surfaced rather than silently discarded. */
    fun getPendingBatch(): PendingBatch? {
        return when (val load = loadPendingBatch()) {
            is PendingBatchLoad.Loaded -> load.batch
            else -> null
        }
    }

    fun loadPendingBatch(): PendingBatchLoad {
        val raw = prefs.getString(KEY_PENDING_BATCH, null) ?: return PendingBatchLoad.Empty
        if (raw.isBlank()) return PendingBatchLoad.Empty
        val parsed = PendingBatch.fromJson(raw)
        return if (parsed == null) PendingBatchLoad.Corrupt else PendingBatchLoad.Loaded(parsed)
    }

    fun setPendingBatch(batch: PendingBatch?) {
        val editor = prefs.edit()
        if (batch == null) editor.remove(KEY_PENDING_BATCH) else editor.putString(KEY_PENDING_BATCH, batch.toJson())
        editor.commit()
    }

    companion object {
        private const val KEY_CHANGES = "hc_changes_token"
        private const val KEY_CHANGES_SCOPE = "hc_changes_token_scope"
        private const val KEY_LAST_ATTEMPT = "last_attempt_at"
        private const val KEY_LAST_SUCCESS = "last_success_at"
        private const val KEY_LAST_ERROR = "last_error"
        private const val KEY_PARTIAL_WARNING = "partial_permission_warning"
        private const val KEY_LAST_QUERY_PERFORMED = "last_query_performed"
        private const val KEY_QUEUED = "queued_count"
        private const val KEY_PENDING_BATCH = "pending_batch_json"
    }
}
