package com.healthchecker.companion.ui

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.net.http.SslError
import android.os.Bundle
import android.provider.Settings
import android.view.View
import android.view.WindowManager
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
import com.healthchecker.companion.consumer.ConsumerOriginPolicy
import com.healthchecker.companion.secure.SecurePrefs
import com.healthchecker.companion.util.SafeLog

/**
 * HC-319D supported consumer launcher.
 *
 * Loads only the HC-319C API-only document from the exact paired origin. Native
 * Health Connect and WorkManager remain in [CompanionStatusActivity]. No
 * JavaScript interface is installed and no clinical data is persisted natively.
 */
class ConsumerLauncherActivity : AppCompatActivity() {
    private lateinit var prefs: SecurePrefs
    private lateinit var webView: WebView
    private lateinit var connectionPanel: View
    private lateinit var connectionMessage: TextView
    private var originPolicy: ConsumerOriginPolicy? = null
    private var fileCallback: ValueCallback<Array<Uri>>? = null

    private val filePicker = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        val callback = fileCallback ?: return@registerForActivityResult
        fileCallback = null
        val uri = if (result.resultCode == Activity.RESULT_OK) result.data?.data else null
        callback.onReceiveValue(uri?.let { arrayOf(it) })
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // HC321-UAT1: do not blanket FLAG_SECURE on ordinary consumer screens.
        // Secure flag is applied only while login/password-change surfaces are visible.
        applySecureWindow(false)
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
                // The consumer is a single-document application. Back exits rather
                // than permitting history to cross the approved route boundary.
                finish()
            }
        })
        loadConsumer()
    }

    override fun onResume() {
        super.onResume()
        if (::webView.isInitialized && connectionPanel.visibility == View.VISIBLE) {
            loadConsumer()
        } else if (::webView.isInitialized) {
            refreshSecureWindowFromDom()
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
            allowFileAccess = false
            allowContentAccess = false
            allowFileAccessFromFileURLs = false
            allowUniversalAccessFromFileURLs = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
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
                fileCallback?.onReceiveValue(null)
                fileCallback = filePathCallback
                val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                    addCategory(Intent.CATEGORY_OPENABLE)
                    type = "*/*"
                    putExtra(Intent.EXTRA_MIME_TYPES, arrayOf(
                        "application/pdf", "application/json", "image/png", "image/jpeg"
                    ))
                    putExtra(Intent.EXTRA_ALLOW_MULTIPLE, false)
                }
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
                if (originPolicy?.isAllowed(url) == true) showWebView()
                refreshSecureWindowFromDom()
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
                if (request?.isForMainFrame == true) {
                    showConnectionError(getString(R.string.consumer_host_unreachable))
                }
            }
        }
    }

    private fun handleNavigation(url: String?): Boolean {
        val policy = originPolicy ?: return true
        if (policy.isLogoutCompletion(url)) {
            webView.stopLoading()
            WebStorage.getInstance().deleteAllData()
            webView.clearCache(true)
            prefs.clearUserScopedState()
            SafeLog.i("consumer_logout_local_state_cleared")
            loadConsumer()
            return true
        }
        val allowed = policy.isAllowed(url)
        if (!allowed) {
            SafeLog.w("consumer_navigation_blocked")
            Toast.makeText(this, R.string.consumer_navigation_blocked, Toast.LENGTH_SHORT).show()
        }
        return !allowed
    }

    private fun loadConsumer() {
        val policy = ConsumerOriginPolicy.create(
            prefs.getConsumerOrigin() ?: debugConsumerOrigin(),
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
        webView.loadUrl(policy.mobileUrl())
    }

    private fun debugConsumerOrigin(): String? =
        if (BuildConfig.DEBUG && BuildConfig.ALLOW_CLEARTEXT_LOCAL_DEV) DEBUG_CONSUMER_ORIGIN else null

    private fun showWebView() {
        connectionPanel.visibility = View.GONE
        webView.visibility = View.VISIBLE
    }

    private fun showConnectionError(message: String) {
        connectionMessage.text = message
        webView.visibility = View.GONE
        connectionPanel.visibility = View.VISIBLE
        applySecureWindow(false)
    }

    private fun refreshSecureWindowFromDom() {
        if (!::webView.isInitialized) return
        webView.evaluateJavascript(SecureWindowPolicy.SENSITIVE_SURFACE_JS) { raw ->
            val secure = raw == "true"
            runOnUiThread { applySecureWindow(secure) }
        }
    }

    private fun applySecureWindow(secure: Boolean) {
        if (secure) {
            window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        } else {
            window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
        }
    }

    private fun openNativeSettings() {
        startActivity(Intent(this, CompanionStatusActivity::class.java))
    }

    override fun onDestroy() {
        fileCallback?.onReceiveValue(null)
        fileCallback = null
        webView.stopLoading()
        webView.webChromeClient = null
        webView.webViewClient = WebViewClient()
        webView.destroy()
        super.onDestroy()
    }

    companion object {
        private const val DEBUG_CONSUMER_ORIGIN = "http://localhost:8766"
    }
}
