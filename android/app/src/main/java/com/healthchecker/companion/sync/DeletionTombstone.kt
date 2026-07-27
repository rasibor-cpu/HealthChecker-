package com.healthchecker.companion.sync

/**
 * Deletion reconciliation for HC-303B.
 *
 * Health Connect deletion changes are represented as provenance/tombstone events.
 * Host clinical history is NOT physically deleted in this phase.
 */
data class DeletionTombstone(
    val sourceRecordId: String,
    val connectorId: String = "health_connect",
    val eventType: String = "health_connect_record_deleted",
    val disposition: String = "tombstone_only_no_clinical_delete",
    val measuredAt: String,
    val receivedAt: String
) {
    fun toHostMap(): Map<String, Any?> = mapOf(
        "observation_id" to ("tombstone:" + sourceRecordId),
        "source_record_id" to sourceRecordId,
        "metric_type" to "health_connect_deletion",
        "value" to null,
        "text_value" to disposition,
        "unit" to null,
        "measured_at" to measuredAt,
        "received_at" to receivedAt,
        "acquisition_mode" to "DELAYED",
        "source" to "health_connect_companion",
        "provenance" to "health_connect_deletion_tombstone",
        "device" to mapOf(
            "event_type" to eventType,
            "connector_id" to connectorId,
            "clinical_history_deleted" to "false"
        )
    )
}
