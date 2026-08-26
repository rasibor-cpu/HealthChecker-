package com.healthchecker.companion.consumer

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ConsumerOriginPolicyTest {
    @Test
    fun allowsOnlyApprovedHttpsOriginAndConsumerPaths() {
        val policy = ConsumerOriginPolicy.create(
            "https://health.example:8443",
            isDebugBuild = false,
            allowCleartextLocalDev = false,
        )
        assertNotNull(policy)
        policy!!
        assertTrue(policy.isAllowed("https://health.example:8443/mobile"))
        assertTrue(policy.isAllowed("https://health.example:8443/api/dashboard/summary"))
        assertTrue(policy.isAllowed("https://health.example:8443/api/records?status=imported"))
        assertTrue(policy.isAllowed("https://health.example:8443/js/health_vault/mobile_consumer.js"))
        assertTrue(policy.isAllowed("https://health.example:8443/js/health_vault/health_snapshot.js"))
        assertTrue(policy.isAllowed("https://health.example:8443/js/health_vault/consumer_nav.js"))
        assertTrue(policy.isAllowed("https://health.example:8443/js/health_vault/json_contract.js"))
        assertFalse(policy.isAllowed("https://health.example:8443/mobile#records"))
        assertFalse(policy.isAllowed("https://health.example:8443/"))
        assertFalse(policy.isAllowed("https://health.example:8443/index.html"))
        assertFalse(policy.isAllowed("https://health.example:8443/vault_storage/index.json"))
        assertFalse(policy.isAllowed("https://evil.example/mobile"))
        assertFalse(policy.isAllowed("file:///data/data/health.json"))
        assertFalse(policy.isAllowed("javascript:alert(1)"))
        assertFalse(policy.isAllowed("http://localhost:8766/mobile"))
        assertFalse(policy.isAllowed("http://127.0.0.1:8766/mobile"))
        assertFalse(policy.isAllowed("http://10.0.2.2:8766/mobile"))
    }

    @Test
    fun governedProductionOriginAllowsMobileAndRejectsLoopback() {
        val policy = ConsumerOriginPolicy.create(
            ConsumerOriginLock.PRODUCTION_ORIGIN,
            isDebugBuild = false,
            allowCleartextLocalDev = false,
        )
        assertNotNull(policy)
        assertEquals(ConsumerOriginLock.PRODUCTION_MOBILE_URL, policy!!.mobileUrl())
        assertTrue(policy.isAllowed(ConsumerOriginLock.PRODUCTION_MOBILE_URL))
        assertFalse(policy.isAllowed("http://localhost:8766/mobile"))
    }

    @Test
    fun releaseRejectsCleartextAndDebugAllowsOnlyExplicitLocalDevelopment() {
        assertNull(ConsumerOriginPolicy.create(
            "http://127.0.0.1:8000", isDebugBuild = false, allowCleartextLocalDev = false
        ))
        assertNotNull(ConsumerOriginPolicy.create(
            "http://127.0.0.1:8000", isDebugBuild = true, allowCleartextLocalDev = true
        ))
        assertNull(ConsumerOriginPolicy.create(
            "http://public.example:8000", isDebugBuild = true, allowCleartextLocalDev = true
        ))
    }

    @Test
    fun logoutCompletionIsExactAndSameOrigin() {
        val policy = ConsumerOriginPolicy.create(
            "https://health.example", isDebugBuild = false, allowCleartextLocalDev = false
        )!!
        assertTrue(policy.isLogoutCompletion("https://health.example/mobile/native-logout-complete"))
        assertFalse(policy.isLogoutCompletion("https://evil.example/mobile/native-logout-complete"))
        assertFalse(policy.isLogoutCompletion("https://health.example/mobile/native-logout-complete/extra"))
    }
}
