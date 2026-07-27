package com.healthchecker.companion.util

/** Pure redaction helpers — safe for JVM unit tests without Android runtime. */
object PrivacyRedactor {
    fun redact(message: String): String {
        var out = message
        listOf(
            Regex("(?i)(token|authorization|bearer|pair_code|password|secret)=\\S+"),
            Regex("(?i)\"(value|glucose|heart_rate|systolic|diastolic)\"\\s*:\\s*[^,}\\]]+")
        ).forEach { out = it.replace(out, "$1=[redacted]") }
        return out
    }
}
