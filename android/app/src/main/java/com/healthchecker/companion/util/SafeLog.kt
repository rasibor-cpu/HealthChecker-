package com.healthchecker.companion.util

import android.util.Log

/**
 * Privacy-safe logging — never emit tokens, health values, payload bodies,
 * raw exception messages, causes, or stack traces.
 */
object SafeLog {
    private const val TAG = "HCCompanion"

    fun i(message: String) = Log.i(TAG, PrivacyRedactor.redact(message))
    fun w(message: String) = Log.w(TAG, PrivacyRedactor.redact(message))

    fun e(message: String, t: Throwable? = null) =
        Log.e(TAG, boundedErrorMessage(message, t))

    internal fun boundedErrorMessage(
        message: String,
        t: Throwable? = null
    ): String {
        val safeMessage = PrivacyRedactor.redact(message)
        if (t == null) {
            return safeMessage
        }
        val exceptionClass =
            t.javaClass.simpleName.ifBlank { "Throwable" }
        return "$safeMessage class=$exceptionClass"
    }

    fun redact(message: String): String = PrivacyRedactor.redact(message)
}