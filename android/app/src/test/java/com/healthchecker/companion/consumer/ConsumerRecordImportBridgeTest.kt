package com.healthchecker.companion.consumer

import android.net.Uri
import java.io.ByteArrayInputStream
import java.util.Base64
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28])
class ConsumerRecordImportBridgeTest {

    private val resolver get() = RuntimeEnvironment.getApplication().contentResolver
    private val uri: Uri = Uri.parse("content://com.android.providers.media.documents/document/999")

    @Test
    fun noSelectionReturnsErrorWithoutTouchingContentResolver() {
        val bridge = ConsumerRecordImportBridge(resolver) { null }
        val body = JSONObject(bridge.readSelectedRecordBase64())
        assertEquals(false, body.getBoolean("ok"))
        assertEquals("no_file_selected", body.getString("error"))
    }

    @Test
    fun successfulReadReturnsBase64OfExactSelectedBytes() {
        val payload = "HC329 regression fixture".toByteArray()
        shadowOf(resolver).registerInputStream(uri, ByteArrayInputStream(payload))
        val bridge = ConsumerRecordImportBridge(resolver) { uri }
        val body = JSONObject(bridge.readSelectedRecordBase64())
        assertEquals(true, body.getBoolean("ok"))
        val decoded = Base64.getDecoder().decode(body.getString("base64"))
        assertTrue(payload.contentEquals(decoded))
    }

    @Test
    fun unreadableUriReturnsAnErrorWithoutThrowing() {
        // No stream registered for this URI -> openInputStream throws (matches
        // real Android behavior for a revoked/nonexistent grant far more
        // often than it returns null) -> caught and reported as read_failed.
        // The null-return "file_unreadable" branch is a separate, directly
        // tested pure-function path — see classifyImportReadIsPureAndCoversEveryBranch.
        val bridge = ConsumerRecordImportBridge(resolver) { uri }
        val body = JSONObject(bridge.readSelectedRecordBase64())
        assertEquals(false, body.getBoolean("ok"))
        assertEquals("read_failed", body.getString("error"))
    }

    @Test
    fun oversizedDocumentIsRejectedBeforeBase64Encoding() {
        val oversized = ByteArray(ConsumerSafFileChooserPolicy.MAX_IMPORT_BYTES + 1)
        shadowOf(resolver).registerInputStream(uri, ByteArrayInputStream(oversized))
        val bridge = ConsumerRecordImportBridge(resolver) { uri }
        val body = JSONObject(bridge.readSelectedRecordBase64())
        assertEquals(false, body.getBoolean("ok"))
        assertEquals("file_too_large", body.getString("error"))
    }

    @Test
    fun classifyImportReadIsPureAndCoversEveryBranch() {
        assertTrue(ConsumerSafFileChooserPolicy.classifyImportRead(false, null) is
            ConsumerSafFileChooserPolicy.ImportReadOutcome.NoSelection)
        assertTrue(ConsumerSafFileChooserPolicy.classifyImportRead(true, null) is
            ConsumerSafFileChooserPolicy.ImportReadOutcome.Unreadable)
        assertTrue(ConsumerSafFileChooserPolicy.classifyImportRead(true, ConsumerSafFileChooserPolicy.MAX_IMPORT_BYTES + 1) is
            ConsumerSafFileChooserPolicy.ImportReadOutcome.TooLarge)
        assertTrue(ConsumerSafFileChooserPolicy.classifyImportRead(true, 10) is
            ConsumerSafFileChooserPolicy.ImportReadOutcome.Success)
    }
}
