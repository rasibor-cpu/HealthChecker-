package com.healthchecker.companion.consumer

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ConsumerOriginLockTest {
    private val production = ConsumerOriginLock.PRODUCTION_ORIGIN
    private val mobile = ConsumerOriginLock.PRODUCTION_MOBILE_URL

    private fun resolve(inputs: ConsumerOriginLock.LaunchInputs) = ConsumerOriginLock.resolve(inputs)

    @Test
    fun productionOriginIsGovernedPublicHttpsMobile() {
        val cold = resolve(ConsumerOriginLock.LaunchInputs())
        assertEquals(production, cold.origin)
        assertEquals(mobile, cold.mobileUrl)
        assertEquals("https://health.capitalstratasystems.com/mobile", cold.mobileUrl)
        assertFalse(cold.explicitLocalDev)
        assertTrue(ConsumerOriginLock.isGovernedProductionOrigin(production))
    }

    @Test
    fun debugBuildWithoutExplicitExtraNeverFallsBackToLocalhost() {
        val result = resolve(
            ConsumerOriginLock.LaunchInputs(
                storedConsumerOrigin = null,
                isDebugBuild = true,
                allowCleartextLocalDev = true,
                explicitLocalDevRequested = false,
            )
        )
        assertEquals(mobile, result.mobileUrl)
        assertNotEquals("http://localhost:8766", result.origin)
        assertFalse(result.origin.contains("localhost"))
    }

    @Test
    fun localhostPreferenceIsRejected() {
        val result = resolve(
            ConsumerOriginLock.LaunchInputs(
                storedConsumerOrigin = "http://localhost:8766",
                isDebugBuild = true,
                allowCleartextLocalDev = true,
            )
        )
        assertEquals(mobile, result.mobileUrl)
        assertEquals("http://localhost:8766", result.rejectedCandidate)
        assertTrue(ConsumerOriginLock.isForbiddenLoopbackUrl("http://localhost:8766/mobile"))
        assertTrue(ConsumerOriginLock.isForbiddenLoopbackUrl("https://localhost/mobile"))
    }

    @Test
    fun loopback127IsRejected() {
        for (candidate in listOf(
            "http://127.0.0.1:8766",
            "https://127.0.0.1",
            "http://127.0.0.1/mobile",
        )) {
            val result = resolve(ConsumerOriginLock.LaunchInputs(storedConsumerOrigin = candidate))
            assertEquals(mobile, result.mobileUrl)
            assertTrue(ConsumerOriginLock.isForbiddenLoopbackUrl(candidate))
        }
    }

    @Test
    fun emulatorLoopbackIsRejected() {
        val result = resolve(
            ConsumerOriginLock.LaunchInputs(
                restoredWebViewUrl = "http://10.0.2.2:8766/mobile",
                isDebugBuild = true,
                allowCleartextLocalDev = true,
            )
        )
        assertEquals(mobile, result.mobileUrl)
        assertTrue(ConsumerOriginLock.isForbiddenLoopbackHost("10.0.2.2"))
        assertTrue(ConsumerOriginLock.isForbiddenLoopbackHost("0.0.0.0"))
    }

    @Test
    fun staleWebViewStateCannotOverrideProduction() {
        val result = resolve(
            ConsumerOriginLock.LaunchInputs(
                restoredWebViewUrl = "http://localhost:8766/mobile",
                savedStateUrl = "http://localhost:8766/mobile",
            )
        )
        assertEquals(mobile, result.mobileUrl)
        assertFalse(ConsumerOriginLock.shouldRestoreWebViewState())
        assertTrue(ConsumerOriginLock.mustRecover("http://localhost:8766/mobile", production))
        assertFalse(ConsumerOriginLock.mustRecover(mobile, production))
    }

    @Test
    fun savedStateLoopbackIsRejected() {
        val result = resolve(
            ConsumerOriginLock.LaunchInputs(savedStateUrl = "http://127.0.0.1:8766/mobile")
        )
        assertEquals(mobile, result.mobileUrl)
        assertEquals("http://127.0.0.1:8766/mobile", result.rejectedCandidate)
    }

    @Test
    fun deeplinkLoopbackIsRejected() {
        val result = resolve(
            ConsumerOriginLock.LaunchInputs(intentData = "http://localhost:8766/mobile")
        )
        assertEquals(mobile, result.mobileUrl)
        val policy = ConsumerOriginPolicy.create(
            production, isDebugBuild = false, allowCleartextLocalDev = false
        )
        assertNotNull(policy)
        assertEquals(
            ConsumerOriginLock.NavigationDecision.REJECT_AND_RECOVER,
            ConsumerOriginLock.decideNavigation("http://localhost:8766/mobile", policy!!),
        )
        assertEquals(
            ConsumerOriginLock.NavigationDecision.ALLOW,
            ConsumerOriginLock.decideNavigation(mobile, policy),
        )
    }

    @Test
    fun androidBackMustRecoverFromLoopbackAndStayOnProduction() {
        assertTrue(ConsumerOriginLock.mustRecover("http://localhost:8766/mobile", production))
        assertFalse(ConsumerOriginLock.mustRecover(mobile, production))
        val policy = ConsumerOriginPolicy.create(
            production, isDebugBuild = false, allowCleartextLocalDev = false
        )!!
        assertEquals(
            ConsumerOriginLock.NavigationDecision.REJECT_AND_RECOVER,
            ConsumerOriginLock.decideNavigation("http://127.0.0.1:8766/mobile", policy),
        )
    }

    @Test
    fun coldLaunchUsesGovernedMobileUrl() {
        val cold = resolve(ConsumerOriginLock.LaunchInputs())
        assertEquals(mobile, cold.mobileUrl)
        assertEquals("governed_production", cold.reason)
    }

    @Test
    fun warmLaunchReassertsProductionAndDoesNotKeepLocalhost() {
        val warm = resolve(
            ConsumerOriginLock.LaunchInputs(
                storedConsumerOrigin = "http://localhost:8766",
                restoredWebViewUrl = "http://localhost:8766/mobile",
            )
        )
        assertEquals(mobile, warm.mobileUrl)
        assertTrue(warm.usedProductionFallback)
    }

    @Test
    fun releaseIgnoresExplicitLocalDevExtra() {
        val result = resolve(
            ConsumerOriginLock.LaunchInputs(
                storedConsumerOrigin = "http://localhost:8766",
                explicitLocalDevRequested = true,
                isDebugBuild = false,
                allowCleartextLocalDev = false,
            )
        )
        assertEquals(mobile, result.mobileUrl)
        assertFalse(result.explicitLocalDev)
    }

    @Test
    fun explicitLocalDevIsIsolatedToDebugOptIn() {
        val result = resolve(
            ConsumerOriginLock.LaunchInputs(
                storedConsumerOrigin = "http://127.0.0.1:8766",
                explicitLocalDevRequested = true,
                isDebugBuild = true,
                allowCleartextLocalDev = true,
            )
        )
        assertTrue(result.explicitLocalDev)
        assertEquals("http://127.0.0.1:8766", result.origin)
        assertEquals("http://127.0.0.1:8766/mobile", result.mobileUrl)
    }

    @Test
    fun explicitLocalDevWithoutCandidateDoesNotInventLocalhostHostname() {
        val result = resolve(
            ConsumerOriginLock.LaunchInputs(
                explicitLocalDevRequested = true,
                isDebugBuild = true,
                allowCleartextLocalDev = true,
            )
        )
        assertEquals(ConsumerOriginLock.EXPLICIT_LOCAL_DEV_ORIGIN, result.origin)
        assertFalse(result.origin.contains("localhost"))
    }

    @Test
    fun pairedNonProductionHttpsHostDoesNotOverrideGovernedOrigin() {
        val result = resolve(
            ConsumerOriginLock.LaunchInputs(storedConsumerOrigin = "https://evil.example")
        )
        assertEquals(mobile, result.mobileUrl)
        assertEquals("https://evil.example", result.rejectedCandidate)
    }
}
