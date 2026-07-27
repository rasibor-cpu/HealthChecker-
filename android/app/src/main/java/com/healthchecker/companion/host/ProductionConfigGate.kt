package com.healthchecker.companion.host

import com.healthchecker.companion.BuildConfig

/**
 * Production configuration gate for HC-303B.
 * Release builds fail closed when host URL is missing/cleartext or pairing is absent.
 */
object ProductionConfigGate {
    data class GateResult(val ok: Boolean, val error: String?)

    fun validateDeliveryConfig(
        hostUrl: String?,
        deviceToken: String?,
        isDebugBuild: Boolean = BuildConfig.DEBUG,
        allowCleartextLocalDev: Boolean = BuildConfig.ALLOW_CLEARTEXT_LOCAL_DEV
    ): GateResult {
        if (hostUrl.isNullOrBlank()) {
            return GateResult(false, "host_url_missing")
        }
        if (deviceToken.isNullOrBlank()) {
            return GateResult(false, "not_paired")
        }
        val https = hostUrl.startsWith("https://", ignoreCase = true)
        val localHttp = hostUrl.startsWith("http://127.") ||
            hostUrl.startsWith("http://10.") ||
            hostUrl.startsWith("http://192.168.") ||
            hostUrl.startsWith("http://localhost")
        if (!https) {
            if (!(isDebugBuild && allowCleartextLocalDev && localHttp)) {
                return GateResult(false, "tls_required_outside_local_dev")
            }
        }
        // Release must never embed fixed production hosts or secrets via BuildConfig flags
        if (!isDebugBuild && allowCleartextLocalDev) {
            return GateResult(false, "release_cleartext_flag_forbidden")
        }
        return GateResult(true, null)
    }
}
