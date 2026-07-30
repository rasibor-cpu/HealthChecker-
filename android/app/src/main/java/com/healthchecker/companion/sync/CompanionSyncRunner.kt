package com.healthchecker.companion.sync

import android.content.Context
import com.healthchecker.companion.healthconnect.HealthConnectAvailability
import com.healthchecker.companion.healthconnect.HealthConnectCapability
import com.healthchecker.companion.healthconnect.CapabilityReport
import com.healthchecker.companion.healthconnect.HealthConnectReader
import com.healthchecker.companion.healthconnect.SyncDeliveryGate
import com.healthchecker.companion.host.HostClient
import com.healthchecker.companion.secure.SecurePrefs
import org.json.JSONObject
import java.time.Instant

/**
 * Shared manual/worker sync executor for HC-306I-R7.
 *
 * Persists a complete bounded delivery plan before the first send, advances chunk
 * progress only after durable acknowledgements, and advances the Health Connect
 * cursor only after the final chunk is durably acknowledged and the token+scope
 * pair is persisted.
 */
class CompanionSyncRunner(
    private val context: Context,
    private val prefs: SecurePrefs,
    private val hostClientFactory: (SecurePrefs) -> HostClient = { HostClient(it) },
    private val readerFactory: (Context, SecurePrefs) -> HealthConnectReader = { c, p -> HealthConnectReader(c, p) },
    private val capabilityFactory: suspend (Context) -> CapabilityReport = {
        HealthConnectCapability(it).report()
    },
) {
    enum class Outcome {
        SUCCESS,
        RETRY,
        FAILURE,
    }

    suspend fun runOnce(workOwner: String): Outcome {
        prefs.setLastAttempt(Instant.now().toString())
        val capability = capabilityFactory(context)
        if (capability.availability != HealthConnectAvailability.READY) {
            prefs.setLastError(capability.message)
            prefs.setLastQueryPerformed(false)
            return Outcome.SUCCESS
        }

        val reader = readerFactory(context, prefs)
        val pendingLoad = prefs.loadPendingBatch()
        if (pendingLoad is SecurePrefs.PendingBatchLoad.Corrupt) {
            prefs.setLastError("pending_batch_corrupt")
            prefs.setLastQueryPerformed(false)
            return if (workOwner == "workmanager") Outcome.FAILURE else Outcome.SUCCESS
        }

        var pending = (pendingLoad as? SecurePrefs.PendingBatchLoad.Loaded)?.batch
        if (pending == null) {
            val fetch = reader.fetchNew()
            prefs.setQueuedCount(fetch.observations.size)
            prefs.setLastQueryPerformed(fetch.queryPerformed)
            prefs.setPartialPermissionWarning(SyncDeliveryGate.visiblePartialWarning(fetch))
            if (!SyncDeliveryGate.shouldDeliver(fetch)) {
                prefs.setLastError(SyncDeliveryGate.visibleError(fetch) ?: "query_not_performed")
                return Outcome.SUCCESS
            }
            try {
                val seed = PendingBatch.create(
                    observations = fetch.observations,
                    nextChangesToken = fetch.nextChangesToken,
                    deletedRecordIds = fetch.deletedRecordIds,
                    tokenScope = fetch.proposedTokenScope,
                    partialPermissionWarning = SyncDeliveryGate.visiblePartialWarning(fetch),
                )
                pending = PendingBatch.createBoundedPlan(
                    rootBatchId = seed.batchId,
                    rootNonce = seed.nonce,
                    observationsJson = seed.observationsJson,
                    nextChangesToken = seed.nextChangesToken,
                    deletedRecordIdsJson = seed.deletedRecordIdsJson,
                    tokenScope = seed.tokenScope,
                    partialPermissionWarning = seed.partialPermissionWarning,
                    healthConnectStatusJson = JSONObject().put("availability", capability.availability.name).toString(),
                    permissionsJson = JSONObject()
                        .put("granted_count", capability.permissionsGranted.size)
                        .put("missing_count", capability.permissionsMissing.size)
                        .toString(),
                    workmanagerJson = JSONObject()
                        .put("unique_name", com.healthchecker.companion.work.MonitoringSyncWorker.UNIQUE_NAME)
                        .put("overlap_prevented", true)
                        .put("exact_timing_guaranteed", false)
                        .toString(),
                    nowEpochMs = seed.createdAtEpochMs,
                )
            } catch (_: IllegalArgumentException) {
                prefs.setLastError("single_observation_exceeds_bounded_payload_limit")
                prefs.setQueuedCount(fetch.observations.size)
                return if (workOwner == "workmanager") Outcome.FAILURE else Outcome.SUCCESS
            }
            prefs.setPendingBatch(pending)
        } else if (!pending.hasBoundedPlan()) {
            try {
                val migrated = PendingBatch.createBoundedPlan(
                    rootBatchId = pending.batchId,
                    rootNonce = pending.nonce,
                    observationsJson = pending.observationsJson,
                    nextChangesToken = pending.nextChangesToken,
                    deletedRecordIdsJson = pending.deletedRecordIdsJson,
                    tokenScope = pending.tokenScope,
                    partialPermissionWarning = pending.partialPermissionWarning,
                    healthConnectStatusJson = JSONObject().put("availability", capability.availability.name).toString(),
                    permissionsJson = JSONObject()
                        .put("granted_count", capability.permissionsGranted.size)
                        .put("missing_count", capability.permissionsMissing.size)
                        .toString(),
                    workmanagerJson = JSONObject()
                        .put("unique_name", com.healthchecker.companion.work.MonitoringSyncWorker.UNIQUE_NAME)
                        .put("overlap_prevented", true)
                        .put("exact_timing_guaranteed", false)
                        .toString(),
                    nowEpochMs = pending.createdAtEpochMs,
                )
                prefs.setPendingBatch(migrated)
                pending = migrated
            } catch (_: IllegalArgumentException) {
                prefs.setLastError("single_observation_exceeds_bounded_payload_limit")
                prefs.setQueuedCount(pending.observations().size)
                return if (workOwner == "workmanager") Outcome.FAILURE else Outcome.SUCCESS
            }
        }

        val host = hostClientFactory(prefs)
        while (pending != null) {
            val chunk = pending.activeChunk() ?: run {
                prefs.setLastError("pending_chunk_missing")
                return if (workOwner == "workmanager") Outcome.FAILURE else Outcome.SUCCESS
            }
            prefs.setQueuedCount(pending.remainingObservationCount())
            prefs.setLastQueryPerformed(true)
            prefs.setPartialPermissionWarning(pending.partialPermissionWarning)
            val ack = host.deliver(
                batchId = chunk.batchId,
                nonce = chunk.nonce,
                observationsJson = chunk.observationsJson,
                nextChangesToken = chunk.nextChangesToken,
                healthConnectStatusJson = pending.healthConnectStatusJson ?: JSONObject().toString(),
                permissionsJson = pending.permissionsJson ?: JSONObject().toString(),
                workmanagerJson = pending.workmanagerJson ?: JSONObject().toString(),
                queued = chunk.queuedObservations,
                deletedRecordIdsJson = chunk.deletedRecordIdsJson,
            )

            if (ack.status == "unauthorized" || ack.status == "revoked") {
                prefs.setPendingBatch(null)
                prefs.setLastError(ack.status)
                prefs.setQueuedCount(0)
                return Outcome.FAILURE
            }

            val durableAck = ack.ok && ack.ackBatchId == chunk.batchId
            if (!durableAck) {
                prefs.setLastError(ack.error ?: ack.status)
                return Outcome.RETRY
            }

            if (!pending.isFinalChunk()) {
                pending = pending.advanceChunk()
                if (pending == null) {
                    prefs.setLastError("pending_chunk_advance_failed")
                    return Outcome.RETRY
                }
                prefs.setPendingBatch(pending)
                continue
            }

            if (!ack.cursorAdvanced) {
                prefs.setLastError(ack.error ?: "partial_no_cursor")
                return Outcome.RETRY
            }

            val persisted = reader.acknowledgeCursor(
                ack.nextCursorToken ?: pending.nextChangesToken,
                pending.tokenScope,
            )
            if (!persisted) {
                prefs.setLastError("cursor_scope_persist_failed")
                return Outcome.RETRY
            }

            prefs.setPendingBatch(null)
            prefs.setLastSuccess(Instant.now().toString())
            prefs.setLastError(null)
            prefs.setQueuedCount(0)
            return Outcome.SUCCESS
        }

        prefs.setLastError("pending_plan_unavailable")
        return Outcome.RETRY
    }
}
