package com.healthchecker.companion.ui

import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.health.connect.client.PermissionController
import androidx.lifecycle.lifecycleScope
import com.healthchecker.companion.R
import com.healthchecker.companion.healthconnect.HealthConnectAvailability
import com.healthchecker.companion.healthconnect.HealthConnectCapability
import com.healthchecker.companion.healthconnect.HealthConnectReader
import com.healthchecker.companion.host.HostClient
import com.healthchecker.companion.secure.SecurePrefs
import com.healthchecker.companion.work.MonitoringSyncWorker
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

/**
 * Minimal accessible companion status screen.
 * Does not display diagnostic health values.
 */
class CompanionStatusActivity : AppCompatActivity() {
    private lateinit var prefs: SecurePrefs
    private lateinit var statusBody: TextView

    private val permissionLauncher = registerForActivityResult(
        PermissionController.createRequestPermissionResultContract()
    ) {
        refreshStatus()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_companion_status)
        prefs = SecurePrefs(this)
        statusBody = findViewById(R.id.statusBody)

        findViewById<EditText>(R.id.hostUrl).setText(prefs.getHostUrl().orEmpty())

        findViewById<Button>(R.id.btnPair).setOnClickListener {
            val host = findViewById<EditText>(R.id.hostUrl).text.toString().trim()
            val code = findViewById<EditText>(R.id.pairCode).text.toString().trim()
            lifecycleScope.launch {
                val err = withContext(Dispatchers.IO) {
                    runCatching {
                        HostClient(prefs).confirmPairing(host, code, "Android Companion")
                    }.getOrElse { it.message }
                }
                Toast.makeText(
                    this@CompanionStatusActivity,
                    if (err == null) "Paired" else "Pair failed",
                    Toast.LENGTH_SHORT
                ).show()
                refreshStatus()
            }
        }

        findViewById<Button>(R.id.btnPermissions).setOnClickListener {
            val required = HealthConnectCapability(this).requiredPermissions()
            permissionLauncher.launch(required)
        }

        findViewById<Button>(R.id.btnSchedule).setOnClickListener {
            MonitoringSyncWorker.schedule(this)
            refreshStatus()
        }

        findViewById<Button>(R.id.btnSyncNow).setOnClickListener {
            lifecycleScope.launch { runSyncOnce() }
        }

        refreshStatus()
    }

    private fun refreshStatus() {
        lifecycleScope.launch {
            val report = withContext(Dispatchers.IO) {
                HealthConnectCapability(this@CompanionStatusActivity).report()
            }
            val text = buildString {
                appendLine("Health Connect: ${report.availability}")
                appendLine(report.message)
                appendLine("Permissions granted: ${report.permissionsGranted.size}")
                appendLine("Permissions missing: ${report.permissionsMissing.size}")
                appendLine("ECG supported in HC-303A: false")
                appendLine("Paired device: ${prefs.getDeviceId() ?: "not paired"}")
                appendLine("Last attempt: ${prefs.getLastAttempt() ?: "never"}")
                appendLine("Last success: ${prefs.getLastSuccess() ?: "never"}")
                appendLine("Queued observations: ${prefs.getQueuedCount()}")
                appendLine("Delivery error/action: ${prefs.getLastError() ?: "none"}")
                appendLine("WorkManager unique: ${MonitoringSyncWorker.UNIQUE_NAME}")
                appendLine("Exact/uninterrupted background: NOT guaranteed")
                if (report.availability == HealthConnectAvailability.UPDATE_REQUIRED) {
                    appendLine("Action required: update Health Connect provider.")
                }
            }
            statusBody.text = text
        }
    }

    private suspend fun runSyncOnce() {
        withContext(Dispatchers.IO) {
            val prefs = SecurePrefs(this@CompanionStatusActivity)
            val reader = HealthConnectReader(this@CompanionStatusActivity, prefs)
            val fetch = reader.fetchNew()
            prefs.setQueuedCount(fetch.observations.size)
            prefs.setLastAttempt(java.time.Instant.now().toString())
            val capability = HealthConnectCapability(this@CompanionStatusActivity).report()
            val ack = HostClient(prefs).deliver(
                fetch.observations,
                fetch.nextChangesToken,
                JSONObject().put("availability", capability.availability.name),
                JSONObject().put("missing_count", capability.permissionsMissing.size),
                JSONObject().put("unique_name", MonitoringSyncWorker.UNIQUE_NAME),
                fetch.observations.size
            )
            if (ack.ok && ack.cursorAdvanced) {
                reader.acknowledgeCursor(ack.nextCursorToken ?: fetch.nextChangesToken)
                prefs.setLastSuccess(java.time.Instant.now().toString())
                prefs.setLastError(null)
                prefs.setQueuedCount(0)
            } else {
                prefs.setLastError(ack.error ?: ack.status)
            }
        }
        refreshStatus()
    }
}
