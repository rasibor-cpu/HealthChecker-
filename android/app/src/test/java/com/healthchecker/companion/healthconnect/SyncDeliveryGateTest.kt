package com.healthchecker.companion.healthconnect

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * HC-306I-R3 — false-success / delivery disposition gate (manual ≡ worker).
 */
class SyncDeliveryGateTest {

    private fun result(
        disposition: QueryDisposition,
        queryPerformed: Boolean = disposition != QueryDisposition.NOT_PERFORMED_FATAL,
        error: String? = null,
        partial: Boolean = disposition == QueryDisposition.PERFORMED_PARTIAL
    ) = HealthConnectReader.FetchResult(
        observations = emptyList(),
        nextChangesToken = if (queryPerformed) "tok" else null,
        deletedRecordIds = emptyList(),
        error = error,
        queryPerformed = queryPerformed,
        disposition = disposition,
        partialPermissionWarning = partial,
        grantedTypeCount = if (queryPerformed) 1 else 0,
        missingTypeCount = if (partial) 7 else 0,
        proposedTokenScope = if (queryPerformed) "steps" else null
    )

    @Test
    fun zeroGrantedFatalDoesNotDeliverOrSucceed() {
        val fetch = result(
            QueryDisposition.NOT_PERFORMED_FATAL,
            queryPerformed = false,
            error = "no_granted_permissions"
        )
        assertFalse(SyncDeliveryGate.shouldDeliver(fetch))
        assertFalse(SyncDeliveryGate.shouldMarkSuccess(fetch, ackOk = true, cursorAdvanced = true))
        assertEquals("no_granted_permissions", SyncDeliveryGate.visibleError(fetch))
    }

    @Test
    fun partialGrantDeliversWithWarning() {
        val fetch = result(QueryDisposition.PERFORMED_PARTIAL)
        assertTrue(SyncDeliveryGate.shouldDeliver(fetch))
        assertTrue(SyncDeliveryGate.shouldMarkSuccess(fetch, ackOk = true, cursorAdvanced = true))
        assertTrue(SyncDeliveryGate.visiblePartialWarning(fetch))
        assertNull(SyncDeliveryGate.visibleError(fetch))
    }

    @Test
    fun performedEmptyQueryMaySucceed() {
        val fetch = result(QueryDisposition.PERFORMED_OK)
        assertTrue(fetch.queryPerformed)
        assertTrue(fetch.observations.isEmpty())
        assertTrue(SyncDeliveryGate.shouldDeliver(fetch))
        assertTrue(SyncDeliveryGate.shouldMarkSuccess(fetch, ackOk = true, cursorAdvanced = true))
        // Distinguishable from historical false-empty: queryPerformed=true
        assertTrue(fetch.queryPerformed)
        assertTrue(fetch.disposition != QueryDisposition.NOT_PERFORMED_FATAL)
    }

    @Test
    fun manualPathMustNotDeliverFatalFetch() {
        val fatal = result(QueryDisposition.NOT_PERFORMED_FATAL, error = "permissions_missing")
        // Same gate used by CompanionStatusActivity and MonitoringSyncWorker
        assertFalse(SyncDeliveryGate.shouldDeliver(fatal))
    }

    @Test
    fun workerPathBehavesIdenticallyForFatal() {
        val fatal = result(QueryDisposition.NOT_PERFORMED_FATAL, error = "no_granted_permissions")
        assertFalse(SyncDeliveryGate.shouldDeliver(fatal))
        assertFalse(SyncDeliveryGate.shouldMarkSuccess(fatal, true, true))
    }

    @Test
    fun ackFailureDoesNotMarkSuccessEvenIfQueryPerformed() {
        val fetch = result(QueryDisposition.PERFORMED_OK)
        assertFalse(SyncDeliveryGate.shouldMarkSuccess(fetch, ackOk = false, cursorAdvanced = false))
        assertFalse(SyncDeliveryGate.shouldMarkSuccess(fetch, ackOk = true, cursorAdvanced = false))
    }

    @Test
    fun fromPendingIsDeliverable() {
        val pending = HealthConnectReader.FetchResult.fromPending(
            observations = emptyList(),
            nextChangesToken = "tok",
            deletedRecordIds = emptyList(),
            tokenScope = "steps"
        )
        assertTrue(pending.queryPerformed)
        assertTrue(SyncDeliveryGate.shouldDeliver(pending))
        assertEquals("steps", pending.proposedTokenScope)
    }

    @Test
    fun fromPendingPreservesPartialWarning() {
        val pending = HealthConnectReader.FetchResult.fromPending(
            observations = emptyList(),
            nextChangesToken = "tok",
            deletedRecordIds = emptyList(),
            tokenScope = "steps",
            partialPermissionWarning = true
        )
        assertTrue(SyncDeliveryGate.shouldDeliver(pending))
        assertTrue(SyncDeliveryGate.visiblePartialWarning(pending))
        assertEquals(QueryDisposition.PERFORMED_PARTIAL, pending.disposition)
    }
}
