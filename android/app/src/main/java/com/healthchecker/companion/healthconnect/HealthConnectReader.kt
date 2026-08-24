package com.healthchecker.companion.healthconnect

import android.content.Context
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.changes.DeletionChange
import androidx.health.connect.client.changes.UpsertionChange
import androidx.health.connect.client.records.BloodGlucoseRecord
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
        val proposedTokenScope: String? = null,
        val latestByMetric: Map<String, String> = emptyMap(),
        val inventoryLatestByMetric: Map<String, String> = emptyMap(),
        val catchUpApplied: Boolean = false,
        val catchUpObservationCount: Int = 0
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
                proposedTokenScope = tokenScope,
                latestByMetric = latestFrom(observations)
            )
            fun latestFrom(observations: List<CompanionObservation>): Map<String, String> {
                val out = linkedMapOf<String, String>()
                observations.forEach { obs ->
                    val prior = out[obs.metricType]
                    if (prior == null || obs.measuredAt > prior) out[obs.metricType] = obs.measuredAt
                }
                return out
            }
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
            return enrichWithInventory(
                client,
                recordTypes,
                FetchResult(
                    observations = observations,
                    nextChangesToken = freshToken,
                    deletedRecordIds = emptyList(),
                    queryPerformed = true,
                    disposition = disposition,
                    partialPermissionWarning = partial,
                    grantedTypeCount = granted.size,
                    missingTypeCount = missing.size,
                    proposedTokenScope = scope,
                    latestByMetric = FetchResult.latestFrom(observations)
                )
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
            return enrichWithInventory(
                client,
                recordTypes,
                FetchResult(
                    observations = recovered,
                    nextChangesToken = freshToken,
                    deletedRecordIds = emptyList(),
                    queryPerformed = true,
                    disposition = disposition,
                    partialPermissionWarning = partial,
                    grantedTypeCount = granted.size,
                    missingTypeCount = missing.size,
                    proposedTokenScope = scope,
                    error = "changes_token_invalidated_reinitialized",
                    latestByMetric = FetchResult.latestFrom(recovered)
                )
            )
        }
        return enrichWithInventory(
            client,
            recordTypes,
            FetchResult(
                observations = observations,
                nextChangesToken = nextToken,
                deletedRecordIds = deleted,
                queryPerformed = true,
                disposition = disposition,
                partialPermissionWarning = partial,
                grantedTypeCount = granted.size,
                missingTypeCount = missing.size,
                proposedTokenScope = scope,
                latestByMetric = FetchResult.latestFrom(observations)
            )
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

    private suspend fun enrichWithInventory(
        client: HealthConnectClient,
        allowed: Set<KClass<out Record>>,
        result: FetchResult
    ): FetchResult {
        val inventory = try {
            inventoryLatest(client, allowed)
        } catch (_: Throwable) {
            emptyMap()
        }
        val fetchedLatest = FetchResult.latestFrom(result.observations)
        val need = FreshnessCatchUp.metricsNeedingCatchUp(inventory, fetchedLatest)
        var observations = result.observations
        var catchUpApplied = false
        if (need.isNotEmpty()) {
            val fetchedMax = fetchedLatest.values.maxOrNull()
            val start = FreshnessCatchUp.catchUpStart(Instant.now(), fetchedMax)
            val extra = try {
                catchUpNewest(client, start, allowed)
            } catch (_: Throwable) {
                emptyList()
            }
            if (extra.isNotEmpty()) {
                val seen = observations.map { it.sourceRecordId }.toHashSet()
                val extraOnly = extra.filter { it.sourceRecordId !in seen }
                if (extraOnly.isNotEmpty()) {
                    observations = result.observations + FreshnessCatchUp.capNewest(extraOnly)
                    catchUpApplied = true
                }
            }
        }
        val latest = FreshnessCatchUp.mergeLatest(fetchedLatest, FetchResult.latestFrom(observations), inventory)
        return result.copy(
            observations = observations,
            latestByMetric = latest,
            inventoryLatestByMetric = inventory,
            catchUpApplied = catchUpApplied,
            catchUpObservationCount = if (catchUpApplied) observations.size else 0
        )
    }

    private suspend fun inventoryLatest(
        client: HealthConnectClient,
        allowed: Set<KClass<out Record>>
    ): Map<String, String> {
        val out = linkedMapOf<String, String>()
        val filter = TimeRangeFilter.before(Instant.now().plusSeconds(1))
        suspend fun <T : Record> latestOf(type: KClass<T>) {
            if (type !in allowed) return
            val response = client.readRecords(
                ReadRecordsRequest(
                    recordType = type,
                    timeRangeFilter = filter,
                    ascendingOrder = false,
                    pageSize = 1
                )
            )
            val record = response.records.firstOrNull() ?: return
            mapRecord(record).forEach { obs ->
                val previous = out[obs.metricType]
                if (previous == null || obs.measuredAt > previous) out[obs.metricType] = obs.measuredAt
            }
        }
        latestOf(HeartRateRecord::class)
        latestOf(RestingHeartRateRecord::class)
        latestOf(OxygenSaturationRecord::class)
        latestOf(BloodPressureRecord::class)
        latestOf(StepsRecord::class)
        latestOf(WeightRecord::class)
        latestOf(BloodGlucoseRecord::class)
        latestOf(SleepSessionRecord::class)
        latestOf(ExerciseSessionRecord::class)
        return out
    }

    private suspend fun catchUpNewest(
        client: HealthConnectClient,
        start: Instant,
        allowed: Set<KClass<out Record>>
    ): List<CompanionObservation> {
        val end = Instant.now().plusSeconds(1)
        if (!start.isBefore(end)) return emptyList()
        val filter = TimeRangeFilter.between(start, end)
        val collected = mutableListOf<CompanionObservation>()
        suspend fun <T : Record> pages(type: KClass<T>) {
            if (type !in allowed) return
            var token: String? = null
            repeat(6) {
                val response = client.readRecords(
                    ReadRecordsRequest(
                        recordType = type,
                        timeRangeFilter = filter,
                        ascendingOrder = false,
                        pageSize = 50,
                        pageToken = token
                    )
                )
                response.records.forEach { collected += mapRecord(it) }
                token = response.pageToken
                if (token.isNullOrBlank()) return
                if (collected.size >= FreshnessCatchUp.CATCH_UP_MAX_OBSERVATIONS * 2) return
            }
        }
        pages(HeartRateRecord::class)
        pages(RestingHeartRateRecord::class)
        pages(OxygenSaturationRecord::class)
        pages(BloodPressureRecord::class)
        pages(StepsRecord::class)
        pages(WeightRecord::class)
        pages(BloodGlucoseRecord::class)
        pages(SleepSessionRecord::class)
        pages(ExerciseSessionRecord::class)
        return FreshnessCatchUp.capNewest(collected)
    }

    private suspend fun <T : Record> readAllRecords(
        client: HealthConnectClient,
        type: KClass<T>,
        filter: TimeRangeFilter,
        pageSize: Int = 1000,
        maxPages: Int = 15
    ): List<T> {
        val out = mutableListOf<T>()
        var token: String? = null
        repeat(maxPages) {
            val response = client.readRecords(
                ReadRecordsRequest(
                    recordType = type,
                    timeRangeFilter = filter,
                    ascendingOrder = true,
                    pageSize = pageSize,
                    pageToken = token
                )
            )
            @Suppress("UNCHECKED_CAST")
            out += response.records as List<T>
            token = response.pageToken
            if (token.isNullOrBlank()) return out
        }
        return out
    }

    private suspend fun readInitial(
        client: HealthConnectClient,
        start: Instant,
        allowed: Set<KClass<out Record>>
    ): List<CompanionObservation> {
        val filter = TimeRangeFilter.between(start, Instant.now().plusSeconds(1))
        val out = mutableListOf<CompanionObservation>()
        val types: List<KClass<out Record>> = listOf(
            HeartRateRecord::class,
            RestingHeartRateRecord::class,
            OxygenSaturationRecord::class,
            BloodPressureRecord::class,
            StepsRecord::class,
            WeightRecord::class,
            BloodGlucoseRecord::class,
            SleepSessionRecord::class,
            ExerciseSessionRecord::class,
        )
        for (type in types) {
            if (type !in allowed) continue
            readAllRecords(client, type, filter).forEach { record ->
                out += mapRecord(record)
            }
        }
        return out
    }

    private fun mapGlucose(record: BloodGlucoseRecord): CompanionObservation {
        val interstitial = record.specimenSource == BloodGlucoseRecord.SPECIMEN_SOURCE_INTERSTITIAL_FLUID
        return ObservationMapper.glucose(
            record.metadata.id,
            record.level.inMillimolesPerLiter,
            record.time,
            record.metadata.dataOrigin.packageName,
            interstitial,
            record.zoneOffset
        )
    }

    private fun mapSleepSession(record: SleepSessionRecord): List<CompanionObservation> {
        val origin = record.metadata.dataOrigin.packageName
        val zone = record.startZoneOffset
        val hours = java.time.Duration.between(record.startTime, record.endTime).toMinutes() / 60.0
        val out = mutableListOf(
            ObservationMapper.sleepDurationHours(
                record.metadata.id, hours, record.startTime, origin, zone
            )
        )
        var deep = 0L
        var rem = 0L
        var light = 0L
        var awake = 0L
        var firstSleep: Instant? = null
        record.stages.forEach { stage ->
            val minutes = java.time.Duration.between(stage.startTime, stage.endTime).toMinutes()
            when (stage.stage) {
                SleepSessionRecord.STAGE_TYPE_DEEP -> deep += minutes
                SleepSessionRecord.STAGE_TYPE_REM -> rem += minutes
                SleepSessionRecord.STAGE_TYPE_LIGHT -> light += minutes
                SleepSessionRecord.STAGE_TYPE_AWAKE, SleepSessionRecord.STAGE_TYPE_OUT_OF_BED -> awake += minutes
            }
            val sleeping = stage.stage == SleepSessionRecord.STAGE_TYPE_DEEP ||
                stage.stage == SleepSessionRecord.STAGE_TYPE_REM ||
                stage.stage == SleepSessionRecord.STAGE_TYPE_LIGHT ||
                stage.stage == SleepSessionRecord.STAGE_TYPE_SLEEPING
            if (sleeping && firstSleep == null) firstSleep = stage.startTime
        }
        if (deep > 0) {
            out += ObservationMapper.sleepStageMinutes(record.metadata.id, "deep_sleep_duration", deep.toDouble(), record.startTime, origin, zone)
        }
        if (rem > 0) {
            out += ObservationMapper.sleepStageMinutes(record.metadata.id, "rem_sleep_duration", rem.toDouble(), record.startTime, origin, zone)
        }
        if (light > 0) {
            out += ObservationMapper.sleepStageMinutes(record.metadata.id, "light_sleep_duration", light.toDouble(), record.startTime, origin, zone)
        }
        if (awake > 0) {
            out += ObservationMapper.sleepStageMinutes(record.metadata.id, "sleep_awake_duration", awake.toDouble(), record.startTime, origin, zone)
        }
        val latencyStart = firstSleep
        if (latencyStart != null) {
            val latency = java.time.Duration.between(record.startTime, latencyStart).toMinutes().toDouble()
            if (latency >= 0) {
                out += ObservationMapper.sleepStageMinutes(record.metadata.id, "sleep_latency", latency, record.startTime, origin, zone)
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
            is SleepSessionRecord -> mapSleepSession(record)
            is BloodGlucoseRecord -> listOf(mapGlucose(record))
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
