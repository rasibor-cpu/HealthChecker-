package com.healthchecker.companion.ui

import androidx.core.graphics.Insets
import com.healthchecker.companion.R
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

class WindowInsetApplierTest {
    @Test
    fun bottomPaddingClearsNavBarAndKeepsBaseExtra() {
        val bars = Insets.of(0, 48, 0, 84)
        val cutout = Insets.of(0, 0, 0, 0)
        val pad = WindowInsetApplier.computePadding(
            systemBars = bars,
            cutout = cutout,
            basePaddingPx = 16,
            extraBottomPx = 24
        )
        assertEquals(48, pad.top)
        assertEquals(16, pad.left)
        assertEquals(16, pad.right)
        assertEquals(84 + 16 + 24, pad.bottom)
        assertTrue(pad.bottom > bars.bottom)
    }

    @Test
    fun cutoutInsetsAreHonoredWhenLargerThanSystemBars() {
        val bars = Insets.of(0, 24, 0, 40)
        val cutout = Insets.of(10, 60, 10, 0)
        val pad = WindowInsetApplier.computePadding(bars, cutout, 8, 8)
        assertEquals(60, pad.top)
        assertEquals(18, pad.left)
        assertEquals(18, pad.right)
        assertEquals(40 + 8 + 8, pad.bottom)
    }
}

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28])
class StatusLayoutInsetUiTest {
    @Test
    fun scrollContentAndFinalButtonRemainReachableWithInsets() {
        val controller = Robolectric.buildActivity(ToolbarHarnessActivity::class.java).setup()
        val activity = controller.get()
        val scroll = activity.findViewById<android.widget.ScrollView>(R.id.statusScroll)
        val schedule = activity.findViewById<android.view.View>(R.id.btnSchedule)
        val spacer = activity.findViewById<android.view.View>(R.id.bottomScrollSpacer)
        assertNotNull(scroll)
        assertNotNull(schedule)
        assertNotNull(spacer)
        assertTrue(scroll.isFillViewport)
        WindowInsetApplier.install(
            root = activity.findViewById(R.id.statusRoot),
            toolbar = activity.findViewById(R.id.topAppBar),
            scroll = scroll,
            basePaddingPx = 16,
            extraBottomPx = 24
        )
        val clearedBottom = 16 + 24 + 84
        scroll.setPadding(16, 16, 16, clearedBottom)
        assertTrue(scroll.paddingBottom >= 16 + 24)
        assertEquals(clearedBottom, scroll.paddingBottom)
        // Presence in hierarchy is enough for Robolectric (visibility can be unset pre-layout).
        assertTrue(schedule!!.id == R.id.btnSchedule)
        assertTrue(spacer!!.id == R.id.bottomScrollSpacer)
    }
}
