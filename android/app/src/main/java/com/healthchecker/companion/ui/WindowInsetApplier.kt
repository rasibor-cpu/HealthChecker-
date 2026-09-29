package com.healthchecker.companion.ui

import android.view.View
import androidx.core.graphics.Insets
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat

/**
 * Applies system-bar / display-cutout insets so status content and the final
 * WorkManager control remain scrollable above the navigation bar (Samsung gesture/3-button).
 */
object WindowInsetApplier {
    data class PaddingResult(
        val top: Int,
        val left: Int,
        val right: Int,
        val bottom: Int
    )

    fun computePadding(
        systemBars: Insets,
        cutout: Insets,
        basePaddingPx: Int,
        extraBottomPx: Int
    ): PaddingResult {
        val top = maxOf(systemBars.top, cutout.top)
        val left = maxOf(systemBars.left, cutout.left) + basePaddingPx
        val right = maxOf(systemBars.right, cutout.right) + basePaddingPx
        val bottom = maxOf(systemBars.bottom, cutout.bottom) + basePaddingPx + extraBottomPx
        return PaddingResult(top = top, left = left, right = right, bottom = bottom)
    }

    fun install(
        root: View,
        toolbar: View,
        scroll: View,
        basePaddingPx: Int,
        extraBottomPx: Int
    ) {
        ViewCompat.setOnApplyWindowInsetsListener(root) { _, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            val cutout = insets.getInsets(WindowInsetsCompat.Type.displayCutout())
            val pad = computePadding(bars, cutout, basePaddingPx, extraBottomPx)
            toolbar.setPadding(pad.left, pad.top, pad.right, toolbar.paddingBottom)
            // Top content padding stays at base; bottom clears nav bar + extra scroll room.
            scroll.setPadding(pad.left, basePaddingPx, pad.right, pad.bottom)
            insets
        }
        ViewCompat.requestApplyInsets(root)
    }

    /**
     * HC-352: header-only variant for edge-to-edge screens (targetSdk 35) whose content is a
     * full-bleed view (e.g. WebView) rather than a scrollable list. Pads just the toolbar so it
     * clears the status bar/cutout with a small amount of intentional breathing room, without
     * reserving excess vertical space.
     */
    fun installHeader(
        root: View,
        toolbar: View,
        extraTopPx: Int
    ) {
        ViewCompat.setOnApplyWindowInsetsListener(root) { _, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            val cutout = insets.getInsets(WindowInsetsCompat.Type.displayCutout())
            val top = maxOf(bars.top, cutout.top) + extraTopPx
            val left = maxOf(bars.left, cutout.left)
            val right = maxOf(bars.right, cutout.right)
            toolbar.setPadding(left, top, right, toolbar.paddingBottom)
            insets
        }
        ViewCompat.requestApplyInsets(root)
    }
}
