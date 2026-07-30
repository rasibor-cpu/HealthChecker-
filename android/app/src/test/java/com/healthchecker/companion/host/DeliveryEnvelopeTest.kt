package com.healthchecker.companion.host

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28], manifest = Config.NONE)
class DeliveryEnvelopeTest {
    @Test
    fun finalChunkIncludesNextCursor() {
        val body = DeliveryEnvelope.build(
            batchId = "b1",
            nonce = "n1",
            observations = JSONArray(),
            deletedRecordIds = JSONArray(),
            nextChangesToken = "tok",
            healthConnectStatus = JSONObject().put("availability", "READY"),
            permissions = JSONObject().put("granted_count", 1),
            workmanager = JSONObject().put("unique_name", "hc303a_monitoring_sync"),
            queued = 0,
            includeNextCursor = true,
        )
        assertTrue(body.has("next_cursor"))
        assertTrue(body.getJSONObject("next_cursor").optString("changes_token") == "tok")
    }

    @Test
    fun nonFinalChunkOmitsNextCursor() {
        val body = DeliveryEnvelope.build(
            batchId = "b2",
            nonce = "n2",
            observations = JSONArray().put(JSONObject().put("observation_id", "o1")),
            deletedRecordIds = JSONArray(),
            nextChangesToken = null,
            healthConnectStatus = JSONObject().put("availability", "READY"),
            permissions = JSONObject().put("granted_count", 1),
            workmanager = JSONObject().put("unique_name", "hc303a_monitoring_sync"),
            queued = 1,
            includeNextCursor = false,
        )
        assertFalse(body.has("next_cursor"))
        assertTrue(
            DeliveryEnvelope.measureBytes(
                batchId = "b2",
                nonce = "n2",
                observations = JSONArray().put(JSONObject().put("observation_id", "o1")),
                deletedRecordIds = JSONArray(),
                nextChangesToken = null,
                healthConnectStatus = JSONObject().put("availability", "READY"),
                permissions = JSONObject().put("granted_count", 1),
                workmanager = JSONObject().put("unique_name", "hc303a_monitoring_sync"),
                queued = 1,
                includeNextCursor = false,
            ) < 512_000,
        )
    }
}
