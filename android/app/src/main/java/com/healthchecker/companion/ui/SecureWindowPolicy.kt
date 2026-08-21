package com.healthchecker.companion.ui

/**
 * HC321-UAT1: ordinary consumer screens allow screenshots/recording.
 * FLAG_SECURE is reserved for genuinely sensitive surfaces only
 * (password entry/change, account recovery, secret/credential display).
 */
object SecureWindowPolicy {
    /**
     * @param loginSurfaceVisible true when the mobile login form is showing
     * @param passwordChangeVisible true when the password-change form is showing
     * @param credentialOrSecretVisible true when pair codes / tokens / secrets are on screen
     */
    fun shouldSecureWindow(
        loginSurfaceVisible: Boolean = false,
        passwordChangeVisible: Boolean = false,
        credentialOrSecretVisible: Boolean = false,
    ): Boolean = loginSurfaceVisible || passwordChangeVisible || credentialOrSecretVisible

    /** DOM probe used by [ConsumerLauncherActivity] (no JavascriptInterface). */
    const val SENSITIVE_SURFACE_JS =
        "(function(){var login=document.getElementById('mobile_login');" +
            "var pw=document.getElementById('mobile_password_change');" +
            "var loginVisible=!!(login&&!login.hidden);" +
            "var pwVisible=!!(pw&&!pw.hidden);" +
            "return !!(loginVisible||pwVisible);})();"
}
