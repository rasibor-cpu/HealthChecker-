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
    fun loginAndPasswordSurfacesDoNotRequestFlagSecure() {
        assertFalse(SecureWindowPolicy.shouldSecureWindow(loginSurfaceVisible = true))
        assertFalse(SecureWindowPolicy.shouldSecureWindow(passwordChangeVisible = true))
        assertFalse(SecureWindowPolicy.shouldSecureWindow(credentialOrSecretVisible = true))
        assertFalse(ScreenshotPolicy.isScreenshotBlockingEnabled())
        assertFalse(ScreenshotPolicy.HAS_PROTECTED_SCREENS)
    }

    @Test
    fun sensitiveSurfaceProbeDoesNotAssumeBlanketSecure() {
        assertTrue(SecureWindowPolicy.SENSITIVE_SURFACE_JS.contains("mobile_login"))
        assertTrue(SecureWindowPolicy.SENSITIVE_SURFACE_JS.contains("mobile_password_change"))
        assertFalse(SecureWindowPolicy.SENSITIVE_SURFACE_JS.contains("FLAG_SECURE"))
    }
}
