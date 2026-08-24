package com.healthchecker.companion.healthconnect

import java.time.Instant
import java.time.OffsetDateTime
import java.time.temporal.ChronoUnit

/**
 * HC324 — decide when an incremental Health Connect change-token fetch must be
 * supplemented by a bounded newest-first catch-up read.
 *
 * Pure functions; no Health Connect client, no clinical fabrication.
 */
object FreshnessCatchUp {
    const val CATCH_UP_MAX_OBSERVATIONS = 720
    const val CATCH_UP_LOOKBACK_HOURS = 336L // 14 days
    const val CATCH_UP_OVERLAP_HOURS = 1L

    fun mergeLatest(vararg maps: Map<String, String>): Map<String, String> {
        val out = linkedMapOf<String, String>()
        for (map in maps) {
            for ((metric, at) in map) {
                if (at.isBlank()) continue
                val previous = out[metric]
                if (previous == null || at > previous) out[metric] = at
            }
        }
        return out
    }

    fun metricsNeedingCatchUp(
        inventoryLatest: Map<String, String>,
        fetchedLatest: Map<String, String>
    ): Set<String> {
        return inventoryLatest.filter { (metric, at) ->
            val fetched = fetchedLatest[metric]
            fetched.isNullOrBlank() || at > fetched
        }.keys
    }

    fun catchUpStart(now: Instant, fetchedLatestIso: String?): Instant {
        val floor = now.minus(CATCH_UP_LOOKBACK_HOURS, ChronoUnit.HOURS)
        val parsed = parseIsoInstant(fetchedLatestIso) ?: return floor
        val overlap = parsed.minus(CATCH_UP_OVERLAP_HOURS, ChronoUnit.HOURS)
        return if (overlap.isAfter(floor)) overlap else floor
    }

    fun capNewest(
        observations: List<CompanionObservation>,
        limit: Int = CATCH_UP_MAX_OBSERVATIONS
    ): List<CompanionObservation> {
        if (observations.size <= limit) return observations
        return observations.sortedByDescending { it.measuredAt }.take(limit).sortedBy { it.measuredAt }
    }

    fun parseIsoInstant(value: String?): Instant? {
        val text = value?.trim().orEmpty()
        if (text.isEmpty()) return null
        return try {
            Instant.parse(text)
        } catch (_: Exception) {
            try {
                OffsetDateTime.parse(text).toInstant()
            } catch (_: Exception) {
                null
            }
        }
    }
}
