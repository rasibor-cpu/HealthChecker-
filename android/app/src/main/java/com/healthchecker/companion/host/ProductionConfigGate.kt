package com.healthchecker.companion.host

import com.healthchecker.companion.BuildConfig

/**
 * Production configuration gate for HC-303B.
 * Release builds fail closed when host URL is missing/cleartext or pairing is absent.
 * Local cleartext host matching uses [LocalCleartextHostPolicy] via [PairingInputs.normalizeOrigin].
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
        // Delivery hosts must already be canonical origins (no userinfo/path/query).
        val origin = PairingInputs.normalizeOrigin(
            hostUrl,
            isDebugBuild = isDebugBuild,
            allowCleartextLocalDev = allowCleartextLocalDev,
        )
        if (origin is PairingInputs.OriginResult.Invalid) {
            return GateResult(false, origin.reason)
        }
        // Release must never embed fixed production hosts or secrets via BuildConfig flags
        if (!isDebugBuild && allowCleartextLocalDev) {
            return GateResult(false, "release_cleartext_flag_forbidden")
        }
        return GateResult(true, null)
    }
}
