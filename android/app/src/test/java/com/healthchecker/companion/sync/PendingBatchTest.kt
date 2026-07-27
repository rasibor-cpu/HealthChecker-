package com.healthchecker.companion.sync

import com.healthchecker.companion.healthconnect.CompanionObservation
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28], manifest = Config.NONE)
class PendingBatchTest {
    private fun obs(id: String = "o1") = CompanionObservation(
        observationId = id,
        sourceRecordId = "r-$id",
        metricType = "heart_rate",
        value = 70.0,
        unit = "bpm",
        measuredAt = "2026-07-27T12:00:00Z",
        receivedAt = "2026-07-27T12:01:00Z"
    )

    @Test
    fun roundTripPreservesStableIdentity() {
        val batch = PendingBatch.create(listOf(obs()), "tok-1", listOf("del-1"), nowEpochMs = 42L)
        val restored = PendingBatch.fromJson(batch.toJson())
        assertNotNull(restored)
        assertEquals(batch.batchId, restored!!.batchId)
        assertEquals(batch.nonce, restored.nonce)
        assertEquals(1, restored.observations().size)
        assertEquals(listOf("del-1"), restored.deletedRecordIds())
        assertTrue(restored.nextChangesToken == "tok-1")
    }

    @Test
    fun recreateFromSameJsonKeepsIdentityAcrossProcessRecreation() {
        val original = PendingBatch.create(listOf(obs("a")), "tok-a", emptyList(), nowEpochMs = 1L)
        val json = original.toJson()
        val afterKill = PendingBatch.fromJson(json)!!
        val afterRetry = PendingBatch.fromJson(afterKill.toJson())!!
        assertEquals(original.batchId, afterRetry.batchId)
        assertEquals(original.nonce, afterRetry.nonce)
        assertEquals(original.observationsJson, afterRetry.observationsJson)
    }

    @Test
    fun frozenBatchDoesNotAbsorbNewReadings() {
        val pending = PendingBatch.create(listOf(obs("old")), "tok-1", emptyList())
        val newer = listOf(obs("new1"), obs("new2"))
        // Identity frozen: creating a separate batch for newer readings must not mutate pending.
        val next = PendingBatch.create(newer, "tok-2", emptyList())
        assertEquals(1, pending.observations().size)
        assertEquals("old", pending.observations()[0].observationId)
        assertNotEquals(pending.batchId, next.batchId)
        assertEquals(2, next.observations().size)
    }

    @Test
    fun corruptedStorageSurfacesAsNull() {
        assertNull(PendingBatch.fromJson("{not-json"))
        assertNull(PendingBatch.fromJson("""{"batch_id":"","nonce":"n","observations_json":"[]"}"""))
        assertNull(PendingBatch.fromJson("""{"batch_id":"b","nonce":"n","observations_json":"NOT_ARRAY"}"""))
    }
}

class PendingBatchAckTest {
    @Test
    fun lateAckAgainstNewerBatchDoesNotClear() {
        assertFalse(
            PendingBatchAck.shouldClearPending(
                pendingBatchId = "newer-batch",
                ackOk = true,
                cursorAdvanced = true,
                ackBatchId = "older-batch",
                status = "accepted"
            )
        )
    }

    @Test
    fun matchingDurableAckClears() {
        assertTrue(
            PendingBatchAck.shouldClearPending(
                pendingBatchId = "b1",
                ackOk = true,
                cursorAdvanced = true,
                ackBatchId = "b1",
                status = "accepted"
            )
        )
    }

    @Test
    fun partialOkWithoutCursorDoesNotClear() {
        assertFalse(
            PendingBatchAck.shouldClearPending(
                pendingBatchId = "b1",
                ackOk = true,
                cursorAdvanced = false,
                ackBatchId = "b1",
                status = "partial"
            )
        )
    }

    @Test
    fun unauthorizedClearsWithoutRequiringCursor() {
        assertTrue(
            PendingBatchAck.shouldClearPending(
                pendingBatchId = "b1",
                ackOk = false,
                cursorAdvanced = false,
                ackBatchId = null,
                status = "unauthorized"
            )
        )
    }
}
