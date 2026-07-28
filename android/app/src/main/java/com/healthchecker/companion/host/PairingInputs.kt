package com.healthchecker.companion.host

import com.healthchecker.companion.BuildConfig
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * Normalizes and validates pairing form inputs before any network call.
 * Structured URL parsing rejects userinfo / path / query / fragment attacks.
 */
object PairingInputs {
    sealed class Result {
        data class Normalized(
            val hostUrl: String,
            val pairCode: String,
        ) : Result()

        data class Invalid(val reason: String) : Result()
    }

    fun normalize(
        hostUrl: String?,
        pairCode: String?,
        isDebugBuild: Boolean = BuildConfig.DEBUG,
        allowCleartextLocalDev: Boolean = BuildConfig.ALLOW_CLEARTEXT_LOCAL_DEV,
    ): Result {
        val origin = normalizeOrigin(
            hostUrl,
            isDebugBuild = isDebugBuild,
            allowCleartextLocalDev = allowCleartextLocalDev,
        )
        if (origin is OriginResult.Invalid) return Result.Invalid(origin.reason)
        val code = (pairCode ?: "").trim()
        if (code.isEmpty()) return Result.Invalid("pair_code_required")
        if (code.length > 32) return Result.Invalid("pair_code_invalid")
        // Pairing codes are never persisted by this helper — callers must keep them ephemeral.
        return Result.Normalized(hostUrl = (origin as OriginResult.Ok).origin, pairCode = code)
    }

    sealed class OriginResult {
        data class Ok(val origin: String) : OriginResult()
        data class Invalid(val reason: String) : OriginResult()
    }

    /**
     * Canonical origin only: scheme://host[:port]
     * Rejects userinfo, non-root paths, query, fragment, and ambiguous authorities.
     */
    fun normalizeOrigin(
        raw: String?,
        isDebugBuild: Boolean = BuildConfig.DEBUG,
        allowCleartextLocalDev: Boolean = BuildConfig.ALLOW_CLEARTEXT_LOCAL_DEV,
    ): OriginResult {
        val cleaned = sanitizeHost(raw)
        if (cleaned.isEmpty()) return OriginResult.Invalid("host_url_required")
        if (cleaned.any { it.isWhitespace() || it.code < 0x20 }) {
            return OriginResult.Invalid("host_url_invalid")
        }
        // Scheme-relative and unsupported schemes fail parse or scheme check.
        if (cleaned.startsWith("//")) return OriginResult.Invalid("host_url_scheme_invalid")

        val parsed: HttpUrl = cleaned.toHttpUrlOrNull()
            ?: return OriginResult.Invalid("host_url_malformed")

        if (!parsed.username.isEmpty() || !parsed.password.isEmpty()) {
            return OriginResult.Invalid("host_url_userinfo_forbidden")
        }
        if (parsed.fragment != null) {
            return OriginResult.Invalid("host_url_fragment_forbidden")
        }
        if (parsed.querySize > 0 || parsed.encodedQuery != null) {
            return OriginResult.Invalid("host_url_query_forbidden")
        }
        // Encoded path must be root-only ("" or "/").
        val nonEmptySegments = parsed.pathSegments.filter { it.isNotEmpty() }
        if (nonEmptySegments.isNotEmpty()) {
            return OriginResult.Invalid("host_url_path_forbidden")
        }
        if (parsed.encodedPath.isNotEmpty() && parsed.encodedPath != "/") {
            return OriginResult.Invalid("host_url_path_forbidden")
        }

        val scheme = parsed.scheme.lowercase()
        val host = parsed.host
        if (host.isBlank()) return OriginResult.Invalid("host_url_malformed")

        when (scheme) {
            "https" -> Unit
            "http" -> {
                if (!(isDebugBuild && allowCleartextLocalDev && isPermittedLocalCleartextHost(host))) {
                    return OriginResult.Invalid("host_url_scheme_invalid")
                }
            }
            else -> return OriginResult.Invalid("host_url_scheme_invalid")
        }

        val origin = buildString {
            append(scheme)
            append("://")
            append(host)
            val port = parsed.port
            val defaultPort = if (scheme == "https") 443 else 80
            if (port != defaultPort) {
                append(':')
                append(port)
            }
        }
        return OriginResult.Ok(origin)
    }

    fun sanitizeHost(raw: String?): String {
        if (raw.isNullOrBlank()) return ""
        // Strip paste-hygiene invisibles only; C0 controls / embedded whitespace are rejected later.
        return raw
            .filter { ch -> ch.code !in setOf(0x200B, 0x200C, 0x200D, 0xFEFF, 0x00A0) }
            .trim()
            .trimEnd('/')
    }

    /** Delegates to [LocalCleartextHostPolicy] — shared with [ProductionConfigGate]. */
    fun isPermittedLocalCleartextHost(host: String): Boolean =
        LocalCleartextHostPolicy.isPermitted(host)
}
