package com.healthchecker.companion.ui

import android.view.Window
import android.view.WindowManager

/**
 * HC-322 / HC-322A screenshot policy for Android HealthChecker windows.
 *
 * Ordinary consumer-facing screens must remain screenshot-capable. This helper
 * never sets [WindowManager.LayoutParams.FLAG_SECURE].
 *
 * Protected screens (none on this branch):
 * - Screen: n/a
 * - Mechanism: n/a
 * - Security justification: n/a
 *
 * Pairing tokens and host credentials remain in EncryptedSharedPreferences.
 * Screenshot policy is not used to protect those secrets.
 *
 * Login / password-change fields are not automatically screenshot-blocked.
 * The consumer WebView is a single Activity; FLAG_SECURE must not persist
 * onto Welcome/Dashboard, Health Snapshot, or other authenticated surfaces.
 */
object ScreenshotPolicy {

    /** No Activity currently requires screenshot blocking. */
    const val HAS_PROTECTED_SCREENS: Boolean = false

    fun isScreenshotBlockingEnabled(): Boolean = false

    /**
     * Ensure a consumer window can be captured by the standard Android
     * screenshot gesture. Clears FLAG_SECURE if a previous caller set it;
     * does not set it.
     */
    fun applyConsumerScreenshotPolicy(window: Window?) {
        if (window == null) return
        window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
    }

    fun isFlagSecureSet(window: Window?): Boolean {
        if (window == null) return false
        return window.attributes.flags and WindowManager.LayoutParams.FLAG_SECURE != 0
    }
}
