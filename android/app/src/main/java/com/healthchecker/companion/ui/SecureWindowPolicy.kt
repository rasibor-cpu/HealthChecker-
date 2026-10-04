package com.healthchecker.companion.ui

/**
 * HC322A / HC351: ordinary consumer screens allow screenshots/recording;
 * Password & recovery is the designated sensitive WebView surface.
 */
object SecureWindowPolicy {
    /**
     * @param loginSurfaceVisible unused; login is not automatically secured
    * @param passwordChangeVisible Password & recovery is visible.
    * @param credentialOrSecretVisible Reserved for independently sensitive surfaces.
     */
    fun shouldSecureWindow(
        loginSurfaceVisible: Boolean = false,
        passwordChangeVisible: Boolean = false,
        credentialOrSecretVisible: Boolean = false,
    ): Boolean {
        return passwordChangeVisible || credentialOrSecretVisible
    }

    /** Route-aware DOM probe used to restore policy after lifecycle transitions. */
    const val SENSITIVE_SURFACE_JS =
        "(function(){var login=document.getElementById('mobile_login');" +
            "var pw=document.getElementById('mobile_password_change');" +
            "var recovery=document.getElementById('mobile_recovery_flow');" +
            "var enroll=document.getElementById('mobile_recovery_enroll');" +
            "var mfa=document.getElementById('mobile_totp_challenge');" +
            "var setup=document.getElementById('mobile_totp_setup_details');" +
            "var codes=document.getElementById('mobile_recovery_codes');" +
            "var settings=document.getElementById('mobile_settings');" +
            "var loginVisible=!!(login&&!login.hidden);" +
            "var pwVisible=!!(loginVisible&&pw&&!pw.hidden);" +
            "var recoveryVisible=!!(loginVisible&&recovery&&!recovery.hidden);" +
            "var enrollVisible=!!(loginVisible&&enroll&&!enroll.hidden);" +
            "var settingsVisible=!!(settings&&!settings.hidden);" +
            "var mfaVisible=!!(loginVisible&&mfa&&!mfa.hidden);" +
            "var setupVisible=!!(settingsVisible&&setup&&!setup.hidden);" +
            "var codesVisible=!!(settingsVisible&&codes&&!codes.hidden);" +
            "return !!(settingsVisible||pwVisible||recoveryVisible||enrollVisible||mfaVisible||setupVisible||codesVisible);})();"
}
