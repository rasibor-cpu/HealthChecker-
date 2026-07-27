package com.healthchecker.companion.healthconnect

import android.content.Context
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.changes.DeletionChange
import androidx.health.connect.client.changes.UpsertionChange
import androidx.health.connect.client.records.BloodPressureRecord
import androidx.health.connect.client.records.ExerciseSessionRecord
import androidx.health.connect.client.records.HeartRateRecord
import androidx.health.connect.client.records.OxygenSaturationRecord
import androidx.health.connect.client.records.RestingHeartRateRecord
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.records.StepsRecord
import androidx.health.connect.client.records.WeightRecord
import androidx.health.connect.client.request.ChangesTokenRequest
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.time.TimeRangeFilter
import com.healthchecker.companion.secure.SecurePrefs
import java.time.Instant
import java.time.temporal.ChronoUnit

/**
 * Incremental Health Connect reader.
 * Advances local change-token only after the caller reports durable host acknowledgement.
 */
class HealthConnectReader(
    private val context: Context,
    private val prefs: SecurePrefs
) {
    data class FetchResult(
        val observations: List<CompanionObservation>,
        val nextChangesToken: String?,
        val deletedRecordIds: List<String>,
        val permissionRequired: Boolean = false,
        val permissionDenied: Boolean = false,
        val error: String? = null
    )

    suspend fun fetchNew(initialHistoryDays: Long = 7): FetchResult {
        val capability = HealthConnectCapability(context).report()
        if (capability.availability != HealthConnectAvailability.READY) {
            return FetchResult(emptyList(), null, emptyList(), error = capability.message)
        }
        if (capability.permissionsMissing.isNotEmpty()) {
            return FetchResult(
                emptyList(),
                null,
                emptyList(),
                permissionRequired = true,
                error = "permissions_missing"
            )
        }

        val client = HealthConnectClient.getOrCreate(context)
        val recordTypes = setOf(
            HeartRateRecord::class,
            RestingHeartRateRecord::class,
            OxygenSaturationRecord::class,
            BloodPressureRecord::class,
            SleepSessionRecord::class,
            StepsRecord::class,
            ExerciseSessionRecord::class,
            WeightRecord::class
        )

        var token = prefs.getChangesToken()
        val observations = mutableListOf<CompanionObservation>()
        val deleted = mutableListOf<String>()

        if (token.isNullOrBlank()) {
            // Initial bounded history (not full lifetime scan)
            val start = Instant.now().minus(initialHistoryDays, ChronoUnit.DAYS)
            observations += readInitial(client, start)
            token = client.getChangesToken(ChangesTokenRequest(recordTypes))
            // Do not persist token until host ack — return proposed token only
            return FetchResult(observations, token, deleted)
        }

        var nextToken: String? = token
        var pageToken: String? = token
        try {
            while (pageToken != null) {
                val response = client.getChanges(pageToken)
                response.changes.forEach { change ->
                    when (change) {
                        is UpsertionChange -> observations += mapRecord(change.record)
                        is DeletionChange -> deleted += change.recordId
                    }
                }
                nextToken = response.nextChangesToken
                pageToken = if (response.hasMore) response.nextChangesToken else null
            }
        } catch (t: Throwable) {
            // Changes token invalidation — safe recovery via bounded re-init (do not fabricate)
            prefs.setChangesToken("")
            val start = Instant.now().minus(initialHistoryDays, ChronoUnit.DAYS)
            val recovered = readInitial(client, start)
            val freshToken = client.getChangesToken(ChangesTokenRequest(recordTypes))
            return FetchResult(
                observations = recovered,
                nextChangesToken = freshToken,
                deletedRecordIds = emptyList(),
                error = "changes_token_invalidated_reinitialized"
            )
        }
        return FetchResult(observations, nextToken, deleted)
    }

    fun acknowledgeCursor(nextChangesToken: String?) {
        if (!nextChangesToken.isNullOrBlank()) {
            prefs.setChangesToken(nextChangesToken)
        }
    }

    private suspend fun readInitial(
        client: HealthConnectClient,
        start: Instant
    ): List<CompanionObservation> {
        val out = mutableListOf<CompanionObservation>()
        val filter = TimeRangeFilter.between(start, Instant.now())

        client.readRecords(ReadRecordsRequest(HeartRateRecord::class, timeRangeFilter = filter))
            .records.forEach { r ->
                // HeartRate Sample has no zoneOffset; use series startZoneOffset.
                val zone = r.startZoneOffset
                r.samples.forEachIndexed { idx, sample ->
                    out += ObservationMapper.heartRate(
                        recordId = r.metadata.id + ":$idx",
                        bpm = sample.beatsPerMinute,
                        time = sample.time,
                        dataOrigin = r.metadata.dataOrigin.packageName,
                        zoneOffset = zone
                    )
                }
            }
        client.readRecords(ReadRecordsRequest(RestingHeartRateRecord::class, timeRangeFilter = filter))
            .records.forEach { r ->
                out += ObservationMapper.restingHeartRate(
                    r.metadata.id, r.beatsPerMinute, r.time, r.metadata.dataOrigin.packageName, r.zoneOffset
                )
            }
        client.readRecords(ReadRecordsRequest(OxygenSaturationRecord::class, timeRangeFilter = filter))
            .records.forEach { r ->
                out += ObservationMapper.spo2(
                    r.metadata.id, r.percentage.value, r.time, r.metadata.dataOrigin.packageName, r.zoneOffset
                )
            }
        client.readRecords(ReadRecordsRequest(BloodPressureRecord::class, timeRangeFilter = filter))
            .records.forEach { r ->
                out += ObservationMapper.bloodPressure(
                    r.metadata.id,
                    r.systolic.inMillimetersOfMercury,
                    r.diastolic.inMillimetersOfMercury,
                    r.time,
                    r.metadata.dataOrigin.packageName,
                    r.zoneOffset
                )
            }
        client.readRecords(ReadRecordsRequest(StepsRecord::class, timeRangeFilter = filter))
            .records.forEach { r ->
                out += ObservationMapper.steps(
                    r.metadata.id, r.count, r.startTime, r.metadata.dataOrigin.packageName, r.startZoneOffset
                )
            }
        client.readRecords(ReadRecordsRequest(WeightRecord::class, timeRangeFilter = filter))
            .records.forEach { r ->
                out += ObservationMapper.weightKg(
                    r.metadata.id, r.weight.inKilograms, r.time, r.metadata.dataOrigin.packageName, r.zoneOffset
                )
            }
        client.readRecords(ReadRecordsRequest(SleepSessionRecord::class, timeRangeFilter = filter))
            .records.forEach { r ->
                val hours = java.time.Duration.between(r.startTime, r.endTime).toMinutes() / 60.0
                out += ObservationMapper.sleepDurationHours(
                    r.metadata.id, hours, r.startTime, r.metadata.dataOrigin.packageName, r.startZoneOffset
                )
            }
        // Exercise sessions: map duration minutes as exercise_minutes
        client.readRecords(ReadRecordsRequest(ExerciseSessionRecord::class, timeRangeFilter = filter))
            .records.forEach { r ->
                val minutes = java.time.Duration.between(r.startTime, r.endTime).toMinutes().toDouble()
                out += CompanionObservation(
                    observationId = java.util.UUID.nameUUIDFromBytes(("ex:" + r.metadata.id).toByteArray()).toString(),
                    sourceRecordId = r.metadata.id,
                    metricType = "exercise_minutes",
                    value = minutes,
                    unit = "min",
                    measuredAt = ObservationMapper.instantToIso(r.startTime, r.startZoneOffset),
                    receivedAt = ObservationMapper.instantToIso(Instant.now()),
                    device = mapOf("data_origin" to r.metadata.dataOrigin.packageName)
                )
            }
        return out
    }

    private fun mapRecord(record: Any): List<CompanionObservation> {
        return when (record) {
            is HeartRateRecord -> record.samples.mapIndexed { idx, sample ->
                ObservationMapper.heartRate(
                    record.metadata.id + ":$idx",
                    sample.beatsPerMinute,
                    sample.time,
                    record.metadata.dataOrigin.packageName,
                    record.startZoneOffset
                )
            }
            is RestingHeartRateRecord -> listOf(
                ObservationMapper.restingHeartRate(
                    record.metadata.id, record.beatsPerMinute, record.time,
                    record.metadata.dataOrigin.packageName, record.zoneOffset
                )
            )
            is OxygenSaturationRecord -> listOf(
                ObservationMapper.spo2(
                    record.metadata.id, record.percentage.value, record.time,
                    record.metadata.dataOrigin.packageName, record.zoneOffset
                )
            )
            is BloodPressureRecord -> ObservationMapper.bloodPressure(
                record.metadata.id,
                record.systolic.inMillimetersOfMercury,
                record.diastolic.inMillimetersOfMercury,
                record.time,
                record.metadata.dataOrigin.packageName,
                record.zoneOffset
            )
            is StepsRecord -> listOf(
                ObservationMapper.steps(
                    record.metadata.id, record.count, record.startTime,
                    record.metadata.dataOrigin.packageName, record.startZoneOffset
                )
            )
            is WeightRecord -> listOf(
                ObservationMapper.weightKg(
                    record.metadata.id, record.weight.inKilograms, record.time,
                    record.metadata.dataOrigin.packageName, record.zoneOffset
                )
            )
            is SleepSessionRecord -> {
                val hours = java.time.Duration.between(record.startTime, record.endTime).toMinutes() / 60.0
                listOf(
                    ObservationMapper.sleepDurationHours(
                        record.metadata.id, hours, record.startTime,
                        record.metadata.dataOrigin.packageName, record.startZoneOffset
                    )
                )
            }
            is ExerciseSessionRecord -> {
                val minutes = java.time.Duration.between(record.startTime, record.endTime).toMinutes().toDouble()
                listOf(
                    CompanionObservation(
                        observationId = java.util.UUID.nameUUIDFromBytes(("ex:" + record.metadata.id).toByteArray()).toString(),
                        sourceRecordId = record.metadata.id,
                        metricType = "exercise_minutes",
                        value = minutes,
                        unit = "min",
                        measuredAt = ObservationMapper.instantToIso(record.startTime, record.startZoneOffset),
                        receivedAt = ObservationMapper.instantToIso(Instant.now()),
                        device = mapOf("data_origin" to record.metadata.dataOrigin.packageName)
                    )
                )
            }
            else -> emptyList()
        }
    }
}
