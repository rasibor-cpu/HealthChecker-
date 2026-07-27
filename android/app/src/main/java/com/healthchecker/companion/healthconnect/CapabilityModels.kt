package com.healthchecker.companion.healthconnect

/**
 * Honest Health Connect capability states for HC-303A.
 * Never fabricate capability or treat permission denial as success.
 */
enum class HealthConnectAvailability {
    READY,
    UNSUPPORTED,
    UNAVAILABLE,
    UPDATE_REQUIRED,
    ERROR
}

data class CapabilityReport(
    val availability: HealthConnectAvailability,
    val sdkAvailable: Boolean,
    val message: String,
    val permissionsGranted: Set<String> = emptySet(),
    val permissionsMissing: Set<String> = emptySet(),
    val ecgSupported: Boolean = false
)
