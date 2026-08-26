package com.healthchecker.companion.ui

import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import android.net.http.SslError
import android.os.Bundle
import android.view.View
import android.webkit.CookieManager
import android.webkit.SslErrorHandler
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebStorage
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import com.healthchecker.companion.BuildConfig
import com.healthchecker.companion.R
import com.healthchecker.companion.consumer.ConsumerOriginLock
import com.healthchecker.companion.consumer.ConsumerOriginPolicy
import com.healthchecker.companion.consumer.ConsumerSafFileChooserPolicy
import com.healthchecker.companion.secure.SecurePrefs
import com.healthchecker.companion.util.SafeLog

/**
 * HC-319D supported consumer launcher.
 *
 * HC325-R6: ordinary production launch is fail-closed to the governed public
 * origin. Paired companion host, stale prefs, restored WebView history, and
 * debug localhost defaults must not become the consumer WebView URL.
 * HC325-R6B: SAF content:// documents selected via the file chooser are
 * readable for FormData upload; file:// access stays disabled.
 * Native Health Connect and WorkManager remain in [CompanionStatusActivity].
 * No JavaScript interface is installed and no clinical data is persisted natively.
 */
class ConsumerLauncherActivity : AppCompatActivity() {
    private lateinit var prefs: SecurePrefs
    private lateinit var webView: WebView
    private lateinit var connectionPanel: View
    private lateinit var connectionMessage: TextView
    private var originPolicy: ConsumerOriginPolicy? = null
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private var originRecoveryInFlight = false
    private val grantedSafUris = mutableListOf<Uri>()

    private val filePicker = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        val callback = fileCallback ?: return@registerForActivityResult
        fileCallback = null
        val uris = ConsumerSafFileChooserPolicy.urisFromActivityResult(result.resultCode, result.data)
        uris?.forEach { takeSafReadGrant(it) }
        callback.onReceiveValue(uris)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // HC322A: never set FLAG_SECURE. The consumer WebView is one Activity;
        // a login-time secure flag would persist onto Dashboard/Snapshot/etc.
        ScreenshotPolicy.applyConsumerScreenshotPolicy(window)
        setContentView(R.layout.activity_consumer_launcher)
        prefs = SecurePrefs(this)
        webView = findViewById(R.id.consumerWebView)
        connectionPanel = findViewById(R.id.consumerConnectionRequired)
        connectionMessage = findViewById(R.id.consumerConnectionMessage)

        findViewById<Button>(R.id.consumerSettings).setOnClickListener { openNativeSettings() }
        findViewById<Button>(R.id.consumerOpenSettings).setOnClickListener { openNativeSettings() }
        findViewById<Button>(R.id.consumerRetry).setOnClickListener { loadConsumer() }

        configureWebView()
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                // Same-document in-app stack only. Do not walk WebView history:
                // history can leave the approved /mobile origin/path.
                if (!::webView.isInitialized ||
                    connectionPanel.visibility == View.VISIBLE ||
                    webView.visibility != View.VISIBLE
                ) {
                    finish()
                    return
                }
                webView.evaluateJavascript(ConsumerInAppBackPolicy.HANDLE_SCRIPT) { raw ->
                    if (isDestroyed || isFinishing) return@evaluateJavascript
                    if (ConsumerOriginLock.mustRecover(webView.url, originPolicy?.origin)) {
                        recoverToGovernedOrigin()
                        return@evaluateJavascript
                    }
                    if (!ConsumerInAppBackPolicy.didHandleInApp(raw)) {
                        finish()
                    } else {
                        ScreenshotPolicy.applyConsumerScreenshotPolicy(window)
                    }
                }
            }
        })
        loadConsumer()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        loadConsumer()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        // HC325-R6: do not persist WebView history; restored history can be loopback.
    }

    override fun onResume() {
        super.onResume()
        ScreenshotPolicy.applyConsumerScreenshotPolicy(window)
        if (!::webView.isInitialized) return
        if (connectionPanel.visibility == View.VISIBLE) {
            loadConsumer()
            return
        }
        if (ConsumerOriginLock.mustRecover(webView.url, originPolicy?.origin)) {
            recoverToGovernedOrigin()
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun configureWebView() {
        WebView.setWebContentsDebuggingEnabled(false)
        CookieManager.getInstance().setAcceptCookie(false)
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, false)
        with(webView.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true // HC-318 sessionStorage only; no clinical stores.
            databaseEnabled = false
            allowFileAccess = ConsumerSafFileChooserPolicy.ALLOW_FILE_ACCESS
            allowContentAccess = ConsumerSafFileChooserPolicy.ALLOW_CONTENT_ACCESS
            allowFileAccessFromFileURLs = ConsumerSafFileChooserPolicy.ALLOW_FILE_ACCESS_FROM_FILE_URLS
            allowUniversalAccessFromFileURLs = ConsumerSafFileChooserPolicy.ALLOW_UNIVERSAL_ACCESS_FROM_FILE_URLS
            mixedContentMode = ConsumerSafFileChooserPolicy.MIXED_CONTENT_NEVER_ALLOW
            cacheMode = WebSettings.LOAD_NO_CACHE
            setGeolocationEnabled(false)
            mediaPlaybackRequiresUserGesture = true
            javaScriptCanOpenWindowsAutomatically = false
            setSupportMultipleWindows(false)
            userAgentString = "$userAgentString HealthCheckerAndroid/${BuildConfig.VERSION_NAME}"
        }
        webView.clearCache(true)
        webView.setDownloadListener { _, _, _, _, _ ->
            Toast.makeText(this, R.string.consumer_download_blocked, Toast.LENGTH_LONG).show()
            SafeLog.w("consumer_download_blocked")
        }
        webView.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                webView: WebView?,
                filePathCallback: ValueCallback<Array<Uri>>?,
                fileChooserParams: FileChooserParams?,
            ): Boolean {
                if (ConsumerSafFileChooserPolicy.shouldCancelPreviousCallback()) {
                    fileCallback?.onReceiveValue(null)
                }
                fileCallback = filePathCallback
                val intent = ConsumerSafFileChooserPolicy.createOpenDocumentIntent(
                    fileChooserParams?.acceptTypes
                )
                return try {
                    filePicker.launch(intent)
                    true
                } catch (t: Throwable) {
                    SafeLog.e("consumer_file_picker_failed", t)
                    fileCallback = null
                    filePathCallback?.onReceiveValue(null)
                    false
                }
            }
        }
        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean =
                handleNavigation(request?.url?.toString())

            @Deprecated("Deprecated in Android")
            override fun shouldOverrideUrlLoading(view: WebView?, url: String?): Boolean =
                handleNavigation(url)

            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                if (ConsumerOriginLock.mustRecover(url, originPolicy?.origin)) {
                    recoverToGovernedOrigin()
                    return
                }
                if (originPolicy?.isAllowed(url) == true) showWebView()
                ScreenshotPolicy.applyConsumerScreenshotPolicy(window)
            }

            override fun onReceivedSslError(view: WebView?, handler: SslErrorHandler?, error: SslError?) {
                handler?.cancel()
                showConnectionError(getString(R.string.consumer_tls_error))
                SafeLog.w("consumer_tls_rejected")
            }

            override fun onReceivedError(
                view: WebView?, request: WebResourceRequest?, error: WebResourceError?,
            ) {
                super.onReceivedError(view, request, error)
                if (request?.isForMainFrame != true) return
                val failed = request.url?.toString()
                if (ConsumerOriginLock.mustRecover(failed, originPolicy?.origin)) {
                    recoverToGovernedOrigin()
                    return
                }
                showConnectionError(getString(R.string.consumer_host_unreachable))
            }
        }
    }

    private fun handleNavigation(url: String?): Boolean {
        val policy = originPolicy ?: return true
        return when (ConsumerOriginLock.decideNavigation(url, policy)) {
            ConsumerOriginLock.NavigationDecision.LOGOUT -> {
                webView.stopLoading()
                WebStorage.getInstance().deleteAllData()
                webView.clearCache(true)
                prefs.clearUserScopedState()
                SafeLog.i("consumer_logout_local_state_cleared")
                loadConsumer()
                true
            }
            ConsumerOriginLock.NavigationDecision.ALLOW -> false
            ConsumerOriginLock.NavigationDecision.REJECT_AND_RECOVER -> {
                SafeLog.w("consumer_navigation_blocked")
                if (!ConsumerOriginLock.isForbiddenLoopbackUrl(url)) {
                    Toast.makeText(this, R.string.consumer_navigation_blocked, Toast.LENGTH_SHORT).show()
                }
                recoverToGovernedOrigin()
                true
            }
        }
    }

    private fun loadConsumer() {
        if (::webView.isInitialized) {
            webView.stopLoading()
        }
        val resolution = ConsumerOriginLock.resolve(
            ConsumerOriginLock.LaunchInputs(
                storedConsumerOrigin = prefs.getConsumerOrigin(),
                intentData = intent?.dataString,
                restoredWebViewUrl = if (::webView.isInitialized) webView.url else null,
                savedStateUrl = null,
                explicitLocalDevRequested = intent?.getBooleanExtra(
                    ConsumerOriginLock.EXTRA_EXPLICIT_LOCAL_DEV,
                    false,
                ) == true,
                isDebugBuild = BuildConfig.DEBUG,
                allowCleartextLocalDev = BuildConfig.ALLOW_CLEARTEXT_LOCAL_DEV,
            )
        )
        val policy = ConsumerOriginPolicy.create(
            resolution.origin,
            isDebugBuild = BuildConfig.DEBUG,
            allowCleartextLocalDev = BuildConfig.ALLOW_CLEARTEXT_LOCAL_DEV,
        )
        if (policy == null) {
            originPolicy = null
            showConnectionError(getString(R.string.consumer_connection_required))
            return
        }
        originPolicy = policy
        connectionPanel.visibility = View.GONE
        webView.visibility = View.VISIBLE
        webView.loadUrl(resolution.mobileUrl)
    }

    private fun recoverToGovernedOrigin() {
        if (originRecoveryInFlight || isDestroyed || isFinishing) return
        originRecoveryInFlight = true
        try {
            loadConsumer()
        } finally {
            originRecoveryInFlight = false
        }
    }

    private fun showWebView() {
        connectionPanel.visibility = View.GONE
        webView.visibility = View.VISIBLE
    }

    private fun showConnectionError(message: String) {
        connectionMessage.text = message
        webView.visibility = View.GONE
        connectionPanel.visibility = View.VISIBLE
        ScreenshotPolicy.applyConsumerScreenshotPolicy(window)
    }

    private fun takeSafReadGrant(uri: Uri) {
        try {
            contentResolver.takePersistableUriPermission(
                uri,
                ConsumerSafFileChooserPolicy.persistableReadPermissionFlags(),
            )
            grantedSafUris.add(uri)
        } catch (_: SecurityException) {
            SafeLog.i("saf_persistable_grant_unavailable")
        }
    }

    private fun releaseSafReadGrants() {
        val flags = ConsumerSafFileChooserPolicy.persistableReadPermissionFlags()
        grantedSafUris.forEach { uri ->
            try {
                contentResolver.releasePersistableUriPermission(uri, flags)
            } catch (_: SecurityException) {
                SafeLog.i("saf_persistable_release_unavailable")
            }
        }
        grantedSafUris.clear()
    }

    private fun openNativeSettings() {
        startActivity(Intent(this, CompanionStatusActivity::class.java))
    }

    override fun onDestroy() {
        fileCallback?.onReceiveValue(null)
        fileCallback = null
        releaseSafReadGrants()
        webView.stopLoading()
        webView.webChromeClient = null
        webView.webViewClient = WebViewClient()
        webView.destroy()
        super.onDestroy()
    }

}
