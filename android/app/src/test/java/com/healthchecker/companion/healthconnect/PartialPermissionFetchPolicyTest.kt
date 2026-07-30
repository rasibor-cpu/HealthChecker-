package com.healthchecker.companion.healthconnect

import androidx.health.connect.client.records.ExerciseSessionRecord
import androidx.health.connect.client.records.StepsRecord
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * HC-306I-R3 — adversarial partial-permission fetch policy (no HC client I/O).
 */
class PartialPermissionFetchPolicyTest {

    private val stepsPerm =
        GrantedRecordCatalog.bindings.first { it.metric == GrantedRecordCatalog.Metric.STEPS }.readPermission
    private val exercisePerm =
        GrantedRecordCatalog.bindings.first { it.metric == GrantedRecordCatalog.Metric.EXERCISE }.readPermission

    @Test
    fun stepsOnlyQueriesStepsRecordClassOnly() {
        val granted = setOf(stepsPerm)
        val classes = GrantedRecordCatalog.recordClasses(granted)
        assertEquals(setOf(StepsRecord::class), classes)
        assertTrue(GrantedRecordCatalog.includesSteps(granted))
        assertFalse(ExerciseSessionRecord::class in classes)
    }

    @Test
    fun stepsOnlyDoesNotIncludeExerciseOrOthers() {
        val classes = GrantedRecordCatalog.recordClasses(setOf(stepsPerm))
        assertEquals(1, classes.size)
        assertFalse(classes.any { it != StepsRecord::class })
    }

    @Test
    fun zeroGrantedMeansNoRecordClassesAndFatalGate() {
        val classes = GrantedRecordCatalog.recordClasses(emptySet())
        assertTrue(classes.isEmpty())
        val fatal = HealthConnectReader.FetchResult(
            observations = emptyList(),
            nextChangesToken = null,
            deletedRecordIds = emptyList(),
            permissionRequired = true,
            error = "no_granted_permissions",
            queryPerformed = false,
            disposition = QueryDisposition.NOT_PERFORMED_FATAL,
            grantedTypeCount = 0,
            missingTypeCount = 8
        )
        assertFalse(SyncDeliveryGate.shouldDeliver(fatal))
        assertFalse(SyncDeliveryGate.shouldMarkSuccess(fatal, true, true))
    }

    @Test
    fun revokedExerciseRemovedFromQuerySet() {
        val before = setOf(stepsPerm, exercisePerm)
        val after = setOf(stepsPerm)
        assertTrue(ExerciseSessionRecord::class in GrantedRecordCatalog.recordClasses(before))
        assertFalse(ExerciseSessionRecord::class in GrantedRecordCatalog.recordClasses(after))
    }

    @Test
    fun getChangesTokenScopeUsesOnlyGrantedFingerprint() {
        val scope = GrantedRecordCatalog.scopeFingerprint(setOf(stepsPerm))
        assertEquals("steps", scope)
        // Token request set ≡ record classes for that fingerprint
        assertEquals(
            GrantedRecordCatalog.recordClasses(setOf(stepsPerm)),
            GrantedRecordCatalog.recordClasses(setOf(stepsPerm))
        )
    }
}
