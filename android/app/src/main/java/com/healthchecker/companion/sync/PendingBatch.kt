package com.healthchecker.companion.sync

import com.healthchecker.companion.healthconnect.CompanionObservation
import com.healthchecker.companion.host.DeliveryEnvelope
import org.json.JSONArray
import org.json.JSONObject
import java.nio.charset.StandardCharsets
import java.util.UUID

/**
 * Stable retry state for HC-303B / HC-306I-R7.
 *
 * Legacy installs persisted a monolithic batch. R7 can migrate that payload into a bounded
 * multi-chunk delivery plan without re-querying Health Connect or changing pairing state.
 */
data class PendingBatch(
    val batchId: String,
    val nonce: String,
    val observationsJson: String,
    val nextChangesToken: String?,
    val deletedRecordIdsJson: String,
    val createdAtEpochMs: Long,
    /** Privacy-safe granted-type scope fingerprint committed with the token after ack. */
    val tokenScope: String? = null,
    /** Survives retry so partial-grant warning is not cleared by fromPending. */
    val partialPermissionWarning: Boolean = false,
    /** Complete bounded delivery plan persisted before any network send. */
    val chunksJson: String? = null,
    val activeChunkIndex: Int = 0,
    val healthConnectStatusJson: String? = null,
    val permissionsJson: String? = null,
    val workmanagerJson: String? = null,
    val payloadByteCeiling: Int? = null,
    val observationCountCeiling: Int? = null,
) {
    data class Chunk(
        val index: Int,
        val batchId: String,
        val nonce: String,
        val observationsJson: String,
        val deletedRecordIdsJson: String,
        val nextChangesToken: String?,
        val queuedObservations: Int,
        val measuredPayloadBytes: Int,
    ) {
        fun observations(): List<CompanionObservation> = observationsFromJson(observationsJson)
        fun deletedRecordIds(): List<String> = deletedIdsFromJson(deletedRecordIdsJson)
    }

    fun observations(): List<CompanionObservation> = observationsFromJson(observationsJson)

    fun deletedRecordIds(): List<String> = deletedIdsFromJson(deletedRecordIdsJson)

    fun hasBoundedPlan(): Boolean = !chunksJson.isNullOrBlank()

    fun chunks(): List<Chunk> {
        val raw = chunksJson ?: return emptyList()
        val arr = JSONArray(raw)
        val out = mutableListOf<Chunk>()
        for (i in 0 until arr.length()) {
            val o = arr.getJSONObject(i)
            out += Chunk(
                index = o.getInt("index"),
                batchId = o.getString("batch_id"),
                nonce = o.getString("nonce"),
                observationsJson = o.getString("observations_json"),
                deletedRecordIdsJson = o.optString("deleted_record_ids_json", "[]"),
                nextChangesToken = if (o.isNull("next_changes_token")) null else o.optString("next_changes_token"),
                queuedObservations = o.optInt("queued_observations", JSONArray(o.getString("observations_json")).length()),
                measuredPayloadBytes = o.optInt("measured_payload_bytes", 0),
            )
        }
        return out
    }

    fun activeChunk(): Chunk? {
        val all = chunks()
        return if (all.isEmpty()) null else all.getOrNull(activeChunkIndex)
    }

    fun isFinalChunk(): Boolean {
        val all = chunks()
        return all.isNotEmpty() && activeChunkIndex == all.lastIndex
    }

    fun remainingObservationCount(): Int =
        chunks().drop(activeChunkIndex).sumOf { JSONArray(it.observationsJson).length() }

    fun advanceChunk(): PendingBatch? {
        if (!hasBoundedPlan()) return null
        val next = activeChunkIndex + 1
        return if (next >= chunks().size) null else copy(activeChunkIndex = next)
    }

    companion object {
        const val DEFAULT_MAX_OBSERVATIONS_PER_CHUNK = 180
        const val DEFAULT_MAX_PAYLOAD_BYTES = 480_000

        fun create(
            observations: List<CompanionObservation>,
            nextChangesToken: String?,
            deletedRecordIds: List<String>,
            tokenScope: String? = null,
            partialPermissionWarning: Boolean = false,
            nowEpochMs: Long = System.currentTimeMillis()
        ): PendingBatch {
            val arr = serializeObservations(observations)
            val deleted = JSONArray()
            deletedRecordIds.forEach { deleted.put(it) }
            return PendingBatch(
                batchId = UUID.randomUUID().toString(),
                nonce = UUID.randomUUID().toString(),
                observationsJson = arr.toString(),
                nextChangesToken = nextChangesToken,
                deletedRecordIdsJson = deleted.toString(),
                createdAtEpochMs = nowEpochMs,
                tokenScope = tokenScope,
                partialPermissionWarning = partialPermissionWarning,
            )
        }

        fun createBoundedPlan(
            rootBatchId: String = UUID.randomUUID().toString(),
            rootNonce: String = UUID.randomUUID().toString(),
            observationsJson: String,
            nextChangesToken: String?,
            deletedRecordIdsJson: String,
            tokenScope: String?,
            partialPermissionWarning: Boolean,
            healthConnectStatusJson: String,
            permissionsJson: String,
            workmanagerJson: String,
            nowEpochMs: Long = System.currentTimeMillis(),
            maxObservationsPerChunk: Int = DEFAULT_MAX_OBSERVATIONS_PER_CHUNK,
            maxPayloadBytes: Int = DEFAULT_MAX_PAYLOAD_BYTES,
        ): PendingBatch {
            require(maxObservationsPerChunk in 1..199) { "maxObservationsPerChunk_must_be_lt_200" }
            require(maxPayloadBytes in 1 until 512_000) { "maxPayloadBytes_must_be_lt_512000" }
            val fullObservations = JSONArray(observationsJson)
            val deleted = JSONArray(deletedRecordIdsJson)
            val status = JSONObject(healthConnectStatusJson)
            val permissions = JSONObject(permissionsJson)
            val workmanager = JSONObject(workmanagerJson)

            val chunks = mutableListOf<Chunk>()
            if (fullObservations.length() == 0) {
                chunks += buildChunk(
                    rootBatchId = rootBatchId,
                    rootNonce = rootNonce,
                    index = 0,
                    observations = JSONArray(),
                    deleted = deleted,
                    nextChangesToken = nextChangesToken,
                    healthConnectStatus = status,
                    permissions = permissions,
                    workmanager = workmanager,
                )
            }
            var start = 0
            while (start < fullObservations.length()) {
                val chunkArray = JSONArray()
                var end = start
                while (end < fullObservations.length()) {
                    val candidate = JSONArray(chunkArray.toString())
                    candidate.put(fullObservations.getJSONObject(end))
                    if (candidate.length() > maxObservationsPerChunk) {
                        break
                    }
                    val provisionalChunk = buildChunk(
                        rootBatchId = rootBatchId,
                        rootNonce = rootNonce,
                        index = chunks.size,
                        observations = candidate,
                        deleted = deleted,
                        nextChangesToken = nextChangesToken,
                        healthConnectStatus = status,
                        permissions = permissions,
                        workmanager = workmanager,
                    )
                    if (provisionalChunk.measuredPayloadBytes > maxPayloadBytes) {
                        break
                    }
                    chunkArray.put(fullObservations.getJSONObject(end))
                    end += 1
                }
                if (chunkArray.length() == 0) {
                    val single = JSONArray().put(fullObservations.getJSONObject(start))
                    val oversized = buildChunk(
                        rootBatchId = rootBatchId,
                        rootNonce = rootNonce,
                        index = chunks.size,
                        observations = single,
                        deleted = deleted,
                        nextChangesToken = nextChangesToken,
                        healthConnectStatus = status,
                        permissions = permissions,
                        workmanager = workmanager,
                    )
                    if (oversized.measuredPayloadBytes > maxPayloadBytes) {
                        throw IllegalArgumentException("single_observation_exceeds_bounded_payload_limit")
                    }
                    chunkArray.put(fullObservations.getJSONObject(start))
                    end = start + 1
                }
                chunks += buildChunk(
                    rootBatchId = rootBatchId,
                    rootNonce = rootNonce,
                    index = chunks.size,
                    observations = chunkArray,
                    deleted = deleted,
                    nextChangesToken = nextChangesToken,
                    healthConnectStatus = status,
                    permissions = permissions,
                    workmanager = workmanager,
                )
                start = end
            }

            val exactChunks = chunks.mapIndexed { idx, chunk ->
                val isFinal = idx == chunks.lastIndex
                val observations = JSONArray(chunk.observationsJson)
                val actual = buildChunk(
                    rootBatchId = rootBatchId,
                    rootNonce = rootNonce,
                    index = idx,
                    observations = observations,
                    deleted = if (isFinal) deleted else JSONArray(),
                    nextChangesToken = if (isFinal) nextChangesToken else null,
                    healthConnectStatus = status,
                    permissions = permissions,
                    workmanager = workmanager,
                )
                require(actual.measuredPayloadBytes < 512_000)
                require(JSONArray(actual.observationsJson).length() < 200)
                actual
            }

            return PendingBatch(
                batchId = rootBatchId,
                nonce = rootNonce,
                observationsJson = observationsJson,
                nextChangesToken = nextChangesToken,
                deletedRecordIdsJson = deletedRecordIdsJson,
                createdAtEpochMs = nowEpochMs,
                tokenScope = tokenScope,
                partialPermissionWarning = partialPermissionWarning,
                chunksJson = JSONArray().apply {
                    exactChunks.forEach { put(chunkToJsonObject(it)) }
                }.toString(),
                activeChunkIndex = 0,
                healthConnectStatusJson = status.toString(),
                permissionsJson = permissions.toString(),
                workmanagerJson = workmanager.toString(),
                payloadByteCeiling = maxPayloadBytes,
                observationCountCeiling = maxObservationsPerChunk,
            )
        }

        fun fromJson(raw: String): PendingBatch? {
            return runCatching {
                val o = JSONObject(raw)
                val batchId = o.getString("batch_id")
                val nonce = o.getString("nonce")
                val observationsJson = o.getString("observations_json")
                require(batchId.isNotBlank() && nonce.isNotBlank())
                JSONArray(observationsJson)
                val deletedJson = o.optString("deleted_record_ids_json", "[]")
                JSONArray(deletedJson)
                val chunksJson = if (o.isNull("chunks_json")) null else o.optString("chunks_json").ifBlank { null }
                if (!chunksJson.isNullOrBlank()) {
                    val chunks = JSONArray(chunksJson)
                    require(chunks.length() > 0)
                    for (i in 0 until chunks.length()) {
                        val c = chunks.getJSONObject(i)
                        require(c.getString("batch_id").isNotBlank())
                        require(c.getString("nonce").isNotBlank())
                        JSONArray(c.getString("observations_json"))
                        JSONArray(c.optString("deleted_record_ids_json", "[]"))
                    }
                }
                PendingBatch(
                    batchId = batchId,
                    nonce = nonce,
                    observationsJson = observationsJson,
                    nextChangesToken = if (o.isNull("next_changes_token")) null else o.optString("next_changes_token"),
                    deletedRecordIdsJson = deletedJson,
                    createdAtEpochMs = o.optLong("created_at_epoch_ms", 0L),
                    tokenScope = if (o.isNull("token_scope")) null else o.optString("token_scope").ifBlank { null },
                    partialPermissionWarning = o.optBoolean("partial_permission_warning", false),
                    chunksJson = chunksJson,
                    activeChunkIndex = o.optInt("active_chunk_index", 0),
                    healthConnectStatusJson = if (o.isNull("health_connect_status_json")) null else o.optString("health_connect_status_json").ifBlank { null },
                    permissionsJson = if (o.isNull("permissions_json")) null else o.optString("permissions_json").ifBlank { null },
                    workmanagerJson = if (o.isNull("workmanager_json")) null else o.optString("workmanager_json").ifBlank { null },
                    payloadByteCeiling = if (o.has("payload_byte_ceiling")) o.optInt("payload_byte_ceiling") else null,
                    observationCountCeiling = if (o.has("observation_count_ceiling")) o.optInt("observation_count_ceiling") else null,
                )
            }.getOrNull()
        }

        private fun buildChunk(
            rootBatchId: String,
            rootNonce: String,
            index: Int,
            observations: JSONArray,
            deleted: JSONArray,
            nextChangesToken: String?,
            healthConnectStatus: JSONObject,
            permissions: JSONObject,
            workmanager: JSONObject,
        ): Chunk {
            val batchId = UUID.nameUUIDFromBytes("$rootBatchId:chunk:$index".toByteArray(StandardCharsets.UTF_8)).toString()
            val nonce = UUID.nameUUIDFromBytes("$rootNonce:chunk:$index".toByteArray(StandardCharsets.UTF_8)).toString()
            val payloadBytes = DeliveryEnvelope.measureBytes(
                batchId = batchId,
                nonce = nonce,
                observations = observations,
                deletedRecordIds = deleted,
                nextChangesToken = nextChangesToken,
                healthConnectStatus = healthConnectStatus,
                permissions = permissions,
                workmanager = workmanager,
                queued = observations.length(),
                includeNextCursor = nextChangesToken != null,
            )
            return Chunk(
                index = index,
                batchId = batchId,
                nonce = nonce,
                observationsJson = observations.toString(),
                deletedRecordIdsJson = deleted.toString(),
                nextChangesToken = nextChangesToken,
                queuedObservations = observations.length(),
                measuredPayloadBytes = payloadBytes,
            )
        }

        private fun serializeObservations(observations: List<CompanionObservation>): JSONArray {
            val arr = JSONArray()
            observations.forEach { obs ->
                val o = JSONObject()
                obs.toMap().forEach { (k, v) ->
                    when (v) {
                        null -> o.put(k, JSONObject.NULL)
                        is Map<*, *> -> {
                            val nested = JSONObject()
                            v.forEach { (nk, nv) -> nested.put(nk.toString(), nv ?: JSONObject.NULL) }
                            o.put(k, nested)
                        }
                        else -> o.put(k, v)
                    }
                }
                arr.put(o)
            }
            return arr
        }

        private fun observationsFromJson(raw: String): List<CompanionObservation> {
            val arr = JSONArray(raw)
            val out = mutableListOf<CompanionObservation>()
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                @Suppress("UNCHECKED_CAST")
                val deviceMap = mutableMapOf<String, String>()
                val deviceObj = o.optJSONObject("device")
                if (deviceObj != null) {
                    deviceObj.keys().forEach { key -> deviceMap[key] = deviceObj.optString(key) }
                }
                out += CompanionObservation(
                    observationId = o.getString("observation_id"),
                    sourceRecordId = o.getString("source_record_id"),
                    metricType = o.getString("metric_type"),
                    value = if (o.isNull("value")) null else o.getDouble("value"),
                    textValue = if (o.has("text_value") && !o.isNull("text_value")) o.optString("text_value") else null,
                    unit = if (o.isNull("unit")) null else o.optString("unit"),
                    measuredAt = o.getString("measured_at"),
                    receivedAt = o.getString("received_at"),
                    acquisitionMode = o.optString("acquisition_mode", "DELAYED"),
                    source = o.optString("source", "health_connect_companion"),
                    provenance = o.optString("provenance", "health_connect_sync"),
                    trendDirection = if (o.has("trend_direction") && !o.isNull("trend_direction")) o.optString("trend_direction") else null,
                    device = deviceMap,
                )
            }
            return out
        }

        private fun deletedIdsFromJson(raw: String): List<String> {
            val arr = JSONArray(raw)
            return (0 until arr.length()).map { arr.getString(it) }
        }

        private fun chunkToJsonObject(chunk: Chunk): JSONObject = JSONObject()
            .put("index", chunk.index)
            .put("batch_id", chunk.batchId)
            .put("nonce", chunk.nonce)
            .put("observations_json", chunk.observationsJson)
            .put("deleted_record_ids_json", chunk.deletedRecordIdsJson)
            .put("next_changes_token", chunk.nextChangesToken ?: JSONObject.NULL)
            .put("queued_observations", chunk.queuedObservations)
            .put("measured_payload_bytes", chunk.measuredPayloadBytes)
    }

    fun toJson(): String = JSONObject()
        .put("batch_id", batchId)
        .put("nonce", nonce)
        .put("observations_json", observationsJson)
        .put("next_changes_token", nextChangesToken ?: JSONObject.NULL)
        .put("deleted_record_ids_json", deletedRecordIdsJson)
        .put("created_at_epoch_ms", createdAtEpochMs)
        .put("token_scope", tokenScope ?: JSONObject.NULL)
        .put("partial_permission_warning", partialPermissionWarning)
        .put("chunks_json", chunksJson ?: JSONObject.NULL)
        .put("active_chunk_index", activeChunkIndex)
        .put("health_connect_status_json", healthConnectStatusJson ?: JSONObject.NULL)
        .put("permissions_json", permissionsJson ?: JSONObject.NULL)
        .put("workmanager_json", workmanagerJson ?: JSONObject.NULL)
        .put("payload_byte_ceiling", payloadByteCeiling ?: JSONObject.NULL)
        .put("observation_count_ceiling", observationCountCeiling ?: JSONObject.NULL)
        .toString()
}
