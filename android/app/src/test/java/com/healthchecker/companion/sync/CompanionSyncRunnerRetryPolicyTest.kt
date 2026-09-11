package com.healthchecker.companion.sync

import org.junit.Assert.assertEquals
import org.junit.Test

class CompanionSyncRunnerRetryPolicyTest {

    @Test
    fun sameChunkRetryPolicyIsBounded() {
        assertEquals(6, CompanionSyncRunner.MAX_SAME_CHUNK_ATTEMPTS)
    }

    @Test
    fun sameChunkRetryBaseDelayIsOneSecond() {
        assertEquals(1000L, CompanionSyncRunner.SAME_CHUNK_RETRY_BASE_DELAY_MS)
    }
}
