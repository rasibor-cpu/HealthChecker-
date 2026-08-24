package com.healthchecker.companion.healthconnect

import com.healthchecker.companion.util.PrivacyRedactor
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant
import java.time.ZoneOffset

/**
 * JVM unit tests for observation mapping — no phone or real health records required.
 */
class ObservationMapperTest {
    @Test
    fun heartRateMapsTimezoneAwareIso() {
        val t = Instant.parse("2026-07-27T12:00:00Z")
        val obs = ObservationMapper.heartRate("r1", 72, t, "com.samsung.health")
        assertEquals("heart_rate", obs.metricType)
        assertEquals("bpm", obs.unit)
        assertEquals("2026-07-27T12:00:00Z", obs.measuredAt)
        assertEquals("DELAYED", obs.acquisitionMode)
        assertTrue(obs.receivedAt.endsWith("Z") || obs.receivedAt.contains("T"))
    }

    @Test
    fun bloodPressureIsExplicitNotContinuous() {
        val list = ObservationMapper.bloodPressure(
            "bp1", 120.0, 80.0, Instant.parse("2026-07-27T08:00:00Z"), "com.samsung.health"
        )
        assertEquals(2, list.size)
        assertEquals("explicit_supported_measurement", list[0].device["measurement_kind"])
        assertEquals("systolic_bp", list[0].metricType)
        assertEquals("diastolic_bp", list[1].metricType)
    }

    @Test
    fun glucoseMapsCapillaryVsInterstitialWithoutInventingTrend() {
        val t = Instant.parse("2026-08-24T12:00:00Z")
        val capillary = ObservationMapper.glucose("g1", 6.2, t, "com.freestyle.libre3", false)
        val interstitial = ObservationMapper.glucose("g2", 15.8, t, "com.freestyle.libre3", true)
        assertEquals("glucose_capillary", capillary.metricType)
        assertEquals("glucose_cgm_interstitial", interstitial.metricType)
        assertEquals("mmol/L", capillary.unit)
        assertEquals(null, capillary.trendDirection)
    }

    @Test
    fun safeLogRedactsTokenPatterns() {
        val redacted = PrivacyRedactor.redact("Authorization=Bearer abc.def token=xyz")
        assertTrue(!redacted.contains("abc.def"))
        assertTrue(!redacted.contains("xyz"))
        assertTrue(redacted.contains("[redacted]"))
    }

    @Test
    fun timezoneOffsetPreservedWhenProvided() {
        val t = Instant.parse("2026-07-27T12:00:00Z")
        val obs = ObservationMapper.heartRate(
            "r2", 70, t, "com.samsung.health", ZoneOffset.ofHours(-4)
        )
        assertTrue(obs.measuredAt.contains("-04:00"))
    }
}
