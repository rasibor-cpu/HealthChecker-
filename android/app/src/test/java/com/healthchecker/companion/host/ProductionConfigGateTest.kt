package com.healthchecker.companion.host

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ProductionConfigGateTest {
    @Test
    fun releaseRejectsCleartextAndMissingHost() {
        val missing = ProductionConfigGate.validateDeliveryConfig(
            hostUrl = null,
            deviceToken = "tok",
            isDebugBuild = false,
            allowCleartextLocalDev = false
        )
        assertFalse(missing.ok)
        assertEquals("host_url_missing", missing.error)

        val cleartext = ProductionConfigGate.validateDeliveryConfig(
            hostUrl = "http://192.168.1.10:8000",
            deviceToken = "tok",
            isDebugBuild = false,
            allowCleartextLocalDev = false
        )
        assertFalse(cleartext.ok)
        assertEquals("tls_required_outside_local_dev", cleartext.error)
    }

    @Test
    fun releaseRejectsMissingTokenAndHttpProductionHost() {
        val missingTok = ProductionConfigGate.validateDeliveryConfig(
            hostUrl = "https://vault.example",
            deviceToken = null,
            isDebugBuild = false,
            allowCleartextLocalDev = false
        )
        assertFalse(missingTok.ok)
        assertEquals("not_paired", missingTok.error)

        val httpProd = ProductionConfigGate.validateDeliveryConfig(
            hostUrl = "http://vault.example",
            deviceToken = "tok",
            isDebugBuild = false,
            allowCleartextLocalDev = false
        )
        assertFalse(httpProd.ok)
        assertEquals("tls_required_outside_local_dev", httpProd.error)
    }

    @Test
    fun debugAllowsDocumentedLocalCleartext() {
        val ok = ProductionConfigGate.validateDeliveryConfig(
            hostUrl = "http://192.168.1.10:8000",
            deviceToken = "tok",
            isDebugBuild = true,
            allowCleartextLocalDev = true
        )
        assertTrue(ok.ok)
    }

    @Test
    fun releaseForbidsCleartextFlagEvenIfHttps() {
        val bad = ProductionConfigGate.validateDeliveryConfig(
            hostUrl = "https://example.invalid",
            deviceToken = "tok",
            isDebugBuild = false,
            allowCleartextLocalDev = true
        )
        assertFalse(bad.ok)
        assertEquals("release_cleartext_flag_forbidden", bad.error)
    }
}
