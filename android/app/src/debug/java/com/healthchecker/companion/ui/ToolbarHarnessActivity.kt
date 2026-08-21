package com.healthchecker.companion.ui

import android.os.Bundle
import android.widget.Button
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.appbar.MaterialToolbar
import com.healthchecker.companion.R

/**
 * Debug-only harness for Robolectric toolbar / system Back UI tests.
 * Not a launcher; performs no pairing, sync, or Health Connect I/O.
 */
class ToolbarHarnessActivity : AppCompatActivity() {
    lateinit var navigator: StatusScreenNavigator
    var refreshCalls: Int = 0
    var syncClicks: Int = 0
    var permissionClicks: Int = 0

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ScreenshotPolicy.applyConsumerScreenshotPolicy(window)
        setContentView(R.layout.activity_companion_status)
        navigator = StatusScreenNavigator(
            finishScreen = { finish() },
            refreshStatus = { refreshCalls++ }
        )
        val toolbar = findViewById<MaterialToolbar>(R.id.topAppBar)
        setSupportActionBar(toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        toolbar.setNavigationOnClickListener { navigator.onToolbarBack() }
        onBackPressedDispatcher.addCallback(
            this,
            object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() {
                    navigator.onSystemBack()
                }
            }
        )
        findViewById<TextView>(R.id.statusBody).text = "harness"
        findViewById<Button>(R.id.btnSyncNow).setOnClickListener { syncClicks++ }
        findViewById<Button>(R.id.btnPermissions).setOnClickListener { permissionClicks++ }
    }

    override fun onResume() {
        super.onResume()
        navigator.onHostResumed()
    }
}
