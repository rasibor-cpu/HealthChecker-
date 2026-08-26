package com.healthchecker.companion.consumer

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.webkit.WebSettings
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28])
class ConsumerSafFileChooserPolicyTest {

    @Test
    fun openDocumentIntentUsesSafAndReadGrants() {
        val intent = ConsumerSafFileChooserPolicy.createOpenDocumentIntent(null)
        assertEquals(Intent.ACTION_OPEN_DOCUMENT, intent.action)
        assertTrue(intent.hasCategory(Intent.CATEGORY_OPENABLE))
        assertFalse(intent.getBooleanExtra(Intent.EXTRA_ALLOW_MULTIPLE, true))
        val flags = intent.flags
        assertTrue(flags and Intent.FLAG_GRANT_READ_URI_PERMISSION != 0)
        assertTrue(flags and Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION != 0)
        val types = intent.getStringArrayExtra(Intent.EXTRA_MIME_TYPES)
        assertNotNull(types)
        assertTrue(types!!.toList().containsAll(listOf("application/pdf", "application/json")))
    }

    @Test
    fun chooserPreservesWebViewAcceptTypesWhenSafe() {
        val types = ConsumerSafFileChooserPolicy.mimeTypesForChooser(
            arrayOf("application/pdf", " */* ", "", "image/png")
        )
        assertArrayEquals(arrayOf("application/pdf", "image/png"), types)
    }

    @Test
    fun contentAccessIsEnabledAndFileAccessStaysDisabled() {
        assertTrue(ConsumerSafFileChooserPolicy.ALLOW_CONTENT_ACCESS)
        assertFalse(ConsumerSafFileChooserPolicy.ALLOW_FILE_ACCESS)
        assertFalse(ConsumerSafFileChooserPolicy.ALLOW_FILE_ACCESS_FROM_FILE_URLS)
        assertFalse(ConsumerSafFileChooserPolicy.ALLOW_UNIVERSAL_ACCESS_FROM_FILE_URLS)
        assertEquals(
            WebSettings.MIXED_CONTENT_NEVER_ALLOW,
            ConsumerSafFileChooserPolicy.MIXED_CONTENT_NEVER_ALLOW,
        )
    }

    @Test
    fun selectedContentUriIsReturnedForUploadCallback() {
        val uri = Uri.parse("content://com.android.providers.media.documents/document/123")
        val data = Intent().setData(uri)
        val uris = ConsumerSafFileChooserPolicy.urisFromActivityResult(Activity.RESULT_OK, data)
        assertNotNull(uris)
        assertEquals(1, uris!!.size)
        assertEquals(uri, uris[0])
        assertTrue(ConsumerSafFileChooserPolicy.isSafContentUri(uri))
    }

    @Test
    fun cancelClearsCallbackWithoutUri() {
        assertNull(
            ConsumerSafFileChooserPolicy.urisFromActivityResult(Activity.RESULT_CANCELED, Intent())
        )
        assertNull(ConsumerSafFileChooserPolicy.urisFromActivityResult(Activity.RESULT_OK, null))
        assertTrue(ConsumerSafFileChooserPolicy.shouldCancelPreviousCallback())
    }

    @Test
    fun fileSchemeAndMissingAuthorityAreRejected() {
        assertNull(
            ConsumerSafFileChooserPolicy.urisFromActivityResult(
                Activity.RESULT_OK,
                Intent().setData(Uri.parse("file:///sdcard/secret.pdf")),
            )
        )
        assertFalse(ConsumerSafFileChooserPolicy.isSafContentUri(Uri.parse("file:///tmp/x")))
        assertFalse(ConsumerSafFileChooserPolicy.isSafContentUri(Uri.parse("content:")))
        assertEquals(
            Intent.FLAG_GRANT_READ_URI_PERMISSION,
            ConsumerSafFileChooserPolicy.persistableReadPermissionFlags(),
        )
    }
}
