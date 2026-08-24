package com.healthchecker.companion.healthconnect

import androidx.health.connect.client.records.ExerciseSessionRecord
import androidx.health.connect.client.records.StepsRecord
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * HC-306I-R3 — permission→record mapping and token-scope fingerprints.
 * Pure JVM / Robolectric — no live Health Connect queries.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], manifest = Config.NONE)
class GrantedRecordCatalogTest {

    private val stepsPerm =
        GrantedRecordCatalog.bindings.first { it.metric == GrantedRecordCatalog.Metric.STEPS }.readPermission
    private val exercisePerm =
        GrantedRecordCatalog.bindings.first { it.metric == GrantedRecordCatalog.Metric.EXERCISE }.readPermission
    private val hrPerm =
        GrantedRecordCatalog.bindings.first { it.metric == GrantedRecordCatalog.Metric.HEART_RATE }.readPermission

    @Test
    fun stepsOnlyMapsToStepsRecordOnly() {
        val granted = setOf(stepsPerm)
        val classes = GrantedRecordCatalog.recordClasses(granted)
        assertEquals(setOf(StepsRecord::class), classes)
        assertFalse(classes.contains(ExerciseSessionRecord::class))
        assertTrue(GrantedRecordCatalog.includesSteps(granted))
    }

    @Test
    fun stepsOnlyDoesNotIncludeExerciseOrOthers() {
        val granted = setOf(stepsPerm)
        val bindings = GrantedRecordCatalog.grantedBindings(granted)
        assertEquals(1, bindings.size)
        assertEquals(GrantedRecordCatalog.Metric.STEPS, bindings.single().metric)
        assertEquals(8, GrantedRecordCatalog.missingPermissions(granted).size)
    }

    @Test
    fun zeroGrantedYieldsEmptyClasses() {
        val classes = GrantedRecordCatalog.recordClasses(emptySet())
        assertTrue(classes.isEmpty())
        assertEquals("", GrantedRecordCatalog.scopeFingerprint(emptySet()))
    }

    @Test
    fun scopeFingerprintIsSortedAndStable() {
        val a = GrantedRecordCatalog.scopeFingerprint(setOf(stepsPerm, hrPerm))
        val b = GrantedRecordCatalog.scopeFingerprint(setOf(hrPerm, stepsPerm))
        assertEquals(a, b)
        assertEquals("heart_rate,steps", a)
    }

    @Test
    fun expandedPermissionChangesScope() {
        val stepsOnly = GrantedRecordCatalog.scopeFingerprint(setOf(stepsPerm))
        val expanded = GrantedRecordCatalog.scopeFingerprint(setOf(stepsPerm, exercisePerm))
        assertFalse(GrantedRecordCatalog.scopeMatches(stepsOnly, expanded))
        assertTrue(GrantedRecordCatalog.scopeMatches(stepsOnly, stepsOnly))
    }

    @Test
    fun revokedPermissionDropsFromScopeAndClasses() {
        val before = setOf(stepsPerm, exercisePerm)
        val after = setOf(stepsPerm)
        assertTrue(ExerciseSessionRecord::class in GrantedRecordCatalog.recordClasses(before))
        assertFalse(ExerciseSessionRecord::class in GrantedRecordCatalog.recordClasses(after))
        assertFalse(
            GrantedRecordCatalog.scopeMatches(
                GrantedRecordCatalog.scopeFingerprint(before),
                GrantedRecordCatalog.scopeFingerprint(after)
            )
        )
    }

    @Test
    fun corruptScopeFailsClosed() {
        assertNull(GrantedRecordCatalog.parseScopeFingerprint("steps,not_a_real_metric"))
        assertFalse(GrantedRecordCatalog.isValidScopeFingerprint("steps,not_a_real_metric"))
        assertFalse(GrantedRecordCatalog.scopeMatches("steps,not_a_real_metric", "steps"))
        assertNotNull(GrantedRecordCatalog.parseScopeFingerprint("steps"))
        assertEquals(emptySet<String>(), GrantedRecordCatalog.parseScopeFingerprint(""))
    }

    @Test
    fun blankPersistedScopeDoesNotMatchCurrent() {
        assertFalse(GrantedRecordCatalog.scopeMatches(null, "steps"))
        assertFalse(GrantedRecordCatalog.scopeMatches("", "steps"))
        assertFalse(GrantedRecordCatalog.scopeMatches("   ", "steps"))
    }

    @Test
    fun emptyFingerprintIsNotValidForPersistence() {
        assertFalse(GrantedRecordCatalog.isValidScopeFingerprint(""))
        assertFalse(GrantedRecordCatalog.isValidScopeFingerprint(null))
        assertTrue(GrantedRecordCatalog.isValidScopeFingerprint("steps"))
    }
}
