package com.healthchecker.companion.host

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PairingInputsTest {
    @Test
    fun rejectsBlankHost() {
        val result = PairingInputs.normalize("   ", "ABCD1234", isDebugBuild = true, allowCleartextLocalDev = true)
        assertTrue(result is PairingInputs.Result.Invalid)
        assertEquals("host_url_required", (result as PairingInputs.Result.Invalid).reason)
    }

    @Test
    fun rejectsMissingScheme() {
        val result = PairingInputs.normalize(
            "127.0.0.1:8877",
            "ABCD1234",
            isDebugBuild = true,
            allowCleartextLocalDev = true,
        )
        assertTrue(result is PairingInputs.Result.Invalid)
        val reason = (result as PairingInputs.Result.Invalid).reason
        assertTrue(
            reason == "host_url_scheme_invalid" || reason == "host_url_malformed",
        )
    }

    @Test
    fun rejectsBlankCode() {
        val result = PairingInputs.normalize(
            "http://127.0.0.1:8877",
            "  ",
            isDebugBuild = true,
            allowCleartextLocalDev = true,
        )
        assertTrue(result is PairingInputs.Result.Invalid)
        assertEquals("pair_code_required", (result as PairingInputs.Result.Invalid).reason)
    }

    @Test
    fun acceptsLoopbackHttpAndStripsZeroWidth() {
        val dirty = "\u200Bhttp://127.0.0.1:8877\uFEFF/"
        val result = PairingInputs.normalize(
            dirty,
            " ab12cd34 ",
            isDebugBuild = true,
            allowCleartextLocalDev = true,
        )
        assertTrue(result is PairingInputs.Result.Normalized)
        val ok = result as PairingInputs.Result.Normalized
        assertEquals("http://127.0.0.1:8877", ok.hostUrl)
        assertEquals("ab12cd34", ok.pairCode)
    }

    @Test
    fun rejectsUserinfoAttackDisguisedAsLocal() {
        val result = PairingInputs.normalizeOrigin(
            "https://127.0.0.1@evil.example",
            isDebugBuild = true,
            allowCleartextLocalDev = true,
        )
        assertTrue(result is PairingInputs.OriginResult.Invalid)
        assertEquals(
            "host_url_userinfo_forbidden",
            (result as PairingInputs.OriginResult.Invalid).reason,
        )
    }

    @Test
    fun rejectsPathQueryAndFragment() {
        assertEquals(
            "host_url_path_forbidden",
            (PairingInputs.normalizeOrigin(
                "https://vault.example/api",
                isDebugBuild = false,
                allowCleartextLocalDev = false,
            ) as PairingInputs.OriginResult.Invalid).reason,
        )
        assertEquals(
            "host_url_query_forbidden",
            (PairingInputs.normalizeOrigin(
                "https://vault.example?x=1",
                isDebugBuild = false,
                allowCleartextLocalDev = false,
            ) as PairingInputs.OriginResult.Invalid).reason,
        )
        assertEquals(
            "host_url_fragment_forbidden",
            (PairingInputs.normalizeOrigin(
                "https://vault.example#frag",
                isDebugBuild = false,
                allowCleartextLocalDev = false,
            ) as PairingInputs.OriginResult.Invalid).reason,
        )
    }

    @Test
    fun rejectsWhitespaceAndControlCharacters() {
        assertEquals(
            "host_url_invalid",
            (PairingInputs.normalizeOrigin(
                "https://vault .example",
                isDebugBuild = false,
                allowCleartextLocalDev = false,
            ) as PairingInputs.OriginResult.Invalid).reason,
        )
        assertEquals(
            "host_url_invalid",
            (PairingInputs.normalizeOrigin(
                "https://va\u0001ult.example",
                isDebugBuild = false,
                allowCleartextLocalDev = false,
            ) as PairingInputs.OriginResult.Invalid).reason,
        )
        assertEquals(
            "host_url_invalid",
            (PairingInputs.normalizeOrigin(
                "https://vault.example\u0000",
                isDebugBuild = false,
                allowCleartextLocalDev = false,
            ) as PairingInputs.OriginResult.Invalid).reason,
        )
    }

    @Test
    fun canonicalizesHostAndPort() {
        val httpsDefault = PairingInputs.normalizeOrigin(
            "HTTPS://Vault.Example:443/",
            isDebugBuild = false,
            allowCleartextLocalDev = false,
        )
        assertTrue(httpsDefault is PairingInputs.OriginResult.Ok)
        assertEquals("https://vault.example", (httpsDefault as PairingInputs.OriginResult.Ok).origin)

        val customPort = PairingInputs.normalizeOrigin(
            "https://vault.example:8443/",
            isDebugBuild = false,
            allowCleartextLocalDev = false,
        )
        assertEquals("https://vault.example:8443", (customPort as PairingInputs.OriginResult.Ok).origin)

        val loopback = PairingInputs.normalizeOrigin(
            "http://127.0.0.1:8877/",
            isDebugBuild = true,
            allowCleartextLocalDev = true,
        )
        assertEquals("http://127.0.0.1:8877", (loopback as PairingInputs.OriginResult.Ok).origin)
    }

    @Test
    fun releaseHttpsOnlyRejectsCleartext() {
        val result = PairingInputs.normalizeOrigin(
            "http://127.0.0.1:8877",
            isDebugBuild = false,
            allowCleartextLocalDev = false,
        )
        assertTrue(result is PairingInputs.OriginResult.Invalid)
        assertEquals("host_url_scheme_invalid", (result as PairingInputs.OriginResult.Invalid).reason)
    }

    @Test
    fun debugPermitsLoopbackHttp() {
        val result = PairingInputs.normalizeOrigin(
            "http://127.0.0.1:8877",
            isDebugBuild = true,
            allowCleartextLocalDev = true,
        )
        assertTrue(result is PairingInputs.OriginResult.Ok)
        assertEquals("http://127.0.0.1:8877", (result as PairingInputs.OriginResult.Ok).origin)
    }

    @Test
    fun rejectsSchemeRelativeAndUnsupportedSchemes() {
        assertEquals(
            "host_url_scheme_invalid",
            (PairingInputs.normalizeOrigin(
                "//evil.example",
                isDebugBuild = true,
                allowCleartextLocalDev = true,
            ) as PairingInputs.OriginResult.Invalid).reason,
        )
        val ftp = PairingInputs.normalizeOrigin(
            "ftp://vault.example",
            isDebugBuild = true,
            allowCleartextLocalDev = true,
        )
        assertTrue(ftp is PairingInputs.OriginResult.Invalid)
        val ftpReason = (ftp as PairingInputs.OriginResult.Invalid).reason
        assertTrue(
            ftpReason == "host_url_scheme_invalid" || ftpReason == "host_url_malformed",
        )
    }
}
