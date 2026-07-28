package com.healthchecker.companion.ui

import com.google.android.material.appbar.MaterialToolbar
import com.healthchecker.companion.R
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowLooper

/**
 * UI navigation tests for toolbar Back, system Back, and settings-return refresh.
 * Uses debug-only [ToolbarHarnessActivity] (no SecurePrefs / Health Connect I/O).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28])
class StatusScreenNavigationUiTest {

    @Test
    fun toolbarBackFinishesWithoutSideEffects() {
        val controller = Robolectric.buildActivity(ToolbarHarnessActivity::class.java).setup()
        val activity = controller.get()
        assertFalse(activity.isFinishing)
        val toolbar = activity.findViewById<MaterialToolbar>(R.id.topAppBar)
        assertNotNull(toolbar.navigationIcon)
        assertEquals(
            activity.getString(R.string.nav_back),
            toolbar.navigationContentDescription
        )
        // Click the navigation (Back) affordance if present as a child view.
        var clickedNav = false
        for (i in 0 until toolbar.childCount) {
            val child = toolbar.getChildAt(i)
            if (child.contentDescription == activity.getString(R.string.nav_back)) {
                child.performClick()
                clickedNav = true
                break
            }
        }
        if (!clickedNav) {
            // Fallback: invoke the same path wired to the navigation listener.
            activity.navigator.onToolbarBack()
        }
        ShadowLooper.runUiThreadTasksIncludingDelayedTasks()
        assertTrue(activity.isFinishing)
        assertEquals(1, activity.navigator.finishCountForTest())
        assertEquals(0, activity.syncClicks)
        assertEquals(0, activity.permissionClicks)
    }

    @Test
    fun systemBackFinishesConsistentlyWithToolbar() {
        val controller = Robolectric.buildActivity(ToolbarHarnessActivity::class.java).setup()
        val activity = controller.get()
        activity.onBackPressedDispatcher.onBackPressed()
        ShadowLooper.runUiThreadTasksIncludingDelayedTasks()
        assertTrue(activity.isFinishing)
        assertEquals(1, activity.navigator.finishCountForTest())
        assertEquals(0, activity.syncClicks)
        assertEquals(0, activity.permissionClicks)
    }

    @Test
    fun returnFromSettingsRefreshesWithoutSyncOrPermissionMutation() {
        val controller = Robolectric.buildActivity(ToolbarHarnessActivity::class.java).setup()
        val activity = controller.get()
        val before = activity.refreshCalls
        activity.navigator.markLeavingForExternalSettings()
        assertTrue(activity.navigator.awaitingExternalSettingsReturn)
        controller.pause()
        controller.resume()
        ShadowLooper.runUiThreadTasksIncludingDelayedTasks()
        assertFalse(activity.navigator.awaitingExternalSettingsReturn)
        assertTrue(activity.refreshCalls > before)
        assertEquals(0, activity.syncClicks)
        assertEquals(0, activity.permissionClicks)
        assertFalse(activity.isFinishing)
    }
}

class StatusScreenNavigatorTest {
    @Test
    fun backPathsFinishOnly() {
        var finished = 0
        var refreshed = 0
        val nav = StatusScreenNavigator(
            finishScreen = { finished++ },
            refreshStatus = { refreshed++ }
        )
        nav.onToolbarBack()
        nav.onSystemBack()
        assertEquals(2, finished)
        assertEquals(0, refreshed)
    }

    @Test
    fun settingsReturnRefreshesOnce() {
        var refreshed = 0
        val nav = StatusScreenNavigator(
            finishScreen = {},
            refreshStatus = { refreshed++ }
        )
        nav.onHostResumed()
        assertEquals(0, refreshed)
        nav.markLeavingForExternalSettings()
        nav.onHostResumed()
        assertEquals(1, refreshed)
        nav.onHostResumed()
        assertEquals(1, refreshed)
    }
}
