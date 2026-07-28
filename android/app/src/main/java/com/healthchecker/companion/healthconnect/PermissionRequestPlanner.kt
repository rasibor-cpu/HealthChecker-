package com.healthchecker.companion.healthconnect

/**
 * Pure planner for Health Connect permission UI launches (HC-303C).
 * Always request only currently-missing permissions — re-requesting already-granted
 * permissions can no-op the Health Connect UI on Android 14+/16 after a partial grant.
 */
object PermissionRequestPlanner {
    enum class Action {
        LAUNCH_MISSING,
        ALREADY_COMPLETE,
        NOT_READY,
        BLOCKED_IN_PROGRESS
    }

    enum class LifecycleEvent {
        LAUNCH_STARTED,
        RESULT_DELIVERED,
        CANCELLED,
        RESUME_AFTER_PAUSE,
        LAUNCH_FAILED,
        ACTIVITY_RECREATED
    }

    data class Plan(
        val action: Action,
        val permissionsToRequest: Set<String>,
        val userMessage: String
    )

    fun plan(
        availability: HealthConnectAvailability,
        missingPermissions: Set<String>,
        requestInProgress: Boolean
    ): Plan {
        if (requestInProgress) {
            return Plan(
                action = Action.BLOCKED_IN_PROGRESS,
                permissionsToRequest = emptySet(),
                userMessage = "Permission request already in progress. Wait for Health Connect to return, then try again."
            )
        }
        if (availability != HealthConnectAvailability.READY) {
            return Plan(
                action = Action.NOT_READY,
                permissionsToRequest = emptySet(),
                userMessage = "Health Connect is not ready for permission requests ($availability)."
            )
        }
        if (missingPermissions.isEmpty()) {
            return Plan(
                action = Action.ALREADY_COMPLETE,
                permissionsToRequest = emptySet(),
                userMessage = "All required Health Connect permissions are already granted."
            )
        }
        return Plan(
            action = Action.LAUNCH_MISSING,
            permissionsToRequest = missingPermissions,
            userMessage = "Requesting ${missingPermissions.size} missing Health Connect permission(s)."
        )
    }

    /**
     * In-progress flag must clear after cancel, result, resume, failed launch, or recreation
     * so the permissions button remains reusable.
     */
    fun nextInProgress(event: LifecycleEvent): Boolean {
        return event == LifecycleEvent.LAUNCH_STARTED
    }
}
