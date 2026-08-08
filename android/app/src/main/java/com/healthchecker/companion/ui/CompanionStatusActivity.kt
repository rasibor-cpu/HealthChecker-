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
import com.healthchecker.companion.BuildConfig
import com.healthchecker.companion.R
import com.healthchecker.companion.healthconnect.BackgroundReadPolicy
import com.healthchecker.companion.healthconnect.CapabilityReport
import com.healthchecker.companion.healthconnect.HealthConnectAvailability
import com.healthchecker.companion.healthconnect.HealthConnectCapability
import com.healthchecker.companion.host.HostClient
import com.healthchecker.companion.healthconnect.PermissionLaunchMonitor
import com.healthchecker.companion.healthconnect.PermissionRequestPlanner
import com.healthchecker.companion.host.PairingInputs
import com.healthchecker.companion.secure.SecurePrefs
import com.healthchecker.companion.sync.CompanionSyncRunner
import com.healthchecker.companion.util.SafeLog
import com.healthchecker.companion.work.MonitoringSyncWorker
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

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

        findViewById<EditText>(R.id.hostUrl).setText(prefs.displayHostForEditing())
        applyDebugPairingExtras(intent)

        findViewById<Button>(R.id.btnPair).setOnClickListener {
            val hostRaw = findViewById<EditText>(R.id.hostUrl).text.toString()
            val codeRaw = findViewById<EditText>(R.id.pairCode).text.toString()
            when (val normalized = PairingInputs.normalize(hostRaw, codeRaw)) {
                is PairingInputs.Result.Invalid -> {
                    SafeLog.w("pair_confirm_rejected reason=" + normalized.reason)
                    Toast.makeText(
                        this,
                        getString(R.string.pair_failed_reason, normalized.reason),
                        Toast.LENGTH_LONG
                    ).show()
                }
                is PairingInputs.Result.Normalized -> {
                    // Draft only — never touch active delivery host / token before success.
                    val host = normalized.hostUrl
                    val code = normalized.pairCode
                    prefs.setDraftHostUrl(host)
                    findViewById<EditText>(R.id.hostUrl).setText(host)
                    lifecycleScope.launch {
                        val err = withContext(Dispatchers.IO) {
                            runCatching {
                                HostClient(prefs).confirmPairing(
                                    host,
                                    code,
                                    "Android Companion"
                                )
                            }.getOrElse { t ->
                                SafeLog.e("pair_confirm_transport_failed", t)
                                t.javaClass.simpleName
                            }
                        }
                        // Failed/cancelled pairing: active host + token unchanged; keep draft for retry.
                        if (err == null) {
                            findViewById<EditText>(R.id.pairCode).text?.clear()
                            findViewById<EditText>(R.id.hostUrl).setText(prefs.displayHostForEditing())
                        }
                        Toast.makeText(
                            this@CompanionStatusActivity,
                            if (err == null) {
                                getString(R.string.pair_success)
                            } else {
                                getString(R.string.pair_failed_reason, err)
                            },
                            Toast.LENGTH_LONG
                        ).show()
                        refreshStatus()
                    }
                }
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
            lifecycleScope.launch {
                val report = try {
                    withContext(Dispatchers.IO) {
                        HealthConnectCapability(
                            this@CompanionStatusActivity
                        ).report()
                    }
                } catch (t: Throwable) {
                    val message =
                        "Unable to verify background Health Connect access."
                    permissionActionMessage = message
                    SafeLog.e(
                        "background_capability_check_failed",
                        t
                    )
                    Toast.makeText(
                        this@CompanionStatusActivity,
                        message,
                        Toast.LENGTH_LONG
                    ).show()
                    return@launch
                }

                permissionActionMessage = when (
                    BackgroundReadPolicy.scheduleDecision(
                        featureAvailable =
                            report.backgroundReadFeatureAvailable,
                        backgroundPermissionGranted =
                            report.backgroundReadPermissionGranted
                    )
                ) {
                    BackgroundReadPolicy.ScheduleDecision.READY -> {
                        MonitoringSyncWorker.schedule(
                            this@CompanionStatusActivity
                        )
                        "Background monitoring schedule enabled."
                    }
                    BackgroundReadPolicy.ScheduleDecision.FEATURE_UNAVAILABLE ->
                        "Background Health Connect reads are unavailable on this device."
                    BackgroundReadPolicy.ScheduleDecision.PERMISSION_REQUIRED ->
                        "Background permission is required. Tap REQUEST HEALTH CONNECT PERMISSIONS."
                }

                applyStatusReport(report)
            }
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
                missingAfter =
                    requestableMissingPermissions(report),
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
                missingPermissions =
                    requestableMissingPermissions(report),
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

    private fun requestableMissingPermissions(
        report: CapabilityReport
    ): Set<String> = BackgroundReadPolicy.permissionsToRequest(
        missingRecordPermissions = report.permissionsMissing,
        featureAvailable = report.backgroundReadFeatureAvailable,
        backgroundPermissionGranted =
            report.backgroundReadPermissionGranted
    )

    private fun applyStatusReport(report: CapabilityReport) {
        val text = buildString {
            appendLine("Health Connect: ${report.availability}")
            appendLine(report.message)
            appendLine("Sync mode: granted types only")
            appendLine("Permissions granted: ${report.permissionsGranted.size}")
            appendLine("Permissions missing: ${report.permissionsMissing.size}")
            if (report.permissionsMissing.isNotEmpty() && report.permissionsGranted.isNotEmpty()) {
                appendLine("Partial permissions warning: yes")
            } else if (prefs.getPartialPermissionWarning()) {
                appendLine("Partial permissions warning: yes")
            } else {
                appendLine("Partial permissions warning: no")
            }
            appendLine(
                "Background read feature: " +
                    if (report.backgroundReadFeatureAvailable) {
                        "available"
                    } else {
                        "unavailable"
                    }
            )
            appendLine(
                "Background read permission: " +
                    when {
                        !report.backgroundReadFeatureAvailable ->
                            "not available"
                        report.backgroundReadPermissionGranted ->
                            "granted"
                        else -> "missing"
                    }
            )
            appendLine(
                "Background schedule readiness: " +
                    BackgroundReadPolicy.scheduleDecision(
                        report.backgroundReadFeatureAvailable,
                        report.backgroundReadPermissionGranted
                    ).name.lowercase()
            )
            when {
                prefs.getLastError()?.let {
                    it == "no_granted_permissions" ||
                        it == "query_not_performed" ||
                        it == "permission_required" ||
                        it == "background_permission_required"
                } == true ->
                    appendLine("Last fetch: query not performed (permission action required)")
                prefs.getLastQueryPerformed() ->
                    appendLine("Last fetch: query performed")
                else ->
                    appendLine("Last fetch: none")
            }
            appendLine("ECG supported in HC-303A: false")
            appendLine("Paired device: ${if (prefs.getDeviceToken() != null && prefs.getHostUrl() != null) "yes" else "not paired"}")
            when (val integrity = prefs.assessPairingIntegrity()) {
                is com.healthchecker.companion.secure.CompanionHostStore.PairingIntegrity.Inconsistent -> {
                    appendLine("Pairing repair required: ${integrity.reason}")
                    appendLine(getString(R.string.pairing_repair_required))
                }
                else -> Unit
            }
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
            if (requestableMissingPermissions(report).isNotEmpty()) {
                appendLine("Tip: REQUEST asks only for missing access. MANAGE opens Health Connect settings.")
                appendLine(getString(R.string.hc_settings_android16_note))
            }
            if (
                report.backgroundReadFeatureAvailable &&
                !report.backgroundReadPermissionGranted
            ) {
                appendLine(
                    "Action required: grant background Health Connect access before enabling the schedule."
                )
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
                prefs.setLastQueryPerformed(false)
                return@withContext
            }
            try {
                CompanionSyncRunner(this@CompanionStatusActivity, prefs).runOnce("manual")
            } finally {
                prefs.syncMutex.release("manual")
            }
        }
        refreshStatus()
    }

    /**
     * Debug-only one-tap pairing prep. Ignored in release builds.
     * Populates EditText + draft key only — never active host, token, pair, sync, or WorkManager.
     */
    private fun applyDebugPairingExtras(intent: Intent?) {
        if (!BuildConfig.DEBUG || intent == null) return
        val host = intent.getStringExtra(EXTRA_DEBUG_HOST_URL)
        val code = intent.getStringExtra(EXTRA_DEBUG_PAIR_CODE)
        if (!host.isNullOrBlank()) {
            val origin = PairingInputs.normalizeOrigin(host)
            val draft = when (origin) {
                is PairingInputs.OriginResult.Ok -> origin.origin
                is PairingInputs.OriginResult.Invalid -> PairingInputs.sanitizeHost(host)
            }
            if (draft.isNotEmpty()) {
                prefs.setDraftHostUrl(draft)
                findViewById<EditText>(R.id.hostUrl).setText(draft)
                SafeLog.i("debug_pair_draft_populated")
            }
        }
        if (!code.isNullOrBlank()) {
            // Ephemeral UI only — pairing codes are never written to SecurePrefs.
            findViewById<EditText>(R.id.pairCode).setText(code.trim())
            SafeLog.i("debug_pair_code_field_populated")
        }
    }

    companion object {
        const val EXTRA_DEBUG_HOST_URL = "hc_debug_host_url"
        const val EXTRA_DEBUG_PAIR_CODE = "hc_debug_pair_code"
    }
}
