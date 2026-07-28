package com.healthchecker.companion.healthconnect

/**
 * Detects Android 16 / Health Connect silent permission launches where the Companion
 * pauses/resumes but no permission ActivityResult is delivered (or UI never appears).
 */
object PermissionLaunchMonitor {
    data class LaunchAttempt(
        val missingBefore: Set<String>,
        val resultDelivered: Boolean = false
    )

    data class ResumeAssessment(
        val silentOrNoResult: Boolean,
        val userMessage: String?,
        val suggestManagePermissions: Boolean
    )

    fun onResultDelivered(attempt: LaunchAttempt): LaunchAttempt =
        attempt.copy(resultDelivered = true)

    /**
     * Evaluate resume after a launch attempt.
     * Silent/no-result: resumed while still awaiting and no ActivityResult arrived.
     * Unchanged missing after a delivered result is treated as cancel/deny — not silent.
     */
    fun assessResume(
        attempt: LaunchAttempt?,
        missingAfter: Set<String>,
        stillMarkedInProgress: Boolean
    ): ResumeAssessment {
        if (attempt == null) {
            return ResumeAssessment(false, null, false)
        }
        if (!attempt.resultDelivered && stillMarkedInProgress) {
            return ResumeAssessment(
                silentOrNoResult = true,
                userMessage = "Health Connect permission screen returned no result. " +
                    "Use MANAGE HEALTH CONNECT PERMISSIONS to adjust access, then return here.",
                suggestManagePermissions = true
            )
        }
        if (attempt.resultDelivered && missingAfter == attempt.missingBefore && missingAfter.isNotEmpty()) {
            return ResumeAssessment(
                silentOrNoResult = false,
                userMessage = "No additional permissions were granted. " +
                    "You can retry REQUEST, or use MANAGE HEALTH CONNECT PERMISSIONS.",
                suggestManagePermissions = true
            )
        }
        return ResumeAssessment(false, null, false)
    }
}
