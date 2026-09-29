package com.healthchecker.companion.ui

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SecureWindowPolicyTest {
    @Test
    fun ordinaryConsumerScreensAreNotSecured() {
        assertFalse(SecureWindowPolicy.shouldSecureWindow())
        assertFalse(
            SecureWindowPolicy.shouldSecureWindow(
                loginSurfaceVisible = false,
                passwordChangeVisible = false,
                credentialOrSecretVisible = false,
            )
        )
    }

    @Test
    fun ordinaryScreensAreNotSecureAndPasswordRecoveryIsSecure() {
        assertFalse(SecureWindowPolicy.shouldSecureWindow(loginSurfaceVisible = true))
        assertTrue(SecureWindowPolicy.shouldSecureWindow(passwordChangeVisible = true))
        assertTrue(SecureWindowPolicy.shouldSecureWindow(credentialOrSecretVisible = true))
        assertTrue(ScreenshotPolicy.isScreenshotBlockingEnabled())
        assertTrue(ScreenshotPolicy.HAS_PROTECTED_SCREENS)
    }

    @Test
    fun onlySettingsAndCredentialEntryRoutesAreSensitive() {
        assertTrue(ScreenshotPolicy.isSensitiveRoute("settings"))
        assertTrue(ScreenshotPolicy.isSensitiveRoute("password_recovery"))
        assertFalse(ScreenshotPolicy.isSensitiveRoute("dashboard"))
        assertFalse(ScreenshotPolicy.isSensitiveRoute("login"))
        assertFalse(ScreenshotPolicy.isSensitiveRoute(null))
    }

    @Test
    fun passwordRecoveryRouteProbeIsDesignatedSensitive() {
        assertTrue(SecureWindowPolicy.SENSITIVE_SURFACE_JS.contains("mobile_settings"))
        assertTrue(SecureWindowPolicy.SENSITIVE_SURFACE_JS.contains("mobile_recovery_flow"))
        assertTrue(SecureWindowPolicy.SENSITIVE_SURFACE_JS.contains("mobile_recovery_enroll"))
        assertTrue(SecureWindowPolicy.shouldSecureWindow(passwordChangeVisible = true))
        assertFalse(SecureWindowPolicy.shouldSecureWindow(passwordChangeVisible = false))
    }

    @Test
    fun sensitiveSurfaceProbeDoesNotAssumeBlanketSecure() {
        assertTrue(SecureWindowPolicy.SENSITIVE_SURFACE_JS.contains("mobile_login"))
        assertTrue(SecureWindowPolicy.SENSITIVE_SURFACE_JS.contains("mobile_password_change"))
        assertFalse(SecureWindowPolicy.SENSITIVE_SURFACE_JS.contains("FLAG_SECURE"))
    }
}
