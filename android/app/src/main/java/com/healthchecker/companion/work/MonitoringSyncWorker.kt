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
 * Shares SyncMutex with manual sync to prevent overlap.
 */
class MonitoringSyncWorker(
    appContext: Context,
    params: WorkerParameters
) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val prefs = SecurePrefs(applicationContext)
        val now = Instant.now().toString()
        prefs.setLastAttempt(now)
        val lease = prefs.syncMutex.tryAcquire(OWNER)
        if (!lease.acquired) {
            prefs.setLastError(lease.reason)
            SafeLog.i("worker_skip reason=" + lease.reason)
            return Result.success() // honest skip — not a crash; do not claim sync success timestamp
        }
        return try {
            val capability = HealthConnectCapability(applicationContext).report()
            if (capability.availability != HealthConnectAvailability.READY) {
                prefs.setLastError(capability.message)
                return Result.success()
            }
            if (capability.permissionsMissing.isNotEmpty()) {
                prefs.setLastError("permission_required")
                return Result.success()
            }

            val reader = HealthConnectReader(applicationContext, prefs)
            val pendingLoad = prefs.loadPendingBatch()
            if (pendingLoad is SecurePrefs.PendingBatchLoad.Corrupt) {
                prefs.setLastError("pending_batch_corrupt")
                return Result.failure()
            }
            val pending = (pendingLoad as? SecurePrefs.PendingBatchLoad.Loaded)?.batch
            // Frozen pending identity is reused unchanged; new HC readings stay in HC until after ack.
            val fetch = if (pending != null) {
                HealthConnectReader.FetchResult(
                    observations = pending.observations(),
                    nextChangesToken = pending.nextChangesToken,
                    deletedRecordIds = pending.deletedRecordIds()
                )
            } else {
                reader.fetchNew()
            }
            prefs.setQueuedCount(fetch.observations.size)
            if (fetch.permissionRequired) {
                prefs.setLastError("permission_required")
                return Result.success()
            }
            if (fetch.error != null && fetch.observations.isEmpty() && fetch.deletedRecordIds.isEmpty() && pending == null) {
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
                queued = fetch.observations.size,
                deletedRecordIds = fetch.deletedRecordIds
            )
            if (ack.ok && ack.cursorAdvanced) {
                reader.acknowledgeCursor(ack.nextCursorToken ?: fetch.nextChangesToken)
                prefs.setLastSuccess(Instant.now().toString())
                prefs.setLastError(null)
                prefs.setQueuedCount(0)
                Result.success()
            } else if (ack.status == "unauthorized" || ack.status == "revoked") {
                prefs.setLastError(ack.status)
                Result.failure()
            } else if (ack.ok) {
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
