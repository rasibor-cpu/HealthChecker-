package com.healthchecker.companion.healthconnect

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PermissionLaunchMonitorTest {
    private val missing = setOf("android.permission.health.READ_HEART_RATE")

    @Test
    fun detectsSilentNoResultOnResume() {
        val attempt = PermissionLaunchMonitor.LaunchAttempt(missingBefore = missing)
        val assessment = PermissionLaunchMonitor.assessResume(
            attempt = attempt,
            missingAfter = missing,
            stillMarkedInProgress = true
        )
        assertTrue(assessment.silentOrNoResult)
        assertTrue(assessment.suggestManagePermissions)
        assertTrue(assessment.userMessage!!.contains("MANAGE HEALTH CONNECT PERMISSIONS"))
    }

    @Test
    fun deliveredResultUnchangedSuggestsManageButNotSilent() {
        val attempt = PermissionLaunchMonitor.onResultDelivered(
            PermissionLaunchMonitor.LaunchAttempt(missingBefore = missing)
        )
        val assessment = PermissionLaunchMonitor.assessResume(
            attempt = attempt,
            missingAfter = missing,
            stillMarkedInProgress = false
        )
        assertFalse(assessment.silentOrNoResult)
        assertTrue(assessment.suggestManagePermissions)
    }

    @Test
    fun noAttemptMeansNoSilentDetection() {
        val assessment = PermissionLaunchMonitor.assessResume(
            attempt = null,
            missingAfter = missing,
            stillMarkedInProgress = false
        )
        assertFalse(assessment.silentOrNoResult)
        assertFalse(assessment.suggestManagePermissions)
    }
}
