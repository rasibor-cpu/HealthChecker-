package com.healthchecker.companion.ui

import android.view.WindowManager
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * HC-322A: ordinary HealthChecker windows remain screenshot-capable.
 * FLAG_SECURE must not be applied globally or on consumer activities.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28])
class ScreenshotPolicyTest {

    @Test
    fun screenshotBlockingIsNotEnabledGlobally() {
        assertFalse(ScreenshotPolicy.isScreenshotBlockingEnabled())
        assertFalse(ScreenshotPolicy.HAS_PROTECTED_SCREENS)
    }

    @Test
    fun applyConsumerPolicyClearsFlagSecure() {
        val controller = Robolectric.buildActivity(ToolbarHarnessActivity::class.java).setup()
        val activity = controller.get()
        activity.window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        assertTrue(ScreenshotPolicy.isFlagSecureSet(activity.window))
        ScreenshotPolicy.applyConsumerScreenshotPolicy(activity.window)
        assertFalse(ScreenshotPolicy.isFlagSecureSet(activity.window))
        assertEquals(
            0,
            activity.window.attributes.flags and WindowManager.LayoutParams.FLAG_SECURE
        )
    }

    @Test
    fun harnessActivityDoesNotEnableFlagSecure() {
        val activity = Robolectric.buildActivity(ToolbarHarnessActivity::class.java).setup().get()
        assertFalse(ScreenshotPolicy.isFlagSecureSet(activity.window))
    }

    @Test
    fun permissionsRationaleActivityDoesNotEnableFlagSecure() {
        val activity = Robolectric.buildActivity(PermissionsRationaleActivity::class.java).setup().get()
        assertFalse(ScreenshotPolicy.isFlagSecureSet(activity.window))
        assertEquals(
            0,
            activity.window.attributes.flags and WindowManager.LayoutParams.FLAG_SECURE
        )
    }
}
