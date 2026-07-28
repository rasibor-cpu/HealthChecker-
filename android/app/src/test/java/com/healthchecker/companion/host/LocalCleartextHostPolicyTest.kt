package com.healthchecker.companion.host

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LocalCleartextHostPolicyTest {
    @Test
    fun acceptsLoopbackLiteralInDebugHttp() {
        assertTrue(LocalCleartextHostPolicy.isPermitted("127.0.0.1"))
        val origin = PairingInputs.normalizeOrigin(
            "http://127.0.0.1:8877",
            isDebugBuild = true,
            allowCleartextLocalDev = true,
        )
        assertTrue(origin is PairingInputs.OriginResult.Ok)
        assertEquals("http://127.0.0.1:8877", (origin as PairingInputs.OriginResult.Ok).origin)
    }

    @Test
    fun acceptsIntendedPrivateLiterals() {
        assertTrue(LocalCleartextHostPolicy.isPermitted("10.0.0.1"))
        assertTrue(LocalCleartextHostPolicy.isPermitted("10.255.255.255"))
        assertTrue(LocalCleartextHostPolicy.isPermitted("192.168.0.1"))
        assertTrue(LocalCleartextHostPolicy.isPermitted("192.168.255.254"))
        assertTrue(LocalCleartextHostPolicy.isPermitted("127.255.255.255"))
        assertTrue(LocalCleartextHostPolicy.isPermitted("localhost"))

        assertTrue(
            PairingInputs.normalizeOrigin(
                "http://192.168.1.10:8000",
                isDebugBuild = true,
                allowCleartextLocalDev = true,
            ) is PairingInputs.OriginResult.Ok,
        )
        assertTrue(
            PairingInputs.normalizeOrigin(
                "http://10.1.2.3:8000",
                isDebugBuild = true,
                allowCleartextLocalDev = true,
            ) is PairingInputs.OriginResult.Ok,
        )
    }

    @Test
    fun rejectsDeceptivePrefixHostnames() {
        assertFalse(LocalCleartextHostPolicy.isPermitted("127.evil.com"))
        assertFalse(LocalCleartextHostPolicy.isPermitted("10.example.com"))
        assertFalse(LocalCleartextHostPolicy.isPermitted("192.168.example.com"))
        assertFalse(LocalCleartextHostPolicy.isPermitted("127.0.0.1.evil.com"))

        assertEquals(
            "host_url_scheme_invalid",
            (PairingInputs.normalizeOrigin(
                "http://127.evil.com",
                isDebugBuild = true,
                allowCleartextLocalDev = true,
            ) as PairingInputs.OriginResult.Invalid).reason,
        )
        assertEquals(
            "host_url_scheme_invalid",
            (PairingInputs.normalizeOrigin(
                "http://10.example.com",
                isDebugBuild = true,
                allowCleartextLocalDev = true,
            ) as PairingInputs.OriginResult.Invalid).reason,
        )
        assertEquals(
            "host_url_scheme_invalid",
            (PairingInputs.normalizeOrigin(
                "http://192.168.example.com",
                isDebugBuild = true,
                allowCleartextLocalDev = true,
            ) as PairingInputs.OriginResult.Invalid).reason,
        )
    }

    @Test
    fun rejectsMalformedAndPrivateLookingAddresses() {
        assertFalse(LocalCleartextHostPolicy.isPermitted("127.0.0"))
        assertFalse(LocalCleartextHostPolicy.isPermitted("127.0.0.1.2"))
        assertFalse(LocalCleartextHostPolicy.isPermitted("127.0.0.256"))
        assertFalse(LocalCleartextHostPolicy.isPermitted("127.0.0.-1"))
        assertFalse(LocalCleartextHostPolicy.isPermitted("127.0.0.01"))
        assertFalse(LocalCleartextHostPolicy.isPermitted("192.168.1."))
        assertFalse(LocalCleartextHostPolicy.isPermitted(".192.168.1.1"))
        assertFalse(LocalCleartextHostPolicy.isPermitted("172.16.0.1")) // not in prior gate
        assertFalse(LocalCleartextHostPolicy.isPermitted("::1"))
        assertFalse(LocalCleartextHostPolicy.isPermitted("[::1]"))
    }

    @Test
    fun rejectsPublicIpForDebugHttp() {
        assertFalse(LocalCleartextHostPolicy.isPermitted("8.8.8.8"))
        assertFalse(LocalCleartextHostPolicy.isPermitted("1.1.1.1"))
        assertFalse(LocalCleartextHostPolicy.isPermitted("203.0.113.10"))
        assertEquals(
            "host_url_scheme_invalid",
            (PairingInputs.normalizeOrigin(
                "http://8.8.8.8:8000",
                isDebugBuild = true,
                allowCleartextLocalDev = true,
            ) as PairingInputs.OriginResult.Invalid).reason,
        )
    }

    @Test
    fun httpsAcceptedUnderNormalPolicy() {
        assertTrue(
            PairingInputs.normalizeOrigin(
                "https://vault.example",
                isDebugBuild = false,
                allowCleartextLocalDev = false,
            ) is PairingInputs.OriginResult.Ok,
        )
        assertTrue(
            PairingInputs.normalizeOrigin(
                "https://8.8.8.8",
                isDebugBuild = false,
                allowCleartextLocalDev = false,
            ) is PairingInputs.OriginResult.Ok,
        )
        assertTrue(
            ProductionConfigGate.validateDeliveryConfig(
                hostUrl = "https://vault.example",
                deviceToken = "tok",
                isDebugBuild = false,
                allowCleartextLocalDev = false,
            ).ok,
        )
    }

    @Test
    fun releaseHttpRejected() {
        assertEquals(
            "host_url_scheme_invalid",
            (PairingInputs.normalizeOrigin(
                "http://127.0.0.1:8877",
                isDebugBuild = false,
                allowCleartextLocalDev = false,
            ) as PairingInputs.OriginResult.Invalid).reason,
        )
        assertFalse(
            ProductionConfigGate.validateDeliveryConfig(
                hostUrl = "http://192.168.1.10:8000",
                deviceToken = "tok",
                isDebugBuild = false,
                allowCleartextLocalDev = false,
            ).ok,
        )
    }

    @Test
    fun pairingInputsAndGateSharePolicy() {
        // Same implementation path — PairingInputs delegates; gate uses normalizeOrigin.
        assertEquals(
            LocalCleartextHostPolicy.isPermitted("127.evil.com"),
            PairingInputs.isPermittedLocalCleartextHost("127.evil.com"),
        )
        assertEquals(
            LocalCleartextHostPolicy.isPermitted("192.168.1.1"),
            PairingInputs.isPermittedLocalCleartextHost("192.168.1.1"),
        )
    }
}
