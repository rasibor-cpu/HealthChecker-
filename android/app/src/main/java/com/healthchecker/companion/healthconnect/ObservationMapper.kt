package com.healthchecker.companion.healthconnect

import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.UUID

/**
 * Map Health Connect record fields into HC-302 observation maps.
 * Pure mapping helpers are unit-testable without a device.
 */
object ObservationMapper {
    private val isoInstant = DateTimeFormatter.ISO_INSTANT
    private val isoOffset = DateTimeFormatter.ISO_OFFSET_DATE_TIME

    fun instantToIso(instant: Instant, zoneOffset: ZoneOffset? = null): String {
        return if (zoneOffset != null) {
            isoOffset.format(instant.atOffset(zoneOffset))
        } else {
            isoInstant.format(instant)
        }
    }

    fun heartRate(
        recordId: String,
        bpm: Long,
        time: Instant,
        dataOrigin: String,
        zoneOffset: ZoneOffset? = null
    ): CompanionObservation = CompanionObservation(
        observationId = UUID.nameUUIDFromBytes(("hr:" + recordId).toByteArray()).toString(),
        sourceRecordId = recordId,
        metricType = "heart_rate",
        value = bpm.toDouble(),
        unit = "bpm",
        measuredAt = instantToIso(time, zoneOffset),
        receivedAt = instantToIso(Instant.now()),
        acquisitionMode = "DELAYED",
        device = mapOf("data_origin" to dataOrigin)
    )

    fun restingHeartRate(
        recordId: String,
        bpm: Long,
        time: Instant,
        dataOrigin: String,
        zoneOffset: ZoneOffset? = null
    ): CompanionObservation = CompanionObservation(
        observationId = UUID.nameUUIDFromBytes(("rhr:" + recordId).toByteArray()).toString(),
        sourceRecordId = recordId,
        metricType = "resting_hr",
        value = bpm.toDouble(),
        unit = "bpm",
        measuredAt = instantToIso(time, zoneOffset),
        receivedAt = instantToIso(Instant.now()),
        acquisitionMode = "DELAYED",
        device = mapOf("data_origin" to dataOrigin)
    )

    fun spo2(
        recordId: String,
        percent: Double,
        time: Instant,
        dataOrigin: String,
        zoneOffset: ZoneOffset? = null
    ): CompanionObservation = CompanionObservation(
        observationId = UUID.nameUUIDFromBytes(("spo2:" + recordId).toByteArray()).toString(),
        sourceRecordId = recordId,
        metricType = "oxygen_saturation",
        value = percent,
        unit = "%",
        measuredAt = instantToIso(time, zoneOffset),
        receivedAt = instantToIso(Instant.now()),
        acquisitionMode = "DELAYED",
        device = mapOf("data_origin" to dataOrigin)
    )

    fun bloodPressure(
        recordId: String,
        systolic: Double,
        diastolic: Double,
        time: Instant,
        dataOrigin: String,
        zoneOffset: ZoneOffset? = null
    ): List<CompanionObservation> {
        // BP is an explicit measurement, not continuous.
        val sys = CompanionObservation(
            observationId = UUID.nameUUIDFromBytes(("sbp:" + recordId).toByteArray()).toString(),
            sourceRecordId = recordId + ":systolic",
            metricType = "systolic_bp",
            value = systolic,
            unit = "mmHg",
            measuredAt = instantToIso(time, zoneOffset),
            receivedAt = instantToIso(Instant.now()),
            acquisitionMode = "DELAYED",
            device = mapOf(
                "data_origin" to dataOrigin,
                "measurement_kind" to "explicit_supported_measurement"
            )
        )
        val dia = CompanionObservation(
            observationId = UUID.nameUUIDFromBytes(("dbp:" + recordId).toByteArray()).toString(),
            sourceRecordId = recordId + ":diastolic",
            metricType = "diastolic_bp",
            value = diastolic,
            unit = "mmHg",
            measuredAt = instantToIso(time, zoneOffset),
            receivedAt = instantToIso(Instant.now()),
            acquisitionMode = "DELAYED",
            device = mapOf(
                "data_origin" to dataOrigin,
                "measurement_kind" to "explicit_supported_measurement"
            )
        )
        return listOf(sys, dia)
    }

    fun steps(
        recordId: String,
        count: Long,
        start: Instant,
        dataOrigin: String,
        zoneOffset: ZoneOffset? = null
    ): CompanionObservation = CompanionObservation(
        observationId = UUID.nameUUIDFromBytes(("steps:" + recordId).toByteArray()).toString(),
        sourceRecordId = recordId,
        metricType = "steps",
        value = count.toDouble(),
        unit = "count",
        measuredAt = instantToIso(start, zoneOffset),
        receivedAt = instantToIso(Instant.now()),
        acquisitionMode = "DELAYED",
        device = mapOf("data_origin" to dataOrigin)
    )

    fun weightKg(
        recordId: String,
        kg: Double,
        time: Instant,
        dataOrigin: String,
        zoneOffset: ZoneOffset? = null
    ): CompanionObservation = CompanionObservation(
        observationId = UUID.nameUUIDFromBytes(("wt:" + recordId).toByteArray()).toString(),
        sourceRecordId = recordId,
        metricType = "weight",
        value = kg,
        unit = "kg",
        measuredAt = instantToIso(time, zoneOffset),
        receivedAt = instantToIso(Instant.now()),
        acquisitionMode = "DELAYED",
        device = mapOf("data_origin" to dataOrigin)
    )

    fun sleepDurationHours(
        recordId: String,
        hours: Double,
        start: Instant,
        dataOrigin: String,
        zoneOffset: ZoneOffset? = null
    ): CompanionObservation = CompanionObservation(
        observationId = UUID.nameUUIDFromBytes(("sleep:" + recordId).toByteArray()).toString(),
        sourceRecordId = recordId,
        metricType = "sleep_duration",
        value = hours,
        unit = "h",
        measuredAt = instantToIso(start, zoneOffset),
        receivedAt = instantToIso(Instant.now()),
        acquisitionMode = "DELAYED",
        device = mapOf("data_origin" to dataOrigin, "session_based" to "true")
    )

    /** Reject ECG fabrication — always empty for HC-303A. */
    fun ecgUnsupported(): List<CompanionObservation> = emptyList()

    fun zoneOffsetHint(): ZoneOffset = ZoneOffset.UTC
}
