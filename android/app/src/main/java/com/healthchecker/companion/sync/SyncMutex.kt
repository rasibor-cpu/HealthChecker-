package com.healthchecker.companion.sync

import android.content.SharedPreferences
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Cross-entrypoint sync lease for HC-303B.
 * Prevents overlapping manual sync and WorkManager sync.
 *
 * In-process exclusion is process-wide across every SyncMutex/SecurePrefs instance.
 * The persisted lease remains the crash-recovery guard across abrupt process death.
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

    /*
     * Per-instance guard preserves the existing local double-acquire contract.
     * The companion-object process gate closes the race between separately
     * constructed SecurePrefs/SyncMutex instances in the same Android process.
     */
    private val localHeld = AtomicBoolean(false)

    fun tryAcquire(owner: String): AcquireResult {
        if (!localHeld.compareAndSet(false, true)) {
            return AcquireResult(false, "sync_already_running_local")
        }

        val now = clockMs()

        /*
         * Atomic within this Android process. This critical section covers
         * ownership of the process gate before the SharedPreferences lease
         * is inspected or written, so two SyncMutex instances cannot both
         * observe and claim an apparently free persisted lease.
         *
         * A stale process gate may be stolen. That is primarily a JVM-test
         * seam mirroring persisted stale-lease recovery; in a real process
         * death the companion-object state disappears with the process.
         */
        synchronized(PROCESS_GATE_LOCK) {
            val currentProcessOwner = processOwner
            if (currentProcessOwner != null) {
                val processAge = now - processHeldAt
                if (processAge < STALE_MS) {
                    localHeld.set(false)
                    return AcquireResult(false, "sync_already_running")
                }
            }

            processOwner = owner
            processHeldAt = now
        }

        val held = prefs.getBoolean(KEY_HELD, false)
        val heldAt = prefs.getLong(KEY_HELD_AT, 0L)
        val heldOwner = prefs.getString(KEY_OWNER, null)

        if (held) {
            val age = now - heldAt

            // Negative age => clock moved backward; treat as still held (do not steal).
            // Fresh positive age under STALE_MS blocks other owners.
            val stillHeld = age < STALE_MS

            if (stillHeld && heldOwner != owner) {
                clearProcessGateIfOwned(owner)
                localHeld.set(false)
                return AcquireResult(false, "sync_already_running")
            }

            /*
             * Same persisted owner may renew after process reconstruction.
             * A stale foreign persisted lease may be stolen after process death.
             */
        }

        val committed = prefs.edit()
            .putBoolean(KEY_HELD, true)
            .putLong(KEY_HELD_AT, now)
            .putString(KEY_OWNER, owner)
            .commit()

        if (!committed) {
            clearProcessGateIfOwned(owner)
            localHeld.set(false)
            return AcquireResult(false, "sync_mutex_persist_failed")
        }

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

        /*
         * Wrong-owner release must not release another caller's process gate.
         */
        clearProcessGateIfOwned(owner)
        localHeld.set(false)
    }

    fun isHeld(): Boolean {
        val now = clockMs()

        synchronized(PROCESS_GATE_LOCK) {
            if (processOwner != null) {
                val age = now - processHeldAt

                // Negative clock skew still counts as held.
                if (age < STALE_MS) {
                    return true
                }
            }
        }

        if (!prefs.getBoolean(KEY_HELD, false)) return false

        val age = now - prefs.getLong(KEY_HELD_AT, 0L)

        // Negative age (clock skew) still counts as held.
        return age < STALE_MS
    }

    private fun clearProcessGateIfOwned(owner: String) {
        synchronized(PROCESS_GATE_LOCK) {
            if (processOwner == owner) {
                processOwner = null
                processHeldAt = 0L
            }
        }
    }

    companion object {
        const val STALE_MS: Long = 15 * 60 * 1000L

        private const val KEY_HELD = "sync_mutex_held"
        private const val KEY_HELD_AT = "sync_mutex_held_at"
        private const val KEY_OWNER = "sync_mutex_owner"

        /*
         * Shared by every SyncMutex instance in this JVM process.
         * Manual sync and MonitoringSyncWorker therefore compete on the
         * same atomic in-process gate before touching the persisted lease.
         */
        private val PROCESS_GATE_LOCK = Any()

        @Volatile
        private var processOwner: String? = null

        @Volatile
        private var processHeldAt: Long = 0L
    }
}
