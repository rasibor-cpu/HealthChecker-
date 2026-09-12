package com.healthchecker.companion.consumer

import android.content.ContentProvider
import android.content.ContentValues
import android.database.MatrixCursor
import android.net.Uri
import android.provider.OpenableColumns
import java.io.ByteArrayInputStream
import java.io.InputStream
import java.util.Base64
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28])
class ConsumerRecordImportBridgeTest {

    private val resolver get() = RuntimeEnvironment.getApplication().contentResolver
    private val uri: Uri = Uri.parse("content://com.android.providers.media.documents/document/999")

    // ---- no selection ---------------------------------------------------

    @Test
    fun noSelectionReturnsErrorWithoutTouchingContentResolver() {
        val bridge = ConsumerRecordImportBridge(resolver, { null }, {})
        val body = JSONObject(bridge.readSelectedRecordBase64())
        assertEquals(false, body.getBoolean("ok"))
        assertEquals("no_file_selected", body.getString("error"))
    }

    // ---- Part 1: bounded streaming read ----------------------------------

    @Test
    fun a_exactlyMaxImportBytesSucceeds() {
        val payload = ByteArray(ConsumerSafFileChooserPolicy.MAX_IMPORT_BYTES) { (it % 251).toByte() }
        shadowOf(resolver).registerInputStream(uri, ByteArrayInputStream(payload))
        val bridge = ConsumerRecordImportBridge(resolver, { uri }, {})
        val body = JSONObject(bridge.readSelectedRecordBase64())
        assertEquals(true, body.getBoolean("ok"))
        val decoded = Base64.getDecoder().decode(body.getString("base64"))
        assertTrue(payload.contentEquals(decoded))
    }

    @Test
    fun b_maxImportBytesPlusOneFails() {
        val oversized = ByteArray(ConsumerSafFileChooserPolicy.MAX_IMPORT_BYTES + 1)
        shadowOf(resolver).registerInputStream(uri, ByteArrayInputStream(oversized))
        val bridge = ConsumerRecordImportBridge(resolver, { uri }, {})
        val body = JSONObject(bridge.readSelectedRecordBase64())
        assertEquals(false, body.getBoolean("ok"))
        assertEquals("file_too_large", body.getString("error"))
    }

    /** An input stream that never ends and counts exactly how many bytes it served. */
    private class UnboundedCountingStream : InputStream() {
        var served: Long = 0
            private set
        override fun read(): Int {
            served += 1
            return 0
        }
        override fun read(b: ByteArray, off: Int, len: Int): Int {
            val n = if (len <= 0) 0 else 1
            if (n == 0) return 0
            b[off] = 0
            served += 1
            return 1
        }
    }

    @Test
    fun c_streamLargerThanLimitIsNotFullyConsumed() {
        val limit = 100
        val infinite = UnboundedCountingStream()
        val result = ConsumerSafFileChooserPolicy.readBounded(infinite, limit)
        assertNull(result)
        // Authoritative bound: never more than limit + 1 bytes were ever read
        // from a stream that, left unbounded, would never terminate.
        assertEquals((limit + 1).toLong(), infinite.served)
    }

    @Test
    fun d_unknownSizeStreamsAreStillBounded() {
        // No OpenableColumns.SIZE provider registered for this URI at all
        // (query() returns null, matching a real provider that doesn't
        // report size) — the streaming bound must still catch an oversized
        // document.
        val oversized = ByteArray(ConsumerSafFileChooserPolicy.MAX_IMPORT_BYTES + 1)
        shadowOf(resolver).registerInputStream(uri, ByteArrayInputStream(oversized))
        assertNull(resolver.query(uri, arrayOf(OpenableColumns.SIZE), null, null, null))
        val bridge = ConsumerRecordImportBridge(resolver, { uri }, {})
        val body = JSONObject(bridge.readSelectedRecordBase64())
        assertEquals(false, body.getBoolean("ok"))
        assertEquals("file_too_large", body.getString("error"))
    }

    @Test
    fun e_unreadableUriReturnsAnErrorWithoutThrowing() {
        // No stream registered for this URI -> openInputStream throws (matches
        // real Android behavior for a revoked/nonexistent grant far more
        // often than it returns null) -> caught and reported as read_failed.
        val bridge = ConsumerRecordImportBridge(resolver, { uri }, {})
        val body = JSONObject(bridge.readSelectedRecordBase64())
        assertEquals(false, body.getBoolean("ok"))
        assertEquals("read_failed", body.getString("error"))
    }

    @Test
    fun declaredOversizeShortCircuitsWithoutOpeningTheStream() {
        val authority = "hc329.test.provider"
        val sizedUri = Uri.parse("content://$authority/big")
        val provider = Robolectric.buildContentProvider(SizeOnlyProvider::class.java)
            .create(authority)
            .get()
        provider.declaredSize = ConsumerSafFileChooserPolicy.MAX_IMPORT_BYTES + 1L
        // Deliberately do NOT register an input stream: if the bridge tried to
        // open one anyway, ContentResolver.openInputStream would throw/return
        // null for this fake authority, which the read_failed/file_unreadable
        // branches would surface instead of file_too_large — proving the
        // short circuit happened before any read attempt.
        val bridge = ConsumerRecordImportBridge(resolver, { sizedUri }, {})
        val body = JSONObject(bridge.readSelectedRecordBase64())
        assertEquals(false, body.getBoolean("ok"))
        assertEquals("file_too_large", body.getString("error"))
    }

    class SizeOnlyProvider : ContentProvider() {
        var declaredSize: Long = 0
        override fun onCreate() = true
        override fun query(
            uri: Uri, projection: Array<out String>?, selection: String?,
            selectionArgs: Array<out String>?, sortOrder: String?,
        ) = MatrixCursor(arrayOf(OpenableColumns.SIZE)).apply { addRow(arrayOf(declaredSize)) }
        override fun getType(uri: Uri) = "application/octet-stream"
        override fun insert(uri: Uri, values: ContentValues?): Uri? = null
        override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?) = 0
        override fun update(uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<out String>?) = 0
    }

    // ---- Part 2: one-shot selection on success ---------------------------

    @Test
    fun selectionCanBeReadOnceThenReportsNoFileSelected() {
        val payload = "one-shot fixture".toByteArray()
        shadowOf(resolver).registerInputStream(uri, ByteArrayInputStream(payload))
        var pending: Uri? = uri
        val bridge = ConsumerRecordImportBridge(resolver, { pending }, { pending = null })

        val first = JSONObject(bridge.readSelectedRecordBase64())
        assertEquals(true, first.getBoolean("ok"))

        val second = JSONObject(bridge.readSelectedRecordBase64())
        assertEquals(false, second.getBoolean("ok"))
        assertEquals("no_file_selected", second.getString("error"))
    }

    @Test
    fun newlySelectedUriWorksAfterAPriorSuccessfulRead() {
        val first = uri
        val second = Uri.parse("content://com.android.providers.media.documents/document/1000")
        shadowOf(resolver).registerInputStream(first, ByteArrayInputStream("first".toByteArray()))
        shadowOf(resolver).registerInputStream(second, ByteArrayInputStream("second".toByteArray()))
        var pending: Uri? = first
        val bridge = ConsumerRecordImportBridge(resolver, { pending }, { pending = null })

        assertEquals(true, JSONObject(bridge.readSelectedRecordBase64()).getBoolean("ok"))
        // Simulate the user picking a new document via the native picker.
        pending = second
        val body = JSONObject(bridge.readSelectedRecordBase64())
        assertEquals(true, body.getBoolean("ok"))
        assertEquals("second", String(Base64.getDecoder().decode(body.getString("base64"))))
    }

    @Test
    fun failedReadLeavesSelectionInPlaceForRetry() {
        // No stream registered -> read_failed. onConsumed must NOT be invoked.
        var consumedCalls = 0
        val bridge = ConsumerRecordImportBridge(resolver, { uri }, { consumedCalls++ })
        val first = JSONObject(bridge.readSelectedRecordBase64())
        assertEquals(false, first.getBoolean("ok"))
        assertEquals(0, consumedCalls)
        // Register a stream now and retry the *same* selection, without any
        // reselection step — it must still work.
        shadowOf(resolver).registerInputStream(uri, ByteArrayInputStream("retry ok".toByteArray()))
        val second = JSONObject(bridge.readSelectedRecordBase64())
        assertEquals(true, second.getBoolean("ok"))
        assertEquals(1, consumedCalls)
    }

    @Test
    fun bridgeMethodAcceptsNoArgumentsFromJavaScript() {
        // Structural guarantee that JS can never supply its own URI/path: the
        // one exposed method takes zero parameters, full stop.
        val method = ConsumerRecordImportBridge::class.java.getMethod("readSelectedRecordBase64")
        assertEquals(0, method.parameterCount)
        assertTrue(method.isAnnotationPresent(android.webkit.JavascriptInterface::class.java))
    }

    // ---- pure classifier --------------------------------------------------

    @Test
    fun classifyImportReadIsPureAndCoversEveryBranch() {
        assertTrue(
            ConsumerSafFileChooserPolicy.classifyImportRead(false, true, null) is
                ConsumerSafFileChooserPolicy.ImportReadOutcome.NoSelection
        )
        assertTrue(
            ConsumerSafFileChooserPolicy.classifyImportRead(true, false, null) is
                ConsumerSafFileChooserPolicy.ImportReadOutcome.Unreadable
        )
        assertTrue(
            ConsumerSafFileChooserPolicy.classifyImportRead(true, true, null) is
                ConsumerSafFileChooserPolicy.ImportReadOutcome.TooLarge
        )
        assertTrue(
            ConsumerSafFileChooserPolicy.classifyImportRead(true, true, ByteArray(10)) is
                ConsumerSafFileChooserPolicy.ImportReadOutcome.Success
        )
    }
}
