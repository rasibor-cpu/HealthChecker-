package com.healthchecker.companion.consumer

import com.healthchecker.companion.host.PairingInputs
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/** Exact-origin and path allowlist for the HC-319C API-only consumer surface. */
class ConsumerOriginPolicy private constructor(val origin: String) {
    fun isAllowed(url: String?): Boolean {
        val parsed = url?.toHttpUrlOrNull() ?: return false
        val candidateOrigin = buildString {
            append(parsed.scheme)
            append("://")
            append(parsed.host)
            val defaultPort = if (parsed.scheme == "https") 443 else 80
            if (parsed.port != defaultPort) append(":${parsed.port}")
        }
        if (candidateOrigin != origin || parsed.username.isNotEmpty() || parsed.password.isNotEmpty()) {
            return false
        }
        if (parsed.fragment != null) return false
        val path = parsed.encodedPath
        return path == "/mobile" ||
            path == "/mobile.html" ||
            path == "/mobile/native-logout-complete" ||
            path == "/style.css" ||
            path == "/js/health_vault/mobile_consumer.js" ||
            path == "/js/health_vault/health_snapshot.js" ||
            path == "/js/health_vault/consumer_nav.js" ||
            path == "/js/health_vault/json_contract.js" ||
            path.startsWith("/api/")
    }

    fun isLogoutCompletion(url: String?): Boolean {
        val parsed = url?.toHttpUrlOrNull() ?: return false
        return isAllowed(url) && parsed.encodedPath == "/mobile/native-logout-complete"
    }

    fun mobileUrl(): String = "$origin/mobile"

    companion object {
        fun create(
            rawOrigin: String?,
            isDebugBuild: Boolean,
            allowCleartextLocalDev: Boolean,
        ): ConsumerOriginPolicy? {
            return when (val normalized = PairingInputs.normalizeOrigin(
                rawOrigin,
                isDebugBuild = isDebugBuild,
                allowCleartextLocalDev = allowCleartextLocalDev,
            )) {
                is PairingInputs.OriginResult.Ok -> ConsumerOriginPolicy(normalized.origin)
                is PairingInputs.OriginResult.Invalid -> null
            }
        }
    }
}
