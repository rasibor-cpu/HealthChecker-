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
    fun passwordAndCredentialSurfacesRemainSecured() {
        assertTrue(SecureWindowPolicy.shouldSecureWindow(loginSurfaceVisible = true))
        assertTrue(SecureWindowPolicy.shouldSecureWindow(passwordChangeVisible = true))
        assertTrue(SecureWindowPolicy.shouldSecureWindow(credentialOrSecretVisible = true))
    }

    @Test
    fun sensitiveSurfaceProbeDoesNotAssumeBlanketSecure() {
        assertTrue(SecureWindowPolicy.SENSITIVE_SURFACE_JS.contains("mobile_login"))
        assertTrue(SecureWindowPolicy.SENSITIVE_SURFACE_JS.contains("mobile_password_change"))
        assertFalse(SecureWindowPolicy.SENSITIVE_SURFACE_JS.contains("FLAG_SECURE"))
    }
}
