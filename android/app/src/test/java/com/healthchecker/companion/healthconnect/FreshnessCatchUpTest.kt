package com.healthchecker.companion.healthconnect

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

class FreshnessCatchUpTest {
    @Test
    fun emptyIncrementalNeedsCatchUpWhenInventoryIsNewer() {
        val inventory = mapOf("heart_rate" to "2026-08-24T12:00:00Z")
        val fetched = emptyMap<String, String>()
        assertEquals(setOf("heart_rate"), FreshnessCatchUp.metricsNeedingCatchUp(inventory, fetched))
    }

    @Test
    fun matchingTimestampsDoNotCatchUp() {
        val stamp = "2026-08-18T10:00:00Z"
        val inventory = mapOf("heart_rate" to stamp, "steps" to stamp)
        val fetched = mapOf("heart_rate" to stamp, "steps" to stamp)
        assertTrue(FreshnessCatchUp.metricsNeedingCatchUp(inventory, fetched).isEmpty())
    }

    @Test
    fun mergeKeepsTheLaterTimestamp() {
        val merged = FreshnessCatchUp.mergeLatest(
            mapOf("heart_rate" to "2026-08-18T10:00:00Z"),
            emptyMap(),
            mapOf("heart_rate" to "2026-08-24T12:00:00Z", "steps" to "2026-08-23T00:00:00Z")
        )
        assertEquals("2026-08-24T12:00:00Z", merged["heart_rate"])
        assertEquals("2026-08-23T00:00:00Z", merged["steps"])
    }

    @Test
    fun catchUpStartUsesFetchedLatestWithOverlapRatherThanFullHistory() {
        val now = Instant.parse("2026-08-24T18:00:00Z")
        val start = FreshnessCatchUp.catchUpStart(now, "2026-08-18T10:00:00Z")
        assertEquals(Instant.parse("2026-08-18T09:00:00Z"), start)
    }

    @Test
    fun capNewestPrefersCurrentSamples() {
        val rows = (1..800).map { index ->
            ObservationMapper.heartRate(
                "r$index",
                70,
                Instant.parse("2026-08-18T00:00:00Z").plusSeconds(index.toLong()),
                "com.sec.android.app.shealth"
            )
        }
        val capped = FreshnessCatchUp.capNewest(rows, 10)
        assertEquals(10, capped.size)
        assertEquals("2026-08-18T00:13:20Z", capped.last().measuredAt)
        assertTrue(capped.first().measuredAt < capped.last().measuredAt)
    }
}
