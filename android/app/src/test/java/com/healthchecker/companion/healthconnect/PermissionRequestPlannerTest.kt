package com.healthchecker.companion.healthconnect

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Regression coverage for HC-303C permission UI reopen defect.
 * Pure JVM — no phone, no real Health Connect data.
 */
class PermissionRequestPlannerTest {
    private val steps = "android.permission.health.READ_STEPS"
    private val hr = "android.permission.health.READ_HEART_RATE"
    private val missingAfterPartial = setOf(hr)

    @Test
    fun cancelThenRetryLaunchesMissingOnly() {
        // After cancel, nothing granted — full missing set is launchable again.
        val afterCancel = PermissionRequestPlanner.plan(
            availability = HealthConnectAvailability.READY,
            missingPermissions = setOf(steps, hr),
            requestInProgress = false
        )
        assertEquals(PermissionRequestPlanner.Action.LAUNCH_MISSING, afterCancel.action)
        assertEquals(setOf(steps, hr), afterCancel.permissionsToRequest)
    }

    @Test
    fun partialGrantThenRetryRequestsOnlyRemaining() {
        val plan = PermissionRequestPlanner.plan(
            availability = HealthConnectAvailability.READY,
            missingPermissions = missingAfterPartial,
            requestInProgress = false
        )
        assertEquals(PermissionRequestPlanner.Action.LAUNCH_MISSING, plan.action)
        assertEquals(setOf(hr), plan.permissionsToRequest)
        assertFalse(plan.permissionsToRequest.contains(steps))
    }

    @Test
    fun inProgressBlocksDuplicateLaunch() {
        val plan = PermissionRequestPlanner.plan(
            availability = HealthConnectAvailability.READY,
            missingPermissions = missingAfterPartial,
            requestInProgress = true
        )
        assertEquals(PermissionRequestPlanner.Action.BLOCKED_IN_PROGRESS, plan.action)
        assertTrue(plan.permissionsToRequest.isEmpty())
    }

    @Test
    fun activityRecreationClearsInProgress() {
        assertTrue(
            PermissionRequestPlanner.nextInProgress(PermissionRequestPlanner.LifecycleEvent.LAUNCH_STARTED)
        )
        assertFalse(
            PermissionRequestPlanner.nextInProgress(PermissionRequestPlanner.LifecycleEvent.ACTIVITY_RECREATED)
        )
        assertFalse(
            PermissionRequestPlanner.nextInProgress(PermissionRequestPlanner.LifecycleEvent.RESUME_AFTER_PAUSE)
        )
        assertFalse(
            PermissionRequestPlanner.nextInProgress(PermissionRequestPlanner.LifecycleEvent.RESULT_DELIVERED)
        )
        assertFalse(
            PermissionRequestPlanner.nextInProgress(PermissionRequestPlanner.LifecycleEvent.CANCELLED)
        )
        assertFalse(
            PermissionRequestPlanner.nextInProgress(PermissionRequestPlanner.LifecycleEvent.LAUNCH_FAILED)
        )
    }

    @Test
    fun launchFailurePathClearsAndSurfacesNotReadyOrEmpty() {
        val failedFlag = PermissionRequestPlanner.nextInProgress(
            PermissionRequestPlanner.LifecycleEvent.LAUNCH_FAILED
        )
        assertFalse(failedFlag)
        val notReady = PermissionRequestPlanner.plan(
            availability = HealthConnectAvailability.UNAVAILABLE,
            missingPermissions = setOf(steps),
            requestInProgress = false
        )
        assertEquals(PermissionRequestPlanner.Action.NOT_READY, notReady.action)
        val complete = PermissionRequestPlanner.plan(
            availability = HealthConnectAvailability.READY,
            missingPermissions = emptySet(),
            requestInProgress = false
        )
        assertEquals(PermissionRequestPlanner.Action.ALREADY_COMPLETE, complete.action)
    }
}
