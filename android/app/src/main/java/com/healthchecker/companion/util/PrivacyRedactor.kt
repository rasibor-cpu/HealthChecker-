package com.healthchecker.companion.util

/** Pure redaction helpers — safe for JVM unit tests without Android runtime. */
object PrivacyRedactor {
    fun redact(message: String): String {
        var out = message
        // Multiline Authorization: Bearer / Authorization=Bearer (including partial/malformed values)
        out = Regex("(?is)authorization\\s*[:=]\\s*bearer.{0,512}")
            .replace(out, "authorization=[redacted]")
        out = Regex("(?i)(authorization|token|access_token|refresh_token|device_token|pair_code|password|secret|api_key)\\s*[:=]\\s*\\S+")
            .replace(out, "$1=[redacted]")
        out = Regex("(?i)\"(authorization|token|access_token|refresh_token|device_token|pair_code|password|secret|api_key)\"\\s*:\\s*\"[^\"]*\"")
            .replace(out, "\"$1\":\"[redacted]\"")
        out = Regex("(?is)\\bbearer\\s+\\S+")
            .replace(out, "bearer [redacted]")
        // URL userinfo and query credential/token parameters
        out = Regex("(?i)(https?://)[^\\s/@:]+:[^\\s/@]+@")
            .replace(out, "$1[redacted]@")
        out = Regex("(?i)([?&](?:token|access_token|auth|code|pair_code|device_token|api_key|key)=)[^&\\s\"']+")
            .replace(out, "$1[redacted]")
        // Sensitive identifiers — full values redacted (no prefix/suffix retained)
        out = Regex("(?i)\\bdevice_id[_a-z0-9]*\\s*[:=]\\s*\\S+")
            .replace(out, "device_id=[redacted]")
        out = Regex("(?i)\"device_id\"\\s*:\\s*\"[^\"]*\"")
            .replace(out, "\"device_id\":\"[redacted]\"")
        // Clinical observation values / payload bodies
        out = Regex("(?i)\"(value|text_value|glucose|heart_rate|systolic|diastolic|spo2|oxygen|weight|steps)\"\\s*:\\s*[^,}\\]]+")
            .replace(out, "\"$1\":[redacted]")
        out = Regex("(?is)\\b(body|payload|response_body|request_body)\\s*[:=]\\s*.+")
            .replace(out, "$1=[redacted]")
        return out
    }
}
