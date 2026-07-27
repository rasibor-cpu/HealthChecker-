package com.healthchecker.companion.sync

import android.content.SharedPreferences
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28], manifest = Config.NONE)
class SyncMutexTest {
    @Test
    fun preventsOverlapAndAllowsAfterRelease() {
        val prefs: SharedPreferences =
            RuntimeEnvironment.getApplication().getSharedPreferences("mutex_test", 0)
        prefs.edit().clear().commit()
        val mutex = SyncMutex(prefs) { 1_000L }
        val a = mutex.tryAcquire("manual")
        assertTrue(a.acquired)
        val b = mutex.tryAcquire("workmanager")
        assertFalse(b.acquired)
        assertEquals("sync_already_running_local", b.reason)
        mutex.release("manual")
        val c = mutex.tryAcquire("workmanager")
        assertTrue(c.acquired)
        mutex.release("workmanager")
    }

    @Test
    fun concurrentManualWorkerStartBlocksLoser() {
        val prefs: SharedPreferences =
            RuntimeEnvironment.getApplication().getSharedPreferences("mutex_test2", 0)
        prefs.edit().clear().commit()
        val clock = longArrayOf(1_000L)
        val first = SyncMutex(prefs) { clock[0] }
        assertTrue(first.tryAcquire("manual").acquired)
        // Simulate another process instance (fresh AtomicBoolean) while lease is fresh
        val second = SyncMutex(prefs) { clock[0] + 1_000L }
        val blocked = second.tryAcquire("workmanager")
        assertFalse(blocked.acquired)
        assertEquals("sync_already_running", blocked.reason)
        first.release("manual")
    }

    @Test
    fun prematureLeaseStealRejected() {
        val prefs: SharedPreferences =
            RuntimeEnvironment.getApplication().getSharedPreferences("mutex_steal", 0)
        prefs.edit().clear().commit()
        val clock = longArrayOf(10_000L)
        val holder = SyncMutex(prefs) { clock[0] }
        assertTrue(holder.tryAcquire("manual").acquired)
        // Advance less than STALE_MS — steal must fail
        clock[0] = 10_000L + SyncMutex.STALE_MS - 1
        val stealer = SyncMutex(prefs) { clock[0] }
        val denied = stealer.tryAcquire("workmanager")
        assertFalse(denied.acquired)
        assertEquals("sync_already_running", denied.reason)
        holder.release("manual")
    }

    @Test
    fun staleLeaseRecoversAfterProcessDeath() {
        val prefs: SharedPreferences =
            RuntimeEnvironment.getApplication().getSharedPreferences("mutex_stale", 0)
        prefs.edit().clear().commit()
        val clock = longArrayOf(1_000L)
        val dead = SyncMutex(prefs) { clock[0] }
        assertTrue(dead.tryAcquire("workmanager").acquired)
        // Process death: drop in-memory lock; lease remains in prefs past STALE_MS
        clock[0] = 1_000L + SyncMutex.STALE_MS + 1
        val survivor = SyncMutex(prefs) { clock[0] }
        val recovered = survivor.tryAcquire("manual")
        assertTrue(recovered.acquired)
        survivor.release("manual")
    }

    @Test
    fun releaseOnlyClearsCurrentOwner() {
        val prefs: SharedPreferences =
            RuntimeEnvironment.getApplication().getSharedPreferences("mutex_owner", 0)
        prefs.edit().clear().commit()
        val clock = longArrayOf(5_000L)
        val m = SyncMutex(prefs) { clock[0] }
        assertTrue(m.tryAcquire("manual").acquired)
        m.release("workmanager") // wrong owner — must not clear
        assertTrue(m.isHeld())
        m.release("manual")
        assertFalse(m.isHeld())
    }

    @Test
    fun exceptionPathReleaseViaFinallySemantics() {
        val prefs: SharedPreferences =
            RuntimeEnvironment.getApplication().getSharedPreferences("mutex_finally", 0)
        prefs.edit().clear().commit()
        val m = SyncMutex(prefs) { 100L }
        assertTrue(m.tryAcquire("manual").acquired)
        try {
            throw RuntimeException("simulated")
        } catch (_: RuntimeException) {
            // caller finally
        } finally {
            m.release("manual")
        }
        assertFalse(m.isHeld())
        assertTrue(m.tryAcquire("workmanager").acquired)
        m.release("workmanager")
    }

    @Test
    fun backwardClockSkewDoesNotAllowSteal() {
        val prefs: SharedPreferences =
            RuntimeEnvironment.getApplication().getSharedPreferences("mutex_clock", 0)
        prefs.edit().clear().commit()
        val clock = longArrayOf(100_000L)
        val holder = SyncMutex(prefs) { clock[0] }
        assertTrue(holder.tryAcquire("manual").acquired)
        clock[0] = 50_000L // wall clock moved backward
        val stealer = SyncMutex(prefs) { clock[0] }
        val denied = stealer.tryAcquire("workmanager")
        assertFalse(denied.acquired)
        assertEquals("sync_already_running", denied.reason)
        holder.release("manual")
    }
}
