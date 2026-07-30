package com.healthchecker.companion.host

import org.json.JSONArray
import org.json.JSONObject

/**
 * Exact Android-side serialization of the companion delivery envelope.
 *
 * The chunk planner measures this JSON directly so every persisted chunk stays
 * below the unchanged host-side payload ceiling.
 *
 * Non-final chunks omit next_cursor so the host keeps its prior cursor value
 * instead of persisting a null changes token.
 */
object DeliveryEnvelope {
    fun build(
        batchId: String,
        nonce: String,
        observations: JSONArray,
        deletedRecordIds: JSONArray,
        nextChangesToken: String?,
        healthConnectStatus: JSONObject,
        permissions: JSONObject,
        workmanager: JSONObject,
        queued: Int,
        includeNextCursor: Boolean = true,
    ): JSONObject {
        val body = JSONObject()
            .put("batch_id", batchId)
            .put("nonce", nonce)
            .put("sent_at", java.time.Instant.now().toString())
            .put("observations", observations)
            .put("deletions", deletedRecordIds)
            .put("health_connect_status", healthConnectStatus)
            .put("permissions", permissions)
            .put("workmanager", workmanager)
            .put("queued_observations", queued)
        if (includeNextCursor) {
            body.put(
                "next_cursor",
                JSONObject().put("changes_token", nextChangesToken ?: JSONObject.NULL),
            )
        }
        return body
    }

    fun measureBytes(
        batchId: String,
        nonce: String,
        observations: JSONArray,
        deletedRecordIds: JSONArray,
        nextChangesToken: String?,
        healthConnectStatus: JSONObject,
        permissions: JSONObject,
        workmanager: JSONObject,
        queued: Int,
        includeNextCursor: Boolean = true,
    ): Int = build(
        batchId = batchId,
        nonce = nonce,
        observations = observations,
        deletedRecordIds = deletedRecordIds,
        nextChangesToken = nextChangesToken,
        healthConnectStatus = healthConnectStatus,
        permissions = permissions,
        workmanager = workmanager,
        queued = queued,
        includeNextCursor = includeNextCursor,
    ).toString().toByteArray(Charsets.UTF_8).size
}
