package com.healthchecker.companion.ui

import android.view.Window
import android.view.WindowManager

/**
 * HC-321 screenshot policy for the Android companion.
 *
 * Ordinary consumer-facing HealthChecker screens must remain screenshot-capable.
 * This helper never sets [WindowManager.LayoutParams.FLAG_SECURE].
 *
 * Protected screens (none in this companion module):
 * - Screen: n/a
 * - Mechanism: n/a
 * - Security justification: n/a
 *
 * Pairing tokens and host credentials remain in EncryptedSharedPreferences
 * ([com.healthchecker.companion.secure.SecurePrefs]); screenshot policy is not
 * used to protect those secrets, and is not applied application-wide.
 */
object ScreenshotPolicy {

    /** No companion Activity currently requires screenshot blocking. */
    const val HAS_PROTECTED_SCREENS: Boolean = false

    fun isScreenshotBlockingEnabled(): Boolean = false

    /**
     * Ensure a consumer window can be captured by the standard Android screenshot
     * gesture. Clears FLAG_SECURE if a previous caller set it; does not set it.
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
