package com.healthchecker.companion.consumer

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.webkit.WebSettings
import java.io.InputStream

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

    /**
     * HC329 — bound on native-side reads of a SAF-selected document. Chromium's
     * WebView can fail (or hang, then reject fetch() with a generic "Failed to
     * fetch") when asked to stream a content:// blob directly into a multipart
     * upload body, so the native layer reads the bytes itself and hands them to
     * JS through [ConsumerRecordImportBridge] instead. Bound the read so a huge
     * or hostile document can't be pulled fully into process memory.
     */
    const val MAX_IMPORT_BYTES: Int = 15 * 1024 * 1024

    /**
     * Reads at most [limit] + 1 bytes from [input] — never more, regardless of
     * what the stream or any content-provider-reported size claims. This is
     * the *authoritative* bound on native memory consumption: a document
     * whose full size hugely exceeds [limit] is never fully allocated, since
     * reading stops the instant the (limit + 1)th byte arrives.
     *
     * Returns the exact bytes read when the stream ends at or before [limit]
     * bytes (i.e. the whole document fit). Returns null when the stream still
     * had data beyond [limit] bytes — the document exceeds the limit — again
     * without ever having buffered more than [limit] + 1 bytes to determine
     * that.
     */
    fun readBounded(input: InputStream, limit: Int): ByteArray? {
        val buffer = ByteArray(limit + 1)
        var total = 0
        while (total < buffer.size) {
            val read = input.read(buffer, total, buffer.size - total)
            if (read < 0) break
            total += read
        }
        return if (total > limit) null else buffer.copyOf(total)
    }

    /** Pure outcome classification for a native SAF read — testable without Android framework classes. */
    sealed class ImportReadOutcome {
        object NoSelection : ImportReadOutcome()
        object Unreadable : ImportReadOutcome()
        object TooLarge : ImportReadOutcome()
        data class Success(val byteCount: Int) : ImportReadOutcome()
    }

    /**
     * [boundedRead] must be the result of [readBounded] (or null if the
     * stream itself could not be opened — distinguished by [streamOpened]).
     * Never derives its verdict from a content provider's declared size —
     * only from what was actually, boundedly read.
     */
    fun classifyImportRead(hasSelection: Boolean, streamOpened: Boolean, boundedRead: ByteArray?): ImportReadOutcome {
        if (!hasSelection) return ImportReadOutcome.NoSelection
        if (!streamOpened) return ImportReadOutcome.Unreadable
        return if (boundedRead == null) ImportReadOutcome.TooLarge else ImportReadOutcome.Success(boundedRead.size)
    }
}
