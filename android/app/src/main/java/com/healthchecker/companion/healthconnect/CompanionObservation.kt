package com.healthchecker.companion.healthconnect

/**
 * Normalized observation payload matching HC-302 / HC-303A host schema.
 * Acquisition modes are LIVE or DELAYED only — never SIMULATED in production.
 */
data class CompanionObservation(
    val observationId: String,
    val sourceRecordId: String,
    val metricType: String,
    val value: Double?,
    val textValue: String? = null,
    val unit: String?,
    val measuredAt: String,
    val receivedAt: String,
    val acquisitionMode: String = "DELAYED",
    val source: String = "health_connect_companion",
    val provenance: String = "health_connect_sync",
    val trendDirection: String? = null,
    val device: Map<String, String> = emptyMap()
) {
    fun toMap(): Map<String, Any?> = mapOf(
        "observation_id" to observationId,
        "source_record_id" to sourceRecordId,
        "metric_type" to metricType,
        "value" to value,
        "text_value" to textValue,
        "unit" to unit,
        "measured_at" to measuredAt,
        "received_at" to receivedAt,
        "acquisition_mode" to acquisitionMode,
        "source" to source,
        "provenance" to provenance,
        "trend_direction" to trendDirection,
        "device" to device
    )
}
