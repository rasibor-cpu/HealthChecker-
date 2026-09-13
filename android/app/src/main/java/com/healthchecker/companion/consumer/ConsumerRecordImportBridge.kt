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
 *
 * **One-shot on success:** once a selection's bytes have been successfully
 * handed to JS, [onConsumed] is invoked so the native side clears the pending
 * selection — a second call without a fresh picker selection then returns
 * `no_file_selected`, minimizing how long a granted document stays readable
 * through this interface. On failure (unreadable, too large, transient I/O
 * error) the selection is deliberately left in place rather than cleared:
 * these failures are not "the bytes were exposed," so there is no exposure
 * reason to force the user back through the system picker, and a transient
 * read failure remains retryable without reselecting.
 */
class ConsumerRecordImportBridge(
    private val contentResolver: ContentResolver,
    private val pendingUriProvider: () -> Uri?,
    private val onConsumed: () -> Unit,
) {
    @JavascriptInterface
    fun readSelectedRecordBase64(): String {
        val uri = pendingUriProvider() ?: return errorJson("no_file_selected")

        // Early-rejection optimization only — a provider's declared size can
        // be absent or wrong, so this never substitutes for the authoritative
        // streaming bound below. It only ever avoids an open+read we already
        // know is pointless.
        if (declaredSizeExceedsLimit(uri)) return errorJson("file_too_large")

        val stream = try {
            contentResolver.openInputStream(uri)
        } catch (t: Throwable) {
            SafeLog.e("saf_native_read_failed", t)
            return errorJson("read_failed")
        }
        if (stream == null) return errorJson("file_unreadable")

        return try {
            stream.use { input ->
                val bounded = ConsumerSafFileChooserPolicy.readBounded(
                    input,
                    ConsumerSafFileChooserPolicy.MAX_IMPORT_BYTES,
                )
                when (
                    ConsumerSafFileChooserPolicy.classifyImportRead(
                        hasSelection = true,
                        streamOpened = true,
                        boundedRead = bounded,
                    )
                ) {
                    ConsumerSafFileChooserPolicy.ImportReadOutcome.TooLarge -> errorJson("file_too_large")
                    is ConsumerSafFileChooserPolicy.ImportReadOutcome.Success -> {
                        val name = displayName(uri) ?: "upload"
                        val mimeType = contentResolver.getType(uri) ?: "application/octet-stream"
                        val encoded = Base64.getEncoder().encodeToString(bounded)
                        onConsumed()
                        "{\"ok\":true,\"name\":${jsonString(name)},\"mime_type\":${jsonString(mimeType)}," +
                            "\"base64\":\"$encoded\"}"
                    }
                    ConsumerSafFileChooserPolicy.ImportReadOutcome.NoSelection,
                    ConsumerSafFileChooserPolicy.ImportReadOutcome.Unreadable,
                    -> {
                        // Unreachable given the checks above; fail closed rather
                        // than silently succeed if that ever changes.
                        errorJson("read_failed")
                    }
                }
            }
        } catch (t: Throwable) {
            SafeLog.e("saf_native_read_failed", t)
            errorJson("read_failed")
        }
    }

    private fun declaredSizeExceedsLimit(uri: Uri): Boolean {
        return try {
            contentResolver.query(uri, arrayOf(OpenableColumns.SIZE), null, null, null)?.use { cursor ->
                val idx = cursor.getColumnIndex(OpenableColumns.SIZE)
                idx >= 0 && cursor.moveToFirst() && !cursor.isNull(idx) &&
                    cursor.getLong(idx) > ConsumerSafFileChooserPolicy.MAX_IMPORT_BYTES
            } ?: false
        } catch (_: Throwable) {
            // A metadata-query failure never blocks the authoritative streaming bound.
            false
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
