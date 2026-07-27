package com.healthchecker.companion.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Test

class DeletionTombstoneTest {
    @Test
    fun tombstoneDoesNotClaimClinicalDelete() {
        val t = DeletionTombstone(
            sourceRecordId = "rec-1",
            measuredAt = "2026-07-27T12:00:00Z",
            receivedAt = "2026-07-27T12:01:00Z"
        )
        val map = t.toHostMap()
        assertEquals("health_connect_deletion", map["metric_type"])
        assertEquals("tombstone_only_no_clinical_delete", map["text_value"])
        @Suppress("UNCHECKED_CAST")
        val device = map["device"] as Map<String, String>
        assertEquals("false", device["clinical_history_deleted"])
        assertFalse(map["provenance"].toString().contains("clinical_delete_executed"))
        // Must not look like a clinical measurement value
        assertEquals(null, map["value"])
        assertNotEquals("heart_rate", map["metric_type"])
    }

    @Test
    fun duplicateTombstoneMapsAreIdempotentBySourceId() {
        val a = DeletionTombstone("dup", measuredAt = "t1", receivedAt = "t2")
        val b = DeletionTombstone("dup", measuredAt = "t3", receivedAt = "t4")
        assertEquals(a.sourceRecordId, b.sourceRecordId)
        assertEquals(a.toHostMap()["observation_id"], b.toHostMap()["observation_id"])
    }
}
