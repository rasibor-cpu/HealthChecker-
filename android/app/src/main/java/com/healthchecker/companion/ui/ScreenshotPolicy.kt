package com.healthchecker.companion.ui

import android.view.Window
import android.view.WindowManager

/**
 * HC-322 / HC-322A screenshot policy for Android HealthChecker windows.
 *
 * Ordinary consumer-facing screens must remain screenshot-capable. This helper
 * never sets [WindowManager.LayoutParams.FLAG_SECURE].
 *
 * Protected screen:
 * - Screen: consumer Settings / Password & recovery
 * - Mechanism: FLAG_SECURE while that route is active
 * - Security justification: password and recovery answers are credentials
 *
 * Pairing tokens and host credentials remain in EncryptedSharedPreferences.
 * Screenshot policy is not used to protect those secrets.
 *
 * The consumer WebView is a single Activity, so every route transition and
 * lifecycle resume must explicitly apply the current route policy.
 */
object ScreenshotPolicy {

    const val HAS_PROTECTED_SCREENS: Boolean = true

    fun isScreenshotBlockingEnabled(): Boolean = HAS_PROTECTED_SCREENS

    fun isSensitiveRoute(route: String?): Boolean =
        route in setOf("settings", "password_recovery", "auth_secrets", "totp_setup", "recovery_codes")

    /**
     * Ensure a consumer window can be captured by the standard Android
     * screenshot gesture. Clears FLAG_SECURE if a previous caller set it;
     * does not set it.
     */
    fun applyConsumerScreenshotPolicy(window: Window?, sensitiveScreenVisible: Boolean = false) {
        if (window == null) return
        if (SecureWindowPolicy.shouldSecureWindow(passwordChangeVisible = sensitiveScreenVisible)) {
            window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        } else {
            window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
        }
    }

    fun isFlagSecureSet(window: Window?): Boolean {
        if (window == null) return false
        return window.attributes.flags and WindowManager.LayoutParams.FLAG_SECURE != 0
    }
}
