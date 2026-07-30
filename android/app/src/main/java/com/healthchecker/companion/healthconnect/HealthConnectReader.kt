package com.healthchecker.companion.healthconnect

import android.content.Context
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.changes.DeletionChange
import androidx.health.connect.client.changes.UpsertionChange
import androidx.health.connect.client.records.BloodPressureRecord
import androidx.health.connect.client.records.ExerciseSessionRecord
import androidx.health.connect.client.records.HeartRateRecord
import androidx.health.connect.client.records.OxygenSaturationRecord
import androidx.health.connect.client.records.Record
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
import kotlin.reflect.KClass

/**
 * Incremental Health Connect reader (HC-306I-R3).
 *
 * Queries only currently granted supported record types.
 * Advances local change-token + scope only after durable host acknowledgement.
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
        val error: String? = null,
        val queryPerformed: Boolean = false,
        val disposition: QueryDisposition = QueryDisposition.NOT_PERFORMED_FATAL,
        val partialPermissionWarning: Boolean = false,
        val grantedTypeCount: Int = 0,
        val missingTypeCount: Int = 0,
        /** Scope fingerprint that must be persisted with [nextChangesToken] after durable ack. */
        val proposedTokenScope: String? = null
    ) {
        companion object {
            fun fromPending(
                observations: List<CompanionObservation>,
                nextChangesToken: String?,
                deletedRecordIds: List<String>,
                tokenScope: String?,
                partialPermissionWarning: Boolean = false
            ): FetchResult = FetchResult(
                observations = observations,
                nextChangesToken = nextChangesToken,
                deletedRecordIds = deletedRecordIds,
                queryPerformed = true,
                disposition = if (partialPermissionWarning) {
                    QueryDisposition.PERFORMED_PARTIAL
                } else {
                    QueryDisposition.PERFORMED_OK
                },
                partialPermissionWarning = partialPermissionWarning,
                proposedTokenScope = tokenScope
            )
        }
    }

    suspend fun fetchNew(initialHistoryDays: Long = 7): FetchResult {
        val capability = HealthConnectCapability(context).report()
        if (capability.availability != HealthConnectAvailability.READY) {
            return fatal(capability.message, granted = 0, missing = capability.permissionsMissing.size)
        }

        val granted = capability.permissionsGranted
        val missing = capability.permissionsMissing
        if (granted.isEmpty()) {
            return fatal(
                "no_granted_permissions",
                permissionRequired = true,
                granted = 0,
                missing = missing.size
            )
        }

        val recordTypes = GrantedRecordCatalog.recordClasses(granted)
        val scope = GrantedRecordCatalog.scopeFingerprint(granted)
        val partial = missing.isNotEmpty()
        val disposition =
            if (partial) QueryDisposition.PERFORMED_PARTIAL else QueryDisposition.PERFORMED_OK

        val client = HealthConnectClient.getOrCreate(context)
        val token = prefs.getChangesToken()
        val persistedScope = prefs.getChangesTokenScope()
        val scopeOk = GrantedRecordCatalog.scopeMatches(persistedScope, scope)
        val needsReinit =
            token.isNullOrBlank() ||
                persistedScope.isNullOrBlank() ||
                !GrantedRecordCatalog.isValidScopeFingerprint(persistedScope) ||
                !scopeOk

        if (needsReinit) {
            val start = Instant.now().minus(initialHistoryDays, ChronoUnit.DAYS)
            val observations = readInitial(client, start, recordTypes)
            val freshToken = client.getChangesToken(ChangesTokenRequest(recordTypes))
            return FetchResult(
                observations = observations,
                nextChangesToken = freshToken,
                deletedRecordIds = emptyList(),
                queryPerformed = true,
                disposition = disposition,
                partialPermissionWarning = partial,
                grantedTypeCount = granted.size,
                missingTypeCount = missing.size,
                proposedTokenScope = scope
            )
        }

        val observations = mutableListOf<CompanionObservation>()
        val deleted = mutableListOf<String>()
        var nextToken: String? = token
        var pageToken: String? = token
        try {
            while (pageToken != null) {
                val response = client.getChanges(pageToken)
                response.changes.forEach { change ->
                    when (change) {
                        is UpsertionChange -> {
                            // Only map granted types (revoked types must never surface).
                            if (change.record::class in recordTypes) {
                                observations += mapRecord(change.record)
                            }
                        }
                        is DeletionChange -> deleted += change.recordId
                    }
                }
                nextToken = response.nextChangesToken
                pageToken = if (response.hasMore) response.nextChangesToken else null
            }
        } catch (t: Throwable) {
            // Changes token invalidation — bounded re-init for currently granted types only.
            prefs.clearChangesCursor()
            val start = Instant.now().minus(initialHistoryDays, ChronoUnit.DAYS)
            val recovered = readInitial(client, start, recordTypes)
            val freshToken = client.getChangesToken(ChangesTokenRequest(recordTypes))
            return FetchResult(
                observations = recovered,
                nextChangesToken = freshToken,
                deletedRecordIds = emptyList(),
                queryPerformed = true,
                disposition = disposition,
                partialPermissionWarning = partial,
                grantedTypeCount = granted.size,
                missingTypeCount = missing.size,
                proposedTokenScope = scope,
                error = "changes_token_invalidated_reinitialized"
            )
        }
        return FetchResult(
            observations = observations,
            nextChangesToken = nextToken,
            deletedRecordIds = deleted,
            queryPerformed = true,
            disposition = disposition,
            partialPermissionWarning = partial,
            grantedTypeCount = granted.size,
            missingTypeCount = missing.size,
            proposedTokenScope = scope
        )
    }

    /**
     * Persist token + scope only after durable host acknowledgement.
     * Both must be non-blank; otherwise fail closed (leave prior cursor untouched).
     */
    fun acknowledgeCursor(nextChangesToken: String?, tokenScope: String?): Boolean {
        if (nextChangesToken.isNullOrBlank() || tokenScope.isNullOrBlank()) return false
        if (!GrantedRecordCatalog.isValidScopeFingerprint(tokenScope)) return false
        return prefs.persistChangesCursor(nextChangesToken, tokenScope)
    }

    private fun fatal(
        error: String,
        permissionRequired: Boolean = false,
        granted: Int,
        missing: Int
    ): FetchResult = FetchResult(
        observations = emptyList(),
        nextChangesToken = null,
        deletedRecordIds = emptyList(),
        permissionRequired = permissionRequired,
        error = error,
        queryPerformed = false,
        disposition = QueryDisposition.NOT_PERFORMED_FATAL,
        grantedTypeCount = granted,
        missingTypeCount = missing,
        proposedTokenScope = null
    )

    private suspend fun readInitial(
        client: HealthConnectClient,
        start: Instant,
        allowed: Set<KClass<out Record>>
    ): List<CompanionObservation> {
        val out = mutableListOf<CompanionObservation>()
        val filter = TimeRangeFilter.between(start, Instant.now())

        if (HeartRateRecord::class in allowed) {
            client.readRecords(ReadRecordsRequest(HeartRateRecord::class, timeRangeFilter = filter))
                .records.forEach { r ->
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
        }
        if (RestingHeartRateRecord::class in allowed) {
            client.readRecords(
                ReadRecordsRequest(RestingHeartRateRecord::class, timeRangeFilter = filter)
            ).records.forEach { r ->
                out += ObservationMapper.restingHeartRate(
                    r.metadata.id, r.beatsPerMinute, r.time, r.metadata.dataOrigin.packageName, r.zoneOffset
                )
            }
        }
        if (OxygenSaturationRecord::class in allowed) {
            client.readRecords(
                ReadRecordsRequest(OxygenSaturationRecord::class, timeRangeFilter = filter)
            ).records.forEach { r ->
                out += ObservationMapper.spo2(
                    r.metadata.id, r.percentage.value, r.time, r.metadata.dataOrigin.packageName, r.zoneOffset
                )
            }
        }
        if (BloodPressureRecord::class in allowed) {
            client.readRecords(
                ReadRecordsRequest(BloodPressureRecord::class, timeRangeFilter = filter)
            ).records.forEach { r ->
                out += ObservationMapper.bloodPressure(
                    r.metadata.id,
                    r.systolic.inMillimetersOfMercury,
                    r.diastolic.inMillimetersOfMercury,
                    r.time,
                    r.metadata.dataOrigin.packageName,
                    r.zoneOffset
                )
            }
        }
        if (StepsRecord::class in allowed) {
            client.readRecords(ReadRecordsRequest(StepsRecord::class, timeRangeFilter = filter))
                .records.forEach { r ->
                    out += ObservationMapper.steps(
                        r.metadata.id, r.count, r.startTime, r.metadata.dataOrigin.packageName, r.startZoneOffset
                    )
                }
        }
        if (WeightRecord::class in allowed) {
            client.readRecords(ReadRecordsRequest(WeightRecord::class, timeRangeFilter = filter))
                .records.forEach { r ->
                    out += ObservationMapper.weightKg(
                        r.metadata.id, r.weight.inKilograms, r.time, r.metadata.dataOrigin.packageName, r.zoneOffset
                    )
                }
        }
        if (SleepSessionRecord::class in allowed) {
            client.readRecords(
                ReadRecordsRequest(SleepSessionRecord::class, timeRangeFilter = filter)
            ).records.forEach { r ->
                val hours = java.time.Duration.between(r.startTime, r.endTime).toMinutes() / 60.0
                out += ObservationMapper.sleepDurationHours(
                    r.metadata.id, hours, r.startTime, r.metadata.dataOrigin.packageName, r.startZoneOffset
                )
            }
        }
        if (ExerciseSessionRecord::class in allowed) {
            client.readRecords(
                ReadRecordsRequest(ExerciseSessionRecord::class, timeRangeFilter = filter)
            ).records.forEach { r ->
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
