package com.healthchecker.companion.sync

import com.healthchecker.companion.healthconnect.CompanionObservation
import com.healthchecker.companion.host.HostClient
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * HC-306I-R11 — timeout leaves same chunk pending; durable ack advances once;
 * non-final never requires cursor advancement.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28], manifest = Config.NONE)
class ChunkAckDecisionTest {
    private fun obs(id: String) = CompanionObservation(
        observationId = id,
        sourceRecordId = "r-$id",
        metricType = "steps",
        value = 1.0,
        unit = "count",
        measuredAt = "2026-07-30T12:00:00Z",
        receivedAt = "2026-07-30T12:01:00Z",
    )

    private fun plan(): PendingBatch = PendingBatch.createBoundedPlan(
        rootBatchId = "root",
        rootNonce = "nonce",
        observationsJson = JSONArray().apply {
            (1..3).forEach { put(JSONObject(obs("o$it").toMap())) }
        }.toString(),
        nextChangesToken = "final-tok",
        deletedRecordIdsJson = "[]",
        tokenScope = "steps",
        partialPermissionWarning = false,
        healthConnectStatusJson = JSONObject().put("availability", "READY").toString(),
        permissionsJson = JSONObject().put("granted_count", 1).put("missing_count", 0).toString(),
        workmanagerJson = JSONObject().put("unique_name", "hc303a_monitoring_sync").toString(),
        maxObservationsPerChunk = 2,
        maxPayloadBytes = 480_000,
    )

    @Test
    fun timeoutLeavesSameChunkPendingWithStableIdentity() {
        val pending = plan()
        val chunk0 = pending.activeChunk()!!
        val timeout = HostClient.DeliveryAck(
            ok = false,
            status = "transport_error",
            cursorAdvanced = false,
            nextCursorToken = null,
            error = "SocketTimeoutException",
        )
        val action = ChunkAckDecision.decide(pending, timeout)
        assertTrue(action is ChunkAckDecision.Action.RetrySameChunk)
        assertEquals(0, pending.activeChunkIndex)
        assertEquals(chunk0.batchId, pending.activeChunk()!!.batchId)
        assertEquals(chunk0.nonce, pending.activeChunk()!!.nonce)
        assertNull(chunk0.nextChangesToken)
    }

    @Test
    fun acceptedThenTimeoutThenDuplicateThenAdvanceSequence() {
        var pending = plan()
        val firstId = pending.activeChunk()!!.batchId

        // 1) accepted non-final
        val accepted = HostClient.DeliveryAck(
            ok = true,
            status = "accepted",
            cursorAdvanced = false,
            nextCursorToken = null,
            error = null,
            ackBatchId = firstId,
        )
        val advance = ChunkAckDecision.decide(pending, accepted)
        assertTrue(advance is ChunkAckDecision.Action.AdvanceToNextChunk)
        pending = (advance as ChunkAckDecision.Action.AdvanceToNextChunk).next
        assertEquals(1, pending.activeChunkIndex)

        // Simulate crash/timeout before local progress was observed by caller: still on chunk 0
        // After advance already applied, timeout on next chunk keeps identity.
        val secondId = pending.activeChunk()!!.batchId
        val timeout = HostClient.DeliveryAck(
            ok = false,
            status = "transport_error",
            cursorAdvanced = false,
            nextCursorToken = null,
            error = "SocketTimeoutException",
            ackBatchId = null,
        )
        assertTrue(ChunkAckDecision.decide(pending, timeout) is ChunkAckDecision.Action.RetrySameChunk)
        assertEquals(secondId, pending.activeChunk()!!.batchId)

        // 2) duplicate_ack for same chunk identity (host stored despite lost response)
        val dup = HostClient.DeliveryAck(
            ok = true,
            status = "duplicate_ack",
            cursorAdvanced = false,
            nextCursorToken = null,
            error = null,
            ackBatchId = secondId,
        )
        // Final chunk in this 2-chunk plan
        assertTrue(pending.isFinalChunk())
        // Non-final false cursor would retry; final requires cursor_advanced
        val dupNoCursor = ChunkAckDecision.decide(pending, dup)
        assertTrue(dupNoCursor is ChunkAckDecision.Action.RetrySameChunk)

        val dupFinal = HostClient.DeliveryAck(
            ok = true,
            status = "duplicate_ack",
            cursorAdvanced = true,
            nextCursorToken = "final-tok",
            error = null,
            ackBatchId = secondId,
        )
        val finalize = ChunkAckDecision.decide(pending, dupFinal)
        assertTrue(finalize is ChunkAckDecision.Action.FinalizeCursor)
        assertEquals("final-tok", (finalize as ChunkAckDecision.Action.FinalizeCursor).cursorToken)
    }

    @Test
    fun nonFinalNeverFinalizesCursorEvenIfHostReportsAdvanced() {
        val pending = plan()
        val chunkId = pending.activeChunk()!!.batchId
        // Host bug regression shield: even if host wrongly reports cursor_advanced,
        // non-final policy advances plan only — cursor finalize path is final-only.
        val weird = HostClient.DeliveryAck(
            ok = true,
            status = "accepted",
            cursorAdvanced = true,
            nextCursorToken = null,
            error = null,
            ackBatchId = chunkId,
        )
        val action = ChunkAckDecision.decide(pending, weird)
        assertTrue(action is ChunkAckDecision.Action.AdvanceToNextChunk)
        assertFalse(action is ChunkAckDecision.Action.FinalizeCursor)
        assertNull(pending.activeChunk()!!.nextChangesToken)
        assertEquals("steps", pending.tokenScope)
    }

    @Test
    fun clientTimeoutConstantsAreFiniteAndRaised() {
        assertEquals(180L, HostClient.CALL_TIMEOUT_SECONDS)
        assertTrue(HostClient.CALL_TIMEOUT_SECONDS < Long.MAX_VALUE)
        assertTrue(HostClient.CONNECT_TIMEOUT_SECONDS > 0L)
        assertTrue(HostClient.WRITE_TIMEOUT_SECONDS > 0L)
        assertTrue(HostClient.CALL_TIMEOUT_SECONDS >= HostClient.WRITE_TIMEOUT_SECONDS)
    }
}
