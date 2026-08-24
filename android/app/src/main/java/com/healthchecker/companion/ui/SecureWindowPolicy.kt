package com.healthchecker.companion.ui

/**
 * HC322A: ordinary consumer screens allow screenshots/recording.
 * FLAG_SECURE is not applied by the consumer launcher. A single WebView
 * Activity would otherwise keep a login-time secure flag on Dashboard,
 * Health Snapshot, Trends, Observations, Timeline, and Reports.
 *
 * Sensitive-surface names remain documented for tests; they do not enable
 * screenshot blocking.
 */
object SecureWindowPolicy {
    /**
     * @param loginSurfaceVisible unused; login is not automatically secured
     * @param passwordChangeVisible unused; password change is not automatically secured
     * @param credentialOrSecretVisible unused; secrets stay in EncryptedSharedPreferences
     */
    @Suppress("UNUSED_PARAMETER")
    fun shouldSecureWindow(
        loginSurfaceVisible: Boolean = false,
        passwordChangeVisible: Boolean = false,
        credentialOrSecretVisible: Boolean = false,
    ): Boolean {
        return false
    }

    /** Historical DOM probe IDs (not used to set FLAG_SECURE). */
    const val SENSITIVE_SURFACE_JS =
        "(function(){var login=document.getElementById('mobile_login');" +
            "var pw=document.getElementById('mobile_password_change');" +
            "var loginVisible=!!(login&&!login.hidden);" +
            "var pwVisible=!!(pw&&!pw.hidden);" +
            "return !!(loginVisible||pwVisible);})();"
}
