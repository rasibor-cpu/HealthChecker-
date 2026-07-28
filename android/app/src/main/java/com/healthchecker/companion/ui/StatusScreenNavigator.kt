package com.healthchecker.companion.ui

/**
 * Safe navigation policy for the Companion status screen.
 * Back closes the screen only — never triggers sync, pairing, or permission changes.
 * Returning from Health Connect settings only refreshes displayed capability state.
 */
class StatusScreenNavigator(
    private val finishScreen: () -> Unit,
    private val refreshStatus: () -> Unit
) {
    /** True while Health Connect settings (or similar external UI) is in the foreground. */
    var awaitingExternalSettingsReturn: Boolean = false
        private set

    private var refreshCount: Int = 0
    private var finishCount: Int = 0

    fun refreshCountForTest(): Int = refreshCount
    fun finishCountForTest(): Int = finishCount

    /** Toolbar Up / Back — finishes root status screen without mutating credentials or sync. */
    fun onToolbarBack() {
        finishSafely()
    }

    /** System Back — same safe finish path as the toolbar arrow. */
    fun onSystemBack() {
        finishSafely()
    }

    /** Call immediately before starting Health Connect settings. */
    fun markLeavingForExternalSettings() {
        awaitingExternalSettingsReturn = true
    }

    /**
     * Call from Activity.onResume. Refreshes status after settings return.
     * Does not grant/revoke permissions or start sync.
     */
    fun onHostResumed() {
        if (awaitingExternalSettingsReturn) {
            awaitingExternalSettingsReturn = false
            refreshCount++
            refreshStatus()
        }
    }

    private fun finishSafely() {
        awaitingExternalSettingsReturn = false
        finishCount++
        finishScreen()
    }
}
