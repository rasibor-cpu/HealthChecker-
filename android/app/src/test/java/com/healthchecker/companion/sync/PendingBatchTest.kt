package com.healthchecker.companion.sync

import com.healthchecker.companion.healthconnect.CompanionObservation
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
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
        val batch = PendingBatch.create(
            listOf(obs()),
            "tok-1",
            listOf("del-1"),
            tokenScope = "steps",
            nowEpochMs = 42L
        )
        val restored = PendingBatch.fromJson(batch.toJson())
        assertNotNull(restored)
        assertEquals(batch.batchId, restored!!.batchId)
        assertEquals(batch.nonce, restored.nonce)
        assertEquals(1, restored.observations().size)
        assertEquals(listOf("del-1"), restored.deletedRecordIds())
        assertTrue(restored.nextChangesToken == "tok-1")
        assertEquals("steps", restored.tokenScope)
        assertFalse(restored.partialPermissionWarning)
    }

    @Test
    fun partialWarningSurvivesRoundTripForRetryIdentity() {
        val batch = PendingBatch.create(
            listOf(obs()),
            "tok-partial",
            emptyList(),
            tokenScope = "steps",
            partialPermissionWarning = true,
            nowEpochMs = 7L
        )
        val restored = PendingBatch.fromJson(batch.toJson())!!
        assertTrue(restored.partialPermissionWarning)
        assertEquals("steps", restored.tokenScope)
        assertEquals(batch.batchId, restored.batchId)
        assertEquals(batch.nonce, restored.nonce)
    }

    @Test
    fun legacyJsonWithoutTokenScopeStillParses() {
        val legacy = PendingBatch.create(listOf(obs()), "tok-legacy", emptyList(), nowEpochMs = 9L)
        // Strip token_scope key to simulate pre-R3 payload
        val o = org.json.JSONObject(legacy.toJson())
        o.remove("token_scope")
        val restored = PendingBatch.fromJson(o.toString())
        assertNotNull(restored)
        assertNull(restored!!.tokenScope)
        assertEquals(legacy.batchId, restored.batchId)
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

    @Test
    fun boundedPlanSplitsByCountWithStableOrderedChunks() {
        val observations = (1..181).map { obs("o$it") }
        val plan = PendingBatch.createBoundedPlan(
            observationsJson = JSONArray().apply {
                observations.forEach { put(JSONObject(it.toMap())) }
            }.toString(),
            nextChangesToken = "tok-1",
            deletedRecordIdsJson = "[]",
            tokenScope = "steps",
            partialPermissionWarning = false,
            healthConnectStatusJson = JSONObject().put("availability", "READY").toString(),
            permissionsJson = JSONObject().put("granted_count", 1).put("missing_count", 0).toString(),
            workmanagerJson = JSONObject().put("unique_name", "hc303a_monitoring_sync").toString(),
            maxObservationsPerChunk = 180,
            maxPayloadBytes = 480_000,
        )
        assertTrue(plan.hasBoundedPlan())
        assertEquals(2, plan.chunks().size)
        assertEquals(180, plan.chunks()[0].observations().size)
        assertEquals(1, plan.chunks()[1].observations().size)
        assertEquals("o1", plan.chunks()[0].observations().first().observationId)
        assertEquals("o181", plan.chunks()[1].observations().first().observationId)
        assertNull(plan.chunks()[0].nextChangesToken)
        assertEquals("tok-1", plan.chunks()[1].nextChangesToken)
        assertEquals(emptyList<String>(), plan.chunks()[0].deletedRecordIds())
    }

    @Test
    fun boundedPlanRoundTripPreservesStableChunkIdsAcrossRestart() {
        val observations = (1..5).map { obs("r$it") }
        val plan = PendingBatch.createBoundedPlan(
            rootBatchId = "root-batch",
            rootNonce = "root-nonce",
            observationsJson = JSONArray().apply {
                observations.forEach { put(JSONObject(it.toMap())) }
            }.toString(),
            nextChangesToken = "tok-r",
            deletedRecordIdsJson = """["del-1"]""",
            tokenScope = "steps",
            partialPermissionWarning = true,
            healthConnectStatusJson = JSONObject().put("availability", "READY").toString(),
            permissionsJson = JSONObject().put("granted_count", 1).put("missing_count", 7).toString(),
            workmanagerJson = JSONObject().put("unique_name", "hc303a_monitoring_sync").toString(),
            maxObservationsPerChunk = 2,
            maxPayloadBytes = 480_000,
        )
        val restored = PendingBatch.fromJson(plan.toJson())!!
        assertEquals(plan.batchId, restored.batchId)
        assertEquals(plan.nonce, restored.nonce)
        assertEquals(plan.chunks().map { it.batchId }, restored.chunks().map { it.batchId })
        assertEquals(plan.chunks().map { it.nonce }, restored.chunks().map { it.nonce })
        assertTrue(restored.partialPermissionWarning)
    }

    @Test
    fun advanceChunkPersistsProgressUntilFinalChunk() {
        val observations = (1..4).map { obs("a$it") }
        val plan = PendingBatch.createBoundedPlan(
            observationsJson = JSONArray().apply {
                observations.forEach { put(JSONObject(it.toMap())) }
            }.toString(),
            nextChangesToken = "tok-a",
            deletedRecordIdsJson = "[]",
            tokenScope = "steps",
            partialPermissionWarning = false,
            healthConnectStatusJson = JSONObject().put("availability", "READY").toString(),
            permissionsJson = JSONObject().put("granted_count", 1).put("missing_count", 0).toString(),
            workmanagerJson = JSONObject().put("unique_name", "hc303a_monitoring_sync").toString(),
            maxObservationsPerChunk = 2,
            maxPayloadBytes = 480_000,
        )
        assertEquals(0, plan.activeChunkIndex)
        val next = plan.advanceChunk()!!
        assertEquals(1, next.activeChunkIndex)
        assertNull(next.advanceChunk())
    }

    @Test
    fun oversizedSingleObservationFailsClosed() {
        val huge = obs("huge").copy(textValue = "x".repeat(600_000))
        try {
            PendingBatch.createBoundedPlan(
                observationsJson = JSONArray().put(JSONObject(huge.toMap())).toString(),
                nextChangesToken = "tok-h",
                deletedRecordIdsJson = "[]",
                tokenScope = "steps",
                partialPermissionWarning = false,
                healthConnectStatusJson = JSONObject().put("availability", "READY").toString(),
                permissionsJson = JSONObject().put("granted_count", 1).put("missing_count", 0).toString(),
                workmanagerJson = JSONObject().put("unique_name", "hc303a_monitoring_sync").toString(),
                maxObservationsPerChunk = 180,
                maxPayloadBytes = 480_000,
            )
            fail("expected IllegalArgumentException")
        } catch (_: IllegalArgumentException) {
            // expected fail-closed oversized observation rejection
        }
    }

    @Test
    fun emptyObservationPlanStillProducesFinalCursorChunk() {
        val plan = PendingBatch.createBoundedPlan(
            observationsJson = "[]",
            nextChangesToken = "tok-empty",
            deletedRecordIdsJson = "[]",
            tokenScope = "steps",
            partialPermissionWarning = false,
            healthConnectStatusJson = JSONObject().put("availability", "READY").toString(),
            permissionsJson = JSONObject().put("granted_count", 1).put("missing_count", 0).toString(),
            workmanagerJson = JSONObject().put("unique_name", "hc303a_monitoring_sync").toString(),
        )
        assertEquals(1, plan.chunks().size)
        assertEquals("tok-empty", plan.chunks()[0].nextChangesToken)
        assertTrue(plan.isFinalChunk())
    }

    @Test
    fun nonFinalChunksOmitCursorAndDeletions() {
        val observations = (1..5).map { obs("n$it").copy(metricType = "steps", unit = "count") }
        val plan = PendingBatch.createBoundedPlan(
            observationsJson = JSONArray().apply {
                observations.forEach { put(JSONObject(it.toMap())) }
            }.toString(),
            nextChangesToken = "tok-final-only",
            deletedRecordIdsJson = """["del-a"]""",
            tokenScope = "steps",
            partialPermissionWarning = true,
            healthConnectStatusJson = JSONObject().put("availability", "READY").toString(),
            permissionsJson = JSONObject().put("granted_count", 1).put("missing_count", 7).toString(),
            workmanagerJson = JSONObject().put("unique_name", "hc303a_monitoring_sync").toString(),
            maxObservationsPerChunk = 2,
            maxPayloadBytes = 480_000,
        )
        assertEquals(3, plan.chunks().size)
        plan.chunks().dropLast(1).forEach { chunk ->
            assertNull(chunk.nextChangesToken)
            assertEquals(emptyList<String>(), chunk.deletedRecordIds())
            assertTrue(chunk.measuredPayloadBytes < 512_000)
            assertTrue(chunk.observations().size < 200)
            assertTrue(chunk.observations().all { it.metricType == "steps" })
        }
        val finalChunk = plan.chunks().last()
        assertEquals("tok-final-only", finalChunk.nextChangesToken)
        assertEquals(listOf("del-a"), finalChunk.deletedRecordIds())
        assertTrue(plan.partialPermissionWarning)
    }

    @Test
    fun measuredPayloadUsesExactEnvelopeAndStaysUnderHostCeiling() {
        val observations = (1..50).map { obs("m$it").copy(metricType = "steps", unit = "count") }
        val plan = PendingBatch.createBoundedPlan(
            observationsJson = JSONArray().apply {
                observations.forEach { put(JSONObject(it.toMap())) }
            }.toString(),
            nextChangesToken = "tok-m",
            deletedRecordIdsJson = "[]",
            tokenScope = "steps",
            partialPermissionWarning = false,
            healthConnectStatusJson = JSONObject().put("availability", "READY").toString(),
            permissionsJson = JSONObject().put("granted_count", 1).put("missing_count", 0).toString(),
            workmanagerJson = JSONObject().put("unique_name", "hc303a_monitoring_sync").toString(),
            maxObservationsPerChunk = 40,
            maxPayloadBytes = 480_000,
        )
        plan.chunks().forEach { chunk ->
            assertTrue(chunk.measuredPayloadBytes < 512_000)
            assertTrue(chunk.observations().size < 200)
            assertTrue(chunk.measuredPayloadBytes <= 480_000)
        }
    }

    @Test
    fun legacyMonolithicPendingMigratesWithoutNetworkAndPreservesIdentity() {
        val legacy = PendingBatch.create(
            observations = (1..5).map { obs("legacy$it").copy(metricType = "steps", unit = "count") },
            nextChangesToken = "tok-legacy-mig",
            deletedRecordIds = listOf("del-legacy"),
            tokenScope = "steps",
            partialPermissionWarning = true,
            nowEpochMs = 99L,
        )
        assertFalse(legacy.hasBoundedPlan())
        val migrated = PendingBatch.createBoundedPlan(
            rootBatchId = legacy.batchId,
            rootNonce = legacy.nonce,
            observationsJson = legacy.observationsJson,
            nextChangesToken = legacy.nextChangesToken,
            deletedRecordIdsJson = legacy.deletedRecordIdsJson,
            tokenScope = legacy.tokenScope,
            partialPermissionWarning = legacy.partialPermissionWarning,
            healthConnectStatusJson = JSONObject().put("availability", "READY").toString(),
            permissionsJson = JSONObject().put("granted_count", 1).put("missing_count", 7).toString(),
            workmanagerJson = JSONObject().put("unique_name", "hc303a_monitoring_sync").toString(),
            nowEpochMs = legacy.createdAtEpochMs,
            maxObservationsPerChunk = 2,
            maxPayloadBytes = 480_000,
        )
        assertEquals(legacy.batchId, migrated.batchId)
        assertEquals(legacy.nonce, migrated.nonce)
        assertEquals(legacy.observations().map { it.observationId }, migrated.observations().map { it.observationId })
        assertEquals(legacy.nextChangesToken, migrated.nextChangesToken)
        assertEquals(legacy.tokenScope, migrated.tokenScope)
        assertTrue(migrated.partialPermissionWarning)
        assertTrue(migrated.hasBoundedPlan())
        assertEquals(3, migrated.chunks().size)
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
