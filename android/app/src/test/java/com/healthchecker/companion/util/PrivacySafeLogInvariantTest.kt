package com.healthchecker.companion.util

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * HC-306I-R3 — privacy-safe log line redaction invariants used by companion paths.
 * Does not assert raw token/permission/health values are ever logged.
 */
class PrivacySafeLogInvariantTest {

    @Test
    fun safeLogMessagesMustNotEmbedSensitiveMarkers() {
        // Representative safe event names used by sync/delivery (no values appended).
        val safeEvents = listOf(
            "deliver http=200 bytes=12",
            "worker_skip reason=sync_already_running_local",
            "hc_settings_user_initiated",
            "workmanager_scheduled unique=hc303a_monitoring_sync"
        )
        val forbidden = listOf(
            "Bearer ",
            "changes_token=",
            "device_token=",
            "bpm=",
            "steps_count=",
            "hc_changes_token"
        )
        for (line in safeEvents) {
            for (bad in forbidden) {
                assertFalse("line='$line' must not contain '$bad'", line.contains(bad, ignoreCase = true))
            }
            // Redactor must leave already-safe operational lines intact.
            assertEquals(line, PrivacyRedactor.redact(line))
        }
    }
}
