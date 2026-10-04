package com.healthchecker.companion.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ConsumerSafeNavigationStateTest {
    @Test
    fun restoresOnlyAnAllowlistedRouteAndOpaqueRecordIdentifier() {
        assertEquals(
            ConsumerSafeNavigationState("records", "doc-123"),
            ConsumerSafeNavigationState.normalize(" Records ", "doc-123"),
        )
        assertEquals(
            ConsumerSafeNavigationState("dashboard"),
            ConsumerSafeNavigationState.normalize("dashboard", null),
        )
        assertNull(ConsumerSafeNavigationState.normalize("https://evil.example", null))
        assertNull(ConsumerSafeNavigationState.normalize("records", "../private"))
        assertNull(ConsumerSafeNavigationState.normalize("dashboard", "document-1"))
        assertNull(ConsumerSafeNavigationState.normalize("settings", "record-1"))
    }
}
