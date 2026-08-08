package com.healthchecker.companion.healthconnect

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BackgroundReadPolicyTest {
    private val steps = "android.permission.health.READ_STEPS"

    @Test
    fun unsupportedFeatureIsNotRequestedAndCannotSchedule() {
        val requested = BackgroundReadPolicy.permissionsToRequest(
            missingRecordPermissions = setOf(steps),
            featureAvailable = false,
            backgroundPermissionGranted = false
        )
        assertEquals(setOf(steps), requested)
        assertFalse(
            requested.contains(BackgroundReadPolicy.PERMISSION)
        )
        assertEquals(
            BackgroundReadPolicy.ScheduleDecision.FEATURE_UNAVAILABLE,
            BackgroundReadPolicy.scheduleDecision(false, false)
        )
    }

    @Test
    fun supportedMissingBackgroundPermissionIsRequested() {
        val requested = BackgroundReadPolicy.permissionsToRequest(
            missingRecordPermissions = emptySet(),
            featureAvailable = true,
            backgroundPermissionGranted = false
        )
        assertEquals(
            setOf(BackgroundReadPolicy.PERMISSION),
            requested
        )
        assertEquals(
            BackgroundReadPolicy.ScheduleDecision.PERMISSION_REQUIRED,
            BackgroundReadPolicy.scheduleDecision(true, false)
        )
    }

    @Test
    fun recordAndBackgroundPermissionsAreRequestedTogether() {
        val requested = BackgroundReadPolicy.permissionsToRequest(
            missingRecordPermissions = setOf(steps),
            featureAvailable = true,
            backgroundPermissionGranted = false
        )
        assertEquals(
            setOf(steps, BackgroundReadPolicy.PERMISSION),
            requested
        )
    }

    @Test
    fun grantedBackgroundPermissionAllowsScheduling() {
        val requested = BackgroundReadPolicy.permissionsToRequest(
            missingRecordPermissions = emptySet(),
            featureAvailable = true,
            backgroundPermissionGranted = true
        )
        assertTrue(requested.isEmpty())
        assertEquals(
            BackgroundReadPolicy.ScheduleDecision.READY,
            BackgroundReadPolicy.scheduleDecision(true, true)
        )
    }

    @Test
    fun recordCountsRemainIndependent() {
        val report = CapabilityReport(
            availability = HealthConnectAvailability.READY,
            sdkAvailable = true,
            message = "ready",
            permissionsGranted = setOf(steps),
            permissionsMissing = emptySet(),
            backgroundReadFeatureAvailable = true,
            backgroundReadPermissionGranted = false
        )
        assertEquals(1, report.permissionsGranted.size)
        assertEquals(0, report.permissionsMissing.size)
        assertFalse(report.backgroundReadPermissionGranted)
    }
}
