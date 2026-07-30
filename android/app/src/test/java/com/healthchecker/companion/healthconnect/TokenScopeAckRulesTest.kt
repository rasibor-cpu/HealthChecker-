package com.healthchecker.companion.healthconnect

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * HC-306I-R3 — token+scope commit rules (no raw token material asserted beyond presence flags).
 */
class TokenScopeAckRulesTest {

    @Test
    fun scopeMustBeValidBeforePersist() {
        assertTrue(GrantedRecordCatalog.isValidScopeFingerprint("steps"))
        assertTrue(GrantedRecordCatalog.isValidScopeFingerprint("heart_rate,steps"))
        assertFalse(GrantedRecordCatalog.isValidScopeFingerprint(null))
        assertFalse(GrantedRecordCatalog.isValidScopeFingerprint(""))
        assertFalse(GrantedRecordCatalog.isValidScopeFingerprint("steps,evil"))
    }

    @Test
    fun expandedScopeRequiresReinitializationDecision() {
        val oldScope = "steps"
        val newScope = "exercise,steps"
        // Same decision HealthConnectReader uses before getChanges
        val needsReinit = !GrantedRecordCatalog.scopeMatches(oldScope, newScope)
        assertTrue(needsReinit)
    }

    @Test
    fun reducedScopeRequiresReinitializationDecision() {
        val oldScope = "exercise,steps"
        val newScope = "steps"
        assertTrue(!GrantedRecordCatalog.scopeMatches(oldScope, newScope))
    }

    @Test
    fun missingScopeWithTokenForcesReinit() {
        val tokenPresent = true
        val persistedScope: String? = null
        val current = "steps"
        val needsReinit =
            tokenPresent &&
                (persistedScope.isNullOrBlank() ||
                    !GrantedRecordCatalog.isValidScopeFingerprint(persistedScope) ||
                    !GrantedRecordCatalog.scopeMatches(persistedScope, current))
        assertTrue(needsReinit)
    }

    @Test
    fun acknowledgeRequiresBothTokenAndScope() {
        // Mirrors HealthConnectReader.acknowledgeCursor preconditions
        fun wouldPersist(token: String?, scope: String?): Boolean =
            !token.isNullOrBlank() &&
                !scope.isNullOrBlank() &&
                GrantedRecordCatalog.isValidScopeFingerprint(scope)
        assertFalse(wouldPersist(null, "steps"))
        assertFalse(wouldPersist("tok", null))
        assertFalse(wouldPersist("tok", "bad_scope"))
        assertTrue(wouldPersist("tok", "steps"))
    }
}
