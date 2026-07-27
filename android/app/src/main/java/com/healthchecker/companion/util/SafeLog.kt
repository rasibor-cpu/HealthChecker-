package com.healthchecker.companion.util

import android.util.Log

/**
 * Privacy-safe logging — never emit tokens, health values, or payload bodies.
 */
object SafeLog {
    private const val TAG = "HCCompanion"

    fun i(message: String) = Log.i(TAG, PrivacyRedactor.redact(message))
    fun w(message: String) = Log.w(TAG, PrivacyRedactor.redact(message))
    fun e(message: String, t: Throwable? = null) {
        val msg = PrivacyRedactor.redact(message)
        if (t == null) Log.e(TAG, msg) else Log.e(TAG, msg, t)
    }

    fun redact(message: String): String = PrivacyRedactor.redact(message)
}
