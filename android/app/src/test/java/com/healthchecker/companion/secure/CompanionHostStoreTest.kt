package com.healthchecker.companion.secure

import android.content.SharedPreferences
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28], manifest = Config.NONE)
class CompanionHostStoreTest {
    private lateinit var prefs: SharedPreferences
    private lateinit var store: CompanionHostStore

    @Before
    fun setUp() {
        prefs = RuntimeEnvironment.getApplication().getSharedPreferences("hc_host_store_test", 0)
        prefs.edit().clear().commit()
        store = CompanionHostStore(prefs)
    }

    @Test
    fun failedPairDraftOnlyPreservesActiveHostTokenDevice() {
        assertTrue(
            store.commitPairedSession(
                "https://trusted.example",
                "device-trusted",
                "token-trusted",
            )
        )
        // Simulate failed pair attempt: UI writes draft only.
        store.setDraftHostUrl("https://attacker.example")
        assertEquals("https://trusted.example", store.getActiveHostUrl())
        assertEquals("device-trusted", store.getDeviceId())
        assertEquals("token-trusted", store.getDeviceToken())
        assertEquals("https://attacker.example", store.getDraftHostUrl())
        val integrity = store.assessPairingIntegrity()
        assertTrue(integrity is CompanionHostStore.PairingIntegrity.Paired)
        assertEquals(
            "https://trusted.example",
            (integrity as CompanionHostStore.PairingIntegrity.Paired).activeHost,
        )
    }

    @Test
    fun debugPrefillChangesDraftOnly() {
        assertTrue(
            store.commitPairedSession(
                "https://trusted.example",
                "device-trusted",
                "token-trusted",
            )
        )
        store.setDraftHostUrl("http://127.0.0.1:8877")
        assertEquals("http://127.0.0.1:8877", store.getDraftHostUrl())
        assertEquals("https://trusted.example", store.getActiveHostUrl())
        assertEquals("token-trusted", store.getDeviceToken())
        assertEquals("device-trusted", store.getDeviceId())
    }

    @Test
    fun successfulPairPromotesNormalizedDraftAtomically() {
        store.setDraftHostUrl("https://new.example:8443")
        assertTrue(
            store.commitPairedSession(
                "https://new.example:8443",
                "device-new",
                "token-new",
            )
        )
        assertEquals("https://new.example:8443", store.getActiveHostUrl())
        assertEquals("device-new", store.getDeviceId())
        assertEquals("token-new", store.getDeviceToken())
        assertNull(store.getDraftHostUrl())
    }

    @Test
    fun syncAndDeliveryIgnoreDraft() {
        assertTrue(
            store.commitPairedSession(
                "https://trusted.example",
                "device-trusted",
                "token-trusted",
            )
        )
        store.setDraftHostUrl("https://draft-only.example")
        // Active host is the only delivery destination getter.
        assertEquals("https://trusted.example", store.getActiveHostUrl())
        assertEquals("https://draft-only.example", store.getDraftHostUrl())
        assertFalse(store.getActiveHostUrl() == store.getDraftHostUrl())
    }

    @Test
    fun recreationPreservesDraftWithoutChangingActive() {
        assertTrue(
            store.commitPairedSession(
                "https://trusted.example",
                "device-trusted",
                "token-trusted",
            )
        )
        store.setDraftHostUrl("https://editing.example")
        // Simulate activity recreation: new store over same prefs.
        val reloaded = CompanionHostStore(prefs)
        assertEquals("https://editing.example", reloaded.getDraftHostUrl())
        assertEquals("https://trusted.example", reloaded.getActiveHostUrl())
        assertEquals("https://editing.example", reloaded.displayHostForEditing())
        assertEquals("token-trusted", reloaded.getDeviceToken())
    }

    @Test
    fun partialCorruptLegacyStateFailsClosed() {
        prefs.edit()
            .putString(CompanionHostStore.KEY_HOST, "https://orphan-host.example")
            .remove(CompanionHostStore.KEY_TOKEN)
            .remove(CompanionHostStore.KEY_DEVICE_ID)
            .commit()
        val integrity = store.assessPairingIntegrity()
        assertTrue(integrity is CompanionHostStore.PairingIntegrity.Inconsistent)
        assertEquals(
            "pairing_state_inconsistent",
            (integrity as CompanionHostStore.PairingIntegrity.Inconsistent).reason,
        )
        // Draft must not repair/activate orphan host.
        store.setDraftHostUrl("https://draft.example")
        assertTrue(store.assessPairingIntegrity() is CompanionHostStore.PairingIntegrity.Inconsistent)
        assertEquals("https://orphan-host.example", store.getActiveHostUrl())
    }

    @Test
    fun tokenWithoutHostFailsClosed() {
        prefs.edit()
            .putString(CompanionHostStore.KEY_TOKEN, "orphan-token")
            .remove(CompanionHostStore.KEY_HOST)
            .commit()
        assertTrue(store.assessPairingIntegrity() is CompanionHostStore.PairingIntegrity.Inconsistent)
    }

    @Test
    fun pairingCodesAreNeverPersisted() {
        store.setDraftHostUrl("https://vault.example")
        assertTrue(
            store.commitPairedSession(
                "https://vault.example",
                "device-1",
                "token-1",
            )
        )
        val all = prefs.all
        assertFalse(all.keys.any { it.contains("pair", ignoreCase = true) })
        assertFalse(all.values.any { it?.toString()?.equals("SELA2K26", ignoreCase = true) == true })
        assertNull(prefs.getString("pair_code", null))
        assertNull(prefs.getString("pairing_code", null))
    }

    @Test
    fun incompleteCommitRejectedLeavesPriorState() {
        assertTrue(
            store.commitPairedSession(
                "https://trusted.example",
                "device-trusted",
                "token-trusted",
            )
        )
        assertFalse(store.commitPairedSession("https://new.example", "", "token-new"))
        assertEquals("https://trusted.example", store.getActiveHostUrl())
        assertEquals("token-trusted", store.getDeviceToken())
    }

    @Test
    fun legacyPairedHostTokenRemainsUsable() {
        // Existing install: only host_url + token (no draft key).
        prefs.edit()
            .putString(CompanionHostStore.KEY_HOST, "https://legacy.example")
            .putString(CompanionHostStore.KEY_DEVICE_ID, "legacy-device")
            .putString(CompanionHostStore.KEY_TOKEN, "legacy-token")
            .commit()
        val integrity = store.assessPairingIntegrity()
        assertTrue(integrity is CompanionHostStore.PairingIntegrity.Paired)
        assertEquals("https://legacy.example", store.getActiveHostUrl())
        assertNull(store.getDraftHostUrl())
        assertEquals("https://legacy.example", store.displayHostForEditing())
    }

    @Test
    fun logoutClearsDeliveryPairingButPreservesConsumerOrigin() {
        assertTrue(
            store.commitPairedSession(
                "https://health.example",
                "device-owner",
                "token-owner",
            )
        )
        store.setDraftHostUrl("https://draft.example")

        store.clearPairingCredentials()

        assertTrue(store.assessPairingIntegrity() is CompanionHostStore.PairingIntegrity.Unpaired)
        assertNull(store.getActiveHostUrl())
        assertNull(store.getDraftHostUrl())
        assertNull(store.getDeviceId())
        assertNull(store.getDeviceToken())
        assertEquals("https://health.example", store.getConsumerOrigin())
    }
}
