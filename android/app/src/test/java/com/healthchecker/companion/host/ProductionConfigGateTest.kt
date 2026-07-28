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
        assertEquals("host_url_scheme_invalid", cleartext.error)
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
        assertEquals("host_url_scheme_invalid", httpProd.error)
    }

    @Test
    fun releaseRejectsUserinfoAndPathOnDeliveryHost() {
        val userinfo = ProductionConfigGate.validateDeliveryConfig(
            hostUrl = "https://127.0.0.1@evil.example",
            deviceToken = "tok",
            isDebugBuild = false,
            allowCleartextLocalDev = false,
        )
        assertFalse(userinfo.ok)
        assertEquals("host_url_userinfo_forbidden", userinfo.error)

        val path = ProductionConfigGate.validateDeliveryConfig(
            hostUrl = "https://vault.example/api",
            deviceToken = "tok",
            isDebugBuild = false,
            allowCleartextLocalDev = false,
        )
        assertFalse(path.ok)
        assertEquals("host_url_path_forbidden", path.error)
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
