package com.healthchecker.companion.work

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.healthchecker.companion.healthconnect.BackgroundReadPolicy
import com.healthchecker.companion.healthconnect.HealthConnectCapability
import com.healthchecker.companion.secure.SecurePrefs
import com.healthchecker.companion.sync.CompanionSyncRunner
import com.healthchecker.companion.util.SafeLog
import kotlinx.coroutines.CancellationException
import java.time.Instant
import java.util.concurrent.TimeUnit

/**
 * Unique periodic WorkManager sync.
 * Bounded exponential backoff. Does not claim exact or uninterrupted execution.
 * Android enforces a minimum periodic interval of 15 minutes.
 * Shares SyncMutex with manual sync to prevent overlap.
 *
 * HC-306I-R3: mirrors manual sync disposition — never delivers fatal non-queries.
 */
class MonitoringSyncWorker(
    appContext: Context,
    params: WorkerParameters
) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val prefs = SecurePrefs(applicationContext)
        val now = Instant.now().toString()
        prefs.setLastAttempt(now)

        val capability = try {
            HealthConnectCapability(
                applicationContext
            ).report()
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (t: Throwable) {
            SafeLog.e("background_capability_check_failed", t)
            prefs.setLastError(
                "background_capability_check_failed"
            )
            prefs.setLastQueryPerformed(false)
            return Result.success()
        }

        when (
            BackgroundReadPolicy.scheduleDecision(
                featureAvailable =
                    capability.backgroundReadFeatureAvailable,
                backgroundPermissionGranted =
                    capability.backgroundReadPermissionGranted
            )
        ) {
            BackgroundReadPolicy.ScheduleDecision.FEATURE_UNAVAILABLE -> {
                prefs.setLastError(
                    "background_read_feature_unavailable"
                )
                prefs.setLastQueryPerformed(false)
                return Result.success()
            }
            BackgroundReadPolicy.ScheduleDecision.PERMISSION_REQUIRED -> {
                prefs.setLastError(
                    "background_permission_required"
                )
                prefs.setLastQueryPerformed(false)
                return Result.success()
            }
            BackgroundReadPolicy.ScheduleDecision.READY -> Unit
        }

        val lease = prefs.syncMutex.tryAcquire(OWNER)
        if (!lease.acquired) {
            prefs.setLastError(lease.reason)
            prefs.setLastQueryPerformed(false)
            SafeLog.i("worker_skip reason=" + lease.reason)
            return Result.success() // honest skip — not a crash; do not claim sync success timestamp
        }
        return try {
            when (CompanionSyncRunner(applicationContext, prefs).runOnce(OWNER)) {
                CompanionSyncRunner.Outcome.SUCCESS -> Result.success()
                CompanionSyncRunner.Outcome.RETRY -> Result.retry()
                CompanionSyncRunner.Outcome.FAILURE -> Result.failure()
            }
        } catch (t: Throwable) {
            SafeLog.e("sync_worker_failed", t)
            prefs.setLastError(t.javaClass.simpleName)
            prefs.setLastQueryPerformed(false)
            Result.retry()
        } finally {
            prefs.syncMutex.release(OWNER)
        }
    }

    companion object {
        const val UNIQUE_NAME = "hc303a_monitoring_sync"
        const val OWNER = "workmanager"

        fun schedule(context: Context) {
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .setRequiresBatteryNotLow(true)
                .build()
            val request = PeriodicWorkRequestBuilder<MonitoringSyncWorker>(15, TimeUnit.MINUTES)
                .setConstraints(constraints)
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                UNIQUE_NAME,
                ExistingPeriodicWorkPolicy.KEEP,
                request
            )
            SafeLog.i("workmanager_scheduled unique=" + UNIQUE_NAME)
        }
    }
}
