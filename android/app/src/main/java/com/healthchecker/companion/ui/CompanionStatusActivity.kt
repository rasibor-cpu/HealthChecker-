package com.healthchecker.companion.ui

import android.content.ActivityNotFoundException
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.WindowCompat
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.PermissionController
import androidx.lifecycle.lifecycleScope
import com.google.android.material.appbar.MaterialToolbar
import com.healthchecker.companion.R
import com.healthchecker.companion.healthconnect.HealthConnectAvailability
import com.healthchecker.companion.healthconnect.HealthConnectCapability
import com.healthchecker.companion.healthconnect.HealthConnectReader
import com.healthchecker.companion.healthconnect.PermissionLaunchMonitor
import com.healthchecker.companion.healthconnect.PermissionRequestPlanner
import com.healthchecker.companion.host.HostClient
import com.healthchecker.companion.secure.SecurePrefs
import com.healthchecker.companion.util.SafeLog
import com.healthchecker.companion.work.MonitoringSyncWorker
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

/**
 * Minimal accessible companion status screen.
 * Does not display diagnostic health values.
 *
 * HC-303C: request only missing permissions; detect silent/no-result launches;
 * user-initiated Manage Health Connect permissions (no automatic settings redirect);
 * toolbar/system Back finishes safely without sync/pairing changes.
 */
class CompanionStatusActivity : AppCompatActivity() {
    private lateinit var prefs: SecurePrefs
    private lateinit var statusBody: TextView
    private lateinit var navigator: StatusScreenNavigator
    private var permissionRequestInProgress: Boolean = false
    private var permissionActionMessage: String? = null
    private var permissionLaunchAttempt: PermissionLaunchMonitor.LaunchAttempt? = null

    private val permissionLauncher = registerForActivityResult(
        PermissionController.createRequestPermissionResultContract()
    ) { _ ->
        permissionLaunchAttempt = permissionLaunchAttempt?.let {
            PermissionLaunchMonitor.onResultDelivered(it)
        }
        permissionRequestInProgress = PermissionRequestPlanner.nextInProgress(
            PermissionRequestPlanner.LifecycleEvent.RESULT_DELIVERED
        )
        permissionActionMessage = "Permission request finished. Review granted vs missing below."
        refreshStatus()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        setContentView(R.layout.activity_companion_status)
        prefs = SecurePrefs(this)
        statusBody = findViewById(R.id.statusBody)
        navigator = StatusScreenNavigator(
            finishScreen = { finish() },
            refreshStatus = {
                permissionActionMessage =
                    "Returned from Health Connect settings. Pairing and sync state unchanged."
                refreshStatus()
            }
        )

        val root = findViewById<View>(R.id.statusRoot)
        val toolbar = findViewById<MaterialToolbar>(R.id.topAppBar)
        val scroll = findViewById<View>(R.id.statusScroll)
        WindowInsetApplier.install(
            root = root,
            toolbar = toolbar,
            scroll = scroll,
            basePaddingPx = resources.getDimensionPixelSize(R.dimen.status_scroll_base_padding),
            extraBottomPx = resources.getDimensionPixelSize(R.dimen.status_scroll_extra_bottom)
        )
        setSupportActionBar(toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        supportActionBar?.setDisplayShowHomeEnabled(true)
        toolbar.setNavigationOnClickListener {
            navigator.onToolbarBack()
        }
        onBackPressedDispatcher.addCallback(
            this,
            object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() {
                    navigator.onSystemBack()
                }
            }
        )

        if (savedInstanceState != null) {
            permissionRequestInProgress = PermissionRequestPlanner.nextInProgress(
                PermissionRequestPlanner.LifecycleEvent.ACTIVITY_RECREATED
            )
            permissionLaunchAttempt = null
        }

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
            requestHealthConnectPermissions()
        }

        // User-initiated only — never auto-redirect to Health Connect settings.
        findViewById<Button>(R.id.btnManagePermissions).setOnClickListener {
            openHealthConnectSettingsUserInitiated()
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

    override fun onResume() {
        super.onResume()
        val stillInProgress = permissionRequestInProgress
        val attempt = permissionLaunchAttempt
        if (stillInProgress) {
            permissionRequestInProgress = PermissionRequestPlanner.nextInProgress(
                PermissionRequestPlanner.LifecycleEvent.RESUME_AFTER_PAUSE
            )
        }
        lifecycleScope.launch {
            val report = withContext(Dispatchers.IO) {
                HealthConnectCapability(this@CompanionStatusActivity).report()
            }
            val assessment = PermissionLaunchMonitor.assessResume(
                attempt = attempt,
                missingAfter = report.permissionsMissing,
                stillMarkedInProgress = stillInProgress
            )
            if (assessment.silentOrNoResult || assessment.userMessage != null) {
                permissionActionMessage = assessment.userMessage
                if (assessment.silentOrNoResult) {
                    SafeLog.w("hc_permission_silent_or_no_result")
                    Toast.makeText(
                        this@CompanionStatusActivity,
                        assessment.userMessage,
                        Toast.LENGTH_LONG
                    ).show()
                }
            }
            permissionLaunchAttempt = null
            navigator.onHostResumed()
            // Always refresh granted/missing after resume (permissions or settings return).
            applyStatusReport(report)
        }
    }

    override fun onSupportNavigateUp(): Boolean {
        navigator.onToolbarBack()
        return true
    }

    private fun requestHealthConnectPermissions() {
        if (permissionRequestInProgress) {
            val msg =
                "Permission request already in progress. Wait for Health Connect to return, then try again."
            permissionActionMessage = msg
            Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
            refreshStatus()
            return
        }
        lifecycleScope.launch {
            val report = withContext(Dispatchers.IO) {
                HealthConnectCapability(this@CompanionStatusActivity).report()
            }
            val plan = PermissionRequestPlanner.plan(
                availability = report.availability,
                missingPermissions = report.permissionsMissing,
                requestInProgress = permissionRequestInProgress
            )
            permissionActionMessage = plan.userMessage
            when (plan.action) {
                PermissionRequestPlanner.Action.BLOCKED_IN_PROGRESS,
                PermissionRequestPlanner.Action.NOT_READY,
                PermissionRequestPlanner.Action.ALREADY_COMPLETE -> {
                    Toast.makeText(this@CompanionStatusActivity, plan.userMessage, Toast.LENGTH_LONG).show()
                    refreshStatus()
                }
                PermissionRequestPlanner.Action.LAUNCH_MISSING -> {
                    permissionLaunchAttempt = PermissionLaunchMonitor.LaunchAttempt(
                        missingBefore = plan.permissionsToRequest
                    )
                    permissionRequestInProgress = PermissionRequestPlanner.nextInProgress(
                        PermissionRequestPlanner.LifecycleEvent.LAUNCH_STARTED
                    )
                    try {
                        // Request ONLY missing permissions — do not re-ask already granted ones.
                        permissionLauncher.launch(plan.permissionsToRequest)
                        SafeLog.i("hc_permission_launch count=" + plan.permissionsToRequest.size)
                    } catch (t: Throwable) {
                        permissionRequestInProgress = PermissionRequestPlanner.nextInProgress(
                            PermissionRequestPlanner.LifecycleEvent.LAUNCH_FAILED
                        )
                        permissionLaunchAttempt = null
                        // No automatic settings redirect — surface visible action for the user.
                        val msg = "Health Connect permission screen refused to open. " +
                            "Tap MANAGE HEALTH CONNECT PERMISSIONS to adjust access."
                        permissionActionMessage = msg
                        SafeLog.e("hc_permission_launch_failed", t)
                        Toast.makeText(this@CompanionStatusActivity, msg, Toast.LENGTH_LONG).show()
                        refreshStatus()
                    }
                }
            }
        }
    }

    /** Official Health Connect settings — only from the visible Manage button (user-initiated). */
    private fun openHealthConnectSettingsUserInitiated() {
        try {
            startActivity(Intent(HealthConnectClient.ACTION_HEALTH_CONNECT_SETTINGS))
            navigator.markLeavingForExternalSettings()
            permissionActionMessage =
                "Opened Health Connect settings. " + getString(R.string.hc_settings_android16_note)
            SafeLog.i("hc_settings_user_initiated")
            Toast.makeText(this, R.string.hc_settings_android16_note, Toast.LENGTH_LONG).show()
        } catch (_: ActivityNotFoundException) {
            permissionActionMessage = "Health Connect settings screen unavailable on this device."
            Toast.makeText(this, permissionActionMessage, Toast.LENGTH_LONG).show()
            refreshStatus()
        } catch (t: Throwable) {
            SafeLog.e("hc_settings_open_failed", t)
            permissionActionMessage = "Could not open Health Connect settings. Retry later."
            Toast.makeText(this, permissionActionMessage, Toast.LENGTH_LONG).show()
            refreshStatus()
        }
    }

    private fun refreshStatus() {
        lifecycleScope.launch {
            val report = withContext(Dispatchers.IO) {
                HealthConnectCapability(this@CompanionStatusActivity).report()
            }
            applyStatusReport(report)
        }
    }

    private fun applyStatusReport(report: com.healthchecker.companion.healthconnect.CapabilityReport) {
        val text = buildString {
            appendLine("Health Connect: ${report.availability}")
            appendLine(report.message)
            appendLine("Permissions granted: ${report.permissionsGranted.size}")
            appendLine("Permissions missing: ${report.permissionsMissing.size}")
            appendLine("ECG supported in HC-303A: false")
            appendLine("Paired device: ${if (prefs.getDeviceId() != null) "yes" else "not paired"}")
            appendLine("Last attempt: ${prefs.getLastAttempt() ?: "never"}")
            appendLine("Last success: ${prefs.getLastSuccess() ?: "never"}")
            appendLine("Queued observations: ${prefs.getQueuedCount()}")
            val pendingState = when (prefs.loadPendingBatch()) {
                is SecurePrefs.PendingBatchLoad.Loaded -> "yes"
                is SecurePrefs.PendingBatchLoad.Corrupt -> "corrupt"
                else -> "no"
            }
            appendLine("Pending retry batch: $pendingState")
            appendLine("Delivery error/action: ${prefs.getLastError() ?: "none"}")
            appendLine("Permission action: ${permissionActionMessage ?: "none"}")
            appendLine("Permission request in progress: $permissionRequestInProgress")
            appendLine("WorkManager unique: ${MonitoringSyncWorker.UNIQUE_NAME}")
            appendLine("Sync overlap: ${if (prefs.syncMutex.isHeld()) "busy" else "idle"}")
            appendLine("Exact/uninterrupted background: NOT guaranteed")
            if (report.availability == HealthConnectAvailability.UPDATE_REQUIRED) {
                appendLine("Action required: update Health Connect provider.")
            }
            if (report.permissionsMissing.isNotEmpty()) {
                appendLine("Tip: REQUEST asks only for missing types. MANAGE opens Health Connect settings.")
                appendLine(getString(R.string.hc_settings_android16_note))
            }
        }
        statusBody.text = text
    }

    private suspend fun runSyncOnce() {
        withContext(Dispatchers.IO) {
            val prefs = SecurePrefs(this@CompanionStatusActivity)
            val lease = prefs.syncMutex.tryAcquire("manual")
            if (!lease.acquired) {
                prefs.setLastError(lease.reason)
                return@withContext
            }
            try {
                prefs.setLastAttempt(java.time.Instant.now().toString())
                val reader = HealthConnectReader(this@CompanionStatusActivity, prefs)
                val pendingLoad = prefs.loadPendingBatch()
                if (pendingLoad is SecurePrefs.PendingBatchLoad.Corrupt) {
                    prefs.setLastError("pending_batch_corrupt")
                    return@withContext
                }
                val pending = (pendingLoad as? SecurePrefs.PendingBatchLoad.Loaded)?.batch
                val fetch = if (pending != null) {
                    HealthConnectReader.FetchResult(
                        observations = pending.observations(),
                        nextChangesToken = pending.nextChangesToken,
                        deletedRecordIds = pending.deletedRecordIds()
                    )
                } else {
                    reader.fetchNew()
                }
                prefs.setQueuedCount(fetch.observations.size)
                val capability = HealthConnectCapability(this@CompanionStatusActivity).report()
                val ack = HostClient(prefs).deliver(
                    fetch.observations,
                    fetch.nextChangesToken,
                    JSONObject().put("availability", capability.availability.name),
                    JSONObject().put("missing_count", capability.permissionsMissing.size),
                    JSONObject().put("unique_name", MonitoringSyncWorker.UNIQUE_NAME),
                    fetch.observations.size,
                    fetch.deletedRecordIds
                )
                if (ack.ok && ack.cursorAdvanced) {
                    reader.acknowledgeCursor(ack.nextCursorToken ?: fetch.nextChangesToken)
                    prefs.setLastSuccess(java.time.Instant.now().toString())
                    prefs.setLastError(null)
                    prefs.setQueuedCount(0)
                } else {
                    prefs.setLastError(ack.error ?: ack.status)
                }
            } finally {
                prefs.syncMutex.release("manual")
            }
        }
        refreshStatus()
    }
}
