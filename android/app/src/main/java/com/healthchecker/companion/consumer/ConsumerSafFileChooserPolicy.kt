package com.healthchecker.companion.consumer

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.webkit.WebSettings

/**
 * HC325-R6B — narrowly scoped Storage Access Framework import for the
 * consumer WebView. Chromium FormData upload needs to read the selected
 * content:// blob. file:// and arbitrary filesystem access stay forbidden.
 */
object ConsumerSafFileChooserPolicy {
    const val ALLOW_CONTENT_ACCESS = true
    const val ALLOW_FILE_ACCESS = false
    const val ALLOW_FILE_ACCESS_FROM_FILE_URLS = false
    const val ALLOW_UNIVERSAL_ACCESS_FROM_FILE_URLS = false
    const val MIXED_CONTENT_NEVER_ALLOW = WebSettings.MIXED_CONTENT_NEVER_ALLOW
    const val ALLOW_MULTIPLE = false

    val DEFAULT_ACCEPT_TYPES = arrayOf(
        "application/pdf",
        "application/json",
        "image/png",
        "image/jpeg",
    )

    const val READ_URI_GRANT_FLAGS =
        Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION

    fun mimeTypesForChooser(acceptTypes: Array<String>?): Array<String> {
        val cleaned = acceptTypes
            ?.map { it.trim() }
            ?.filter { it.isNotEmpty() && it != "*/*" }
            .orEmpty()
        return if (cleaned.isNotEmpty()) cleaned.toTypedArray() else DEFAULT_ACCEPT_TYPES
    }

    fun createOpenDocumentIntent(acceptTypes: Array<String>?): Intent {
        val mimeTypes = mimeTypesForChooser(acceptTypes)
        return Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = if (mimeTypes.size == 1) mimeTypes[0] else "*/*"
            putExtra(Intent.EXTRA_MIME_TYPES, mimeTypes)
            putExtra(Intent.EXTRA_ALLOW_MULTIPLE, ALLOW_MULTIPLE)
            addFlags(READ_URI_GRANT_FLAGS)
        }
    }

    /**
     * Only a single SAF content:// URI is returned. file:// and other schemes
     * are dropped so JavaScript never receives arbitrary filesystem paths.
     */
    fun urisFromActivityResult(resultCode: Int, data: Intent?): Array<Uri>? {
        if (resultCode != Activity.RESULT_OK || data == null) return null
        val uri = data.data ?: data.clipData?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.uri
        if (uri == null || !isSafContentUri(uri)) return null
        return arrayOf(uri)
    }

    fun isSafContentUri(uri: Uri?): Boolean {
        if (uri == null) return false
        return uri.scheme.equals("content", ignoreCase = true) && !uri.authority.isNullOrBlank()
    }

    fun persistableReadPermissionFlags(): Int = Intent.FLAG_GRANT_READ_URI_PERMISSION

    fun shouldCancelPreviousCallback(): Boolean = true
}
