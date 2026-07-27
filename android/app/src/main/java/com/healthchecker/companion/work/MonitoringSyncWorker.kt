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
import com.healthchecker.companion.healthconnect.HealthConnectCapability
import com.healthchecker.companion.healthconnect.HealthConnectAvailability
import com.healthchecker.companion.healthconnect.HealthConnectReader
import com.healthchecker.companion.host.HostClient
import com.healthchecker.companion.secure.SecurePrefs
import com.healthchecker.companion.util.SafeLog
import org.json.JSONObject
import java.time.Instant
import java.util.concurrent.TimeUnit

/**
 * Unique periodic WorkManager sync.
 * Bounded exponential backoff. Does not claim exact or uninterrupted execution.
 * Android enforces a minimum periodic interval of 15 minutes.
 */
class MonitoringSyncWorker(
    appContext: Context,
    params: WorkerParameters
) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val prefs = SecurePrefs(applicationContext)
        val now = Instant.now().toString()
        prefs.setLastAttempt(now)
        return try {
            val capability = HealthConnectCapability(applicationContext).report()
            if (capability.availability != HealthConnectAvailability.READY) {
                prefs.setLastError(capability.message)
                return Result.success() // not a retryable worker crash
            }
            if (capability.permissionsMissing.isNotEmpty()) {
                prefs.setLastError("permission_required")
                return Result.success()
            }

            val reader = HealthConnectReader(applicationContext, prefs)
            val fetch = reader.fetchNew()
            prefs.setQueuedCount(fetch.observations.size)
            if (fetch.permissionRequired) {
                prefs.setLastError("permission_required")
                return Result.success()
            }
            if (fetch.error != null && fetch.observations.isEmpty()) {
                prefs.setLastError(fetch.error)
                return Result.retry()
            }

            val host = HostClient(prefs)
            val ack = host.deliver(
                observations = fetch.observations,
                nextChangesToken = fetch.nextChangesToken,
                healthConnectStatus = JSONObject()
                    .put("availability", capability.availability.name)
                    .put("message", capability.message),
                permissions = JSONObject()
                    .put("granted_count", capability.permissionsGranted.size)
                    .put("missing_count", capability.permissionsMissing.size),
                workmanager = JSONObject()
                    .put("unique_name", UNIQUE_NAME)
                    .put("overlap_prevented", true)
                    .put("exact_timing_guaranteed", false),
                queued = fetch.observations.size
            )
            if (ack.ok && ack.cursorAdvanced) {
                reader.acknowledgeCursor(ack.nextCursorToken ?: fetch.nextChangesToken)
                prefs.setLastSuccess(Instant.now().toString())
                prefs.setLastError(null)
                prefs.setQueuedCount(0)
                Result.success()
            } else if (ack.status == "unauthorized" || ack.status == "revoked") {
                // Permanent auth failures must not retry forever
                prefs.setLastError(ack.status)
                Result.failure()
            } else if (ack.ok) {
                // Accepted but cursor not advanced (partial) — retry later without claiming success cursor
                prefs.setLastError(ack.error ?: "partial_no_cursor")
                Result.retry()
            } else {
                prefs.setLastError(ack.error ?: ack.status)
                Result.retry()
            }
        } catch (t: Throwable) {
            SafeLog.e("sync_worker_failed", t)
            prefs.setLastError(t.javaClass.simpleName)
            Result.retry()
        }
    }

    companion object {
        const val UNIQUE_NAME = "hc303a_monitoring_sync"

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
