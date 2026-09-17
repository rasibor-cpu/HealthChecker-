package com.healthchecker.companion.healthconnect

import java.time.Instant
import java.time.OffsetDateTime
import java.time.temporal.ChronoUnit

/**
 * HC324/HC330 — decide when an incremental Health Connect change-token fetch must be
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
        // HC330: when inventory proves at least one metric is ahead of the incremental
        // feed, the caller cannot safely use the newest timestamp from an unrelated
        // metric as the lower bound. A fresh heart-rate sample, for example, must not
        // collapse the catch-up window and hide last night's sleep record. Use the
        // existing bounded 14-day floor for every inventory-ahead recovery pass.
        // The fetched timestamp is deliberately retained in the signature for API
        // compatibility with HC324 callers.
        @Suppress("UNUSED_VARIABLE")
        val ignoredFetchedLatest = fetchedLatestIso
        return now.minus(CATCH_UP_LOOKBACK_HOURS, ChronoUnit.HOURS)
    }

    fun capNewest(
        observations: List<CompanionObservation>,
        limit: Int = CATCH_UP_MAX_OBSERVATIONS
    ): List<CompanionObservation> {
        if (observations.size <= limit) return observations
        if (limit <= 0) return emptyList()

        // Preserve the newest observation for every metric before filling the
        // remaining budget with the newest observations globally. Without this,
        // high-frequency heart-rate samples can crowd lower-frequency metrics such
        // as sleep or oxygen saturation out of the bounded catch-up payload.
        val newestPerMetric = observations
            .groupBy { it.metricType }
            .mapNotNull { (_, rows) -> rows.maxByOrNull { it.measuredAt } }
            .sortedByDescending { it.measuredAt }

        if (newestPerMetric.size >= limit) {
            return newestPerMetric.take(limit).sortedBy { it.measuredAt }
        }

        val reserved = newestPerMetric.map {
            Triple(it.metricType, it.sourceRecordId, it.measuredAt)
        }.toHashSet()
        val remainder = observations
            .asSequence()
            .filter {
                Triple(it.metricType, it.sourceRecordId, it.measuredAt) !in reserved
            }
            .sortedByDescending { it.measuredAt }
            .take(limit - newestPerMetric.size)
            .toList()

        return (newestPerMetric + remainder)
            .distinctBy { Triple(it.metricType, it.sourceRecordId, it.measuredAt) }
            .sortedBy { it.measuredAt }
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