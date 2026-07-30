package com.healthchecker.companion.sync

import com.healthchecker.companion.healthconnect.CompanionObservation
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

/**
 * Stable batch identity for HC-303B retries.
 * Persist identity with payload; reuse until durable host acknowledgement.
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
    val partialPermissionWarning: Boolean = false
) {
    fun observations(): List<CompanionObservation> {
        val arr = JSONArray(observationsJson)
        val out = mutableListOf<CompanionObservation>()
        for (i in 0 until arr.length()) {
            val o = arr.getJSONObject(i)
            @Suppress("UNCHECKED_CAST")
            val deviceMap = mutableMapOf<String, String>()
            val deviceObj = o.optJSONObject("device")
            if (deviceObj != null) {
                deviceObj.keys().forEach { key ->
                    deviceMap[key] = deviceObj.optString(key)
                }
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
                device = deviceMap
            )
        }
        return out
    }

    fun deletedRecordIds(): List<String> {
        val arr = JSONArray(deletedRecordIdsJson)
        return (0 until arr.length()).map { arr.getString(it) }
    }

    companion object {
        fun create(
            observations: List<CompanionObservation>,
            nextChangesToken: String?,
            deletedRecordIds: List<String>,
            tokenScope: String? = null,
            partialPermissionWarning: Boolean = false,
            nowEpochMs: Long = System.currentTimeMillis()
        ): PendingBatch {
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
                partialPermissionWarning = partialPermissionWarning
            )
        }

        fun fromJson(raw: String): PendingBatch? {
            return runCatching {
                val o = JSONObject(raw)
                val batchId = o.getString("batch_id")
                val nonce = o.getString("nonce")
                val observationsJson = o.getString("observations_json")
                require(batchId.isNotBlank() && nonce.isNotBlank())
                // Validate payload JSON parses (corruption detection)
                JSONArray(observationsJson)
                val deletedJson = o.optString("deleted_record_ids_json", "[]")
                JSONArray(deletedJson)
                PendingBatch(
                    batchId = batchId,
                    nonce = nonce,
                    observationsJson = observationsJson,
                    nextChangesToken = if (o.isNull("next_changes_token")) null else o.optString("next_changes_token"),
                    deletedRecordIdsJson = deletedJson,
                    createdAtEpochMs = o.optLong("created_at_epoch_ms", 0L),
                    tokenScope = if (o.isNull("token_scope")) null else o.optString("token_scope").ifBlank { null },
                    partialPermissionWarning = o.optBoolean("partial_permission_warning", false)
                )
            }.getOrNull()
        }
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
        .toString()
}
