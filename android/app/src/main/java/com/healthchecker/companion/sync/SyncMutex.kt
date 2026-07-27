package com.healthchecker.companion.sync

import android.content.SharedPreferences
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Cross-entrypoint sync lease for HC-303B.
 * Prevents overlapping manual sync and WorkManager sync.
 *
 * LIMITATION (documented): exclusion across abrupt process death is lease-based, not absolute.
 * A persisted lease older than [STALE_MS] may be stolen by another owner after process death.
 * Wall-clock jumps forward can make a live lease appear stale; backward clock skew is treated
 * as still-held (negative age) so a lease cannot be trivially stolen by setting the clock back.
 */
class SyncMutex(
    private val prefs: SharedPreferences,
    private val clockMs: () -> Long = { System.currentTimeMillis() }
) {
    data class AcquireResult(val acquired: Boolean, val reason: String)

    private val localHeld = AtomicBoolean(false)

    fun tryAcquire(owner: String): AcquireResult {
        if (!localHeld.compareAndSet(false, true)) {
            return AcquireResult(false, "sync_already_running_local")
        }
        val now = clockMs()
        val held = prefs.getBoolean(KEY_HELD, false)
        val heldAt = prefs.getLong(KEY_HELD_AT, 0L)
        val heldOwner = prefs.getString(KEY_OWNER, null)
        if (held) {
            val age = now - heldAt
            // Negative age => clock moved backward; treat as still held (do not steal).
            // Fresh positive age under STALE_MS blocks other owners.
            val stillHeld = age < STALE_MS
            if (stillHeld && heldOwner != owner) {
                localHeld.set(false)
                return AcquireResult(false, "sync_already_running")
            }
            // Same owner may renew; stale foreign lease may be stolen after process death.
        }
        prefs.edit()
            .putBoolean(KEY_HELD, true)
            .putLong(KEY_HELD_AT, now)
            .putString(KEY_OWNER, owner)
            .commit()
        return AcquireResult(true, "acquired")
    }

    fun release(owner: String) {
        val current = prefs.getString(KEY_OWNER, null)
        if (current == null || current == owner) {
            prefs.edit()
                .putBoolean(KEY_HELD, false)
                .remove(KEY_OWNER)
                .putLong(KEY_HELD_AT, 0L)
                .commit()
        }
        localHeld.set(false)
    }

    fun isHeld(): Boolean {
        if (localHeld.get()) return true
        if (!prefs.getBoolean(KEY_HELD, false)) return false
        val age = clockMs() - prefs.getLong(KEY_HELD_AT, 0L)
        // Negative age (clock skew) still counts as held.
        return age < STALE_MS
    }

    companion object {
        const val STALE_MS: Long = 15 * 60 * 1000L
        private const val KEY_HELD = "sync_mutex_held"
        private const val KEY_HELD_AT = "sync_mutex_held_at"
        private const val KEY_OWNER = "sync_mutex_owner"
    }
}
