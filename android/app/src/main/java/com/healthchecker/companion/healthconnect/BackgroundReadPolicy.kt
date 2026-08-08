package com.healthchecker.companion.healthconnect

/**
 * Fail-closed policy for Health Connect background reads.
 *
 * Record permissions remain separately counted. Background access is
 * requested only when supported, and periodic work is enabled only after
 * the user grants the additional permission.
 */
object BackgroundReadPolicy {
    const val PERMISSION =
        "android.permission.health.READ_HEALTH_DATA_IN_BACKGROUND"

    enum class ScheduleDecision {
        READY,
        FEATURE_UNAVAILABLE,
        PERMISSION_REQUIRED
    }

    fun permissionsToRequest(
        missingRecordPermissions: Set<String>,
        featureAvailable: Boolean,
        backgroundPermissionGranted: Boolean
    ): Set<String> {
        val requested = missingRecordPermissions.toMutableSet()
        if (featureAvailable && !backgroundPermissionGranted) {
            requested += PERMISSION
        }
        return requested
    }

    fun scheduleDecision(
        featureAvailable: Boolean,
        backgroundPermissionGranted: Boolean
    ): ScheduleDecision {
        if (!featureAvailable) {
            return ScheduleDecision.FEATURE_UNAVAILABLE
        }
        if (!backgroundPermissionGranted) {
            return ScheduleDecision.PERMISSION_REQUIRED
        }
        return ScheduleDecision.READY
    }
}
