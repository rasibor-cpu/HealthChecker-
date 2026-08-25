package com.healthchecker.companion.consumer

import com.healthchecker.companion.host.LocalCleartextHostPolicy
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * HC325-R6 — production consumer WebView origin is fail-closed to the governed
 * public origin. Loopback, emulator gateway, stale prefs, restored WebView
 * history, and untrusted intents must never become the launch URL on a
 * physical production device.
 *
 * Explicit local-dev is opt-in only: debug build + cleartext-local-dev flag +
 * [EXTRA_EXPLICIT_LOCAL_DEV]. Missing that extra never falls back to localhost.
 */
object ConsumerOriginLock {
    const val PRODUCTION_ORIGIN = "https://health.capitalstratasystems.com"
    const val PRODUCTION_MOBILE_PATH = "/mobile"
    const val PRODUCTION_MOBILE_URL = "$PRODUCTION_ORIGIN$PRODUCTION_MOBILE_PATH"

    /** Debug-only. Ignored unless DEBUG && ALLOW_CLEARTEXT_LOCAL_DEV. */
    const val EXTRA_EXPLICIT_LOCAL_DEV =
        "com.healthchecker.companion.EXPLICIT_LOCAL_CONSUMER_DEV"

    /** Used only when explicit local-dev is requested and no valid local candidate exists. */
    const val EXPLICIT_LOCAL_DEV_ORIGIN = "http://127.0.0.1:8766"

    data class LaunchInputs(
        val storedConsumerOrigin: String? = null,
        val intentData: String? = null,
        val restoredWebViewUrl: String? = null,
        val savedStateUrl: String? = null,
        val explicitLocalDevRequested: Boolean = false,
        val isDebugBuild: Boolean = false,
        val allowCleartextLocalDev: Boolean = false,
    )

    data class Resolution(
        val origin: String,
        val mobileUrl: String,
        val usedProductionFallback: Boolean,
        val rejectedCandidate: String?,
        val reason: String,
        val explicitLocalDev: Boolean,
    )

    enum class NavigationDecision {
        ALLOW,
        LOGOUT,
        REJECT_AND_RECOVER,
    }

    fun resolve(inputs: LaunchInputs): Resolution {
        val explicitLocal = inputs.isDebugBuild &&
            inputs.allowCleartextLocalDev &&
            inputs.explicitLocalDevRequested

        if (explicitLocal) {
            val localCandidate = firstCandidate(
                inputs.intentData,
                inputs.storedConsumerOrigin,
                inputs.restoredWebViewUrl,
                inputs.savedStateUrl,
            )
            val localOrigin = permittedLocalOrigin(localCandidate) ?: EXPLICIT_LOCAL_DEV_ORIGIN
            return Resolution(
                origin = localOrigin,
                mobileUrl = "$localOrigin$PRODUCTION_MOBILE_PATH",
                usedProductionFallback = false,
                rejectedCandidate = null,
                reason = "explicit_local_dev",
                explicitLocalDev = true,
            )
        }

        val rejected = firstRejectedProductionCandidate(
            inputs.intentData,
            inputs.storedConsumerOrigin,
            inputs.restoredWebViewUrl,
            inputs.savedStateUrl,
        )
        return Resolution(
            origin = PRODUCTION_ORIGIN,
            mobileUrl = PRODUCTION_MOBILE_URL,
            usedProductionFallback = true,
            rejectedCandidate = rejected,
            reason = if (rejected != null) "rejected_untrusted_or_loopback" else "governed_production",
            explicitLocalDev = false,
        )
    }

    fun decideNavigation(url: String?, policy: ConsumerOriginPolicy): NavigationDecision {
        if (policy.isLogoutCompletion(url)) return NavigationDecision.LOGOUT
        if (policy.isAllowed(url)) return NavigationDecision.ALLOW
        return NavigationDecision.REJECT_AND_RECOVER
    }

    fun mustRecover(url: String?, allowedOrigin: String?): Boolean {
        if (allowedOrigin.isNullOrBlank()) return true
        if (url.isNullOrBlank()) return false
        if (isForbiddenLoopbackUrl(url)) return true
        val parsed = url.toHttpUrlOrNull() ?: return true
        val candidateOrigin = canonicalOrigin(parsed.scheme, parsed.host, parsed.port)
        return candidateOrigin != allowedOrigin
    }

    fun shouldRestoreWebViewState(): Boolean = false

    fun isForbiddenLoopbackUrl(url: String?): Boolean {
        val parsed = url?.toHttpUrlOrNull() ?: return isForbiddenLoopbackHost(url?.let { rawHostHint(it) }.orEmpty())
        return isForbiddenLoopbackHost(parsed.host)
    }

    fun isForbiddenLoopbackHost(host: String): Boolean {
        val h = host.trim().lowercase().removePrefix("[").removeSuffix("]")
        if (h.isEmpty()) return false
        if (h == "localhost" || h == "0.0.0.0" || h == "::1" || h == "10.0.2.2") return true
        val octets = LocalCleartextHostPolicy.parseLiteralIpv4(h) ?: return false
        return octets[0] == 127
    }

    fun isGovernedProductionOrigin(origin: String?): Boolean {
        val normalized = origin?.trim()?.trimEnd('/') ?: return false
        return normalized == PRODUCTION_ORIGIN
    }

    private fun firstRejectedProductionCandidate(vararg values: String?): String? {
        for (value in values) {
            if (value.isNullOrBlank()) continue
            if (!isGovernedProductionCandidate(value)) return value
        }
        return null
    }

    private fun isGovernedProductionCandidate(raw: String): Boolean {
        val trimmed = raw.trim()
        if (isGovernedProductionOrigin(trimmed)) return true
        val parsed = trimmed.toHttpUrlOrNull() ?: return false
        if (parsed.username.isNotEmpty() || parsed.password.isNotEmpty()) return false
        return canonicalOrigin(parsed.scheme, parsed.host, parsed.port) == PRODUCTION_ORIGIN
    }

    private fun permittedLocalOrigin(raw: String?): String? {
        if (raw.isNullOrBlank()) return null
        val parsed = raw.trim().toHttpUrlOrNull() ?: return null
        if (parsed.username.isNotEmpty() || parsed.password.isNotEmpty()) return null
        if (!LocalCleartextHostPolicy.isPermitted(parsed.host)) return null
        if (parsed.scheme.lowercase() != "http" && parsed.scheme.lowercase() != "https") return null
        return canonicalOrigin(parsed.scheme, parsed.host, parsed.port)
    }

    private fun canonicalOrigin(scheme: String, host: String, port: Int): String {
        val schemeLc = scheme.lowercase()
        val defaultPort = if (schemeLc == "https") 443 else 80
        return buildString {
            append(schemeLc)
            append("://")
            append(host)
            if (port != defaultPort) {
                append(':')
                append(port)
            }
        }
    }

    private fun firstCandidate(vararg values: String?): String? =
        values.firstOrNull { !it.isNullOrBlank() }

    private fun rawHostHint(raw: String): String {
        val withoutScheme = raw.substringAfter("://", raw)
        return withoutScheme.substringBefore("/").substringBefore(":")
    }
}
