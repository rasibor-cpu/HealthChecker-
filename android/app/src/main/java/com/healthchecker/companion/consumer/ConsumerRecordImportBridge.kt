package com.healthchecker.companion.consumer

import android.content.ContentResolver
import android.net.Uri
import android.provider.OpenableColumns
import android.webkit.JavascriptInterface
import com.healthchecker.companion.util.SafeLog
import java.util.Base64

/**
 * HC329 — narrowly scoped native bridge for reading the bytes of the single
 * SAF content:// document the user most recently selected through
 * [ConsumerSafFileChooserPolicy]. JavaScript cannot request an arbitrary URI:
 * [pendingUriProvider] is native-controlled and only ever returns the URI the
 * file-chooser callback last recorded, so this bridge cannot be used to read
 * anything the user didn't explicitly pick via the system picker.
 *
 * This exists because handing a content:// URI to the WebView's own
 * `<input type=file>` and letting Chromium stream it directly into a
 * `fetch()`/`FormData` multipart body is unreliable for some content
 * providers/WebView builds — it can silently fail or reject with a generic
 * "Failed to fetch" with no actionable detail. Reading the bytes natively and
 * handing them to JS as a plain in-memory Blob avoids that failure mode.
 */
class ConsumerRecordImportBridge(
    private val contentResolver: ContentResolver,
    private val pendingUriProvider: () -> Uri?,
) {
    @JavascriptInterface
    fun readSelectedRecordBase64(): String {
        val uri = pendingUriProvider()
        val outcome = ConsumerSafFileChooserPolicy.classifyImportRead(uri != null, null)
        if (outcome is ConsumerSafFileChooserPolicy.ImportReadOutcome.NoSelection) {
            return errorJson("no_file_selected")
        }
        val safeUri = uri ?: return errorJson("no_file_selected")
        return try {
            val bytes = contentResolver.openInputStream(safeUri)?.use { it.readBytes() }
            when (ConsumerSafFileChooserPolicy.classifyImportRead(true, bytes?.size)) {
                is ConsumerSafFileChooserPolicy.ImportReadOutcome.Unreadable -> errorJson("file_unreadable")
                is ConsumerSafFileChooserPolicy.ImportReadOutcome.TooLarge -> errorJson("file_too_large")
                is ConsumerSafFileChooserPolicy.ImportReadOutcome.Success -> {
                    val name = displayName(safeUri) ?: "upload"
                    val mimeType = contentResolver.getType(safeUri) ?: "application/octet-stream"
                    val encoded = Base64.getEncoder().encodeToString(bytes)
                    "{\"ok\":true,\"name\":${jsonString(name)},\"mime_type\":${jsonString(mimeType)}," +
                        "\"base64\":\"$encoded\"}"
                }
                is ConsumerSafFileChooserPolicy.ImportReadOutcome.NoSelection -> errorJson("no_file_selected")
            }
        } catch (t: Throwable) {
            SafeLog.e("saf_native_read_failed", t)
            errorJson("read_failed")
        }
    }

    private fun displayName(uri: Uri): String? {
        return try {
            contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
                val idx = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if (idx >= 0 && cursor.moveToFirst()) cursor.getString(idx) else null
            }
        } catch (_: Throwable) {
            null
        }
    }

    private fun errorJson(code: String) = "{\"ok\":false,\"error\":${jsonString(code)}}"

    private fun jsonString(value: String) =
        "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\""
}
