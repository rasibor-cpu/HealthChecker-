package com.healthchecker.companion.ui

import com.healthchecker.companion.consumer.ConsumerOriginPolicy
import com.healthchecker.companion.consumer.ConsumerOriginLock
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28])
class FirstPartyBridgeAuthorizationTest {
    private val policy = ConsumerOriginPolicy.create(
        ConsumerOriginLock.PRODUCTION_ORIGIN,
        isDebugBuild = false,
        allowCleartextLocalDev = false,
    )
    private val approvedUrl = ConsumerOriginLock.PRODUCTION_MOBILE_URL

    @Test
    fun authorizationDefaultsFailClosed() {
        assertFalse(FirstPartyBridgeAuthorization().isAuthorized())
    }

    @Test
    fun onlyAnApprovedCommittedCurrentPageEnablesAuthorization() {
        val authorization = FirstPartyBridgeAuthorization()

        assertTrue(
            authorization.confirmCommittedPage(
                approvedUrl,
                approvedUrl,
                policy,
            )
        )
        assertTrue(authorization.isAuthorized())

        assertFalse(
            authorization.confirmCommittedPage(
                "https://evil.example/mobile",
                "https://evil.example/mobile",
                policy,
            )
        )
        assertFalse(authorization.isAuthorized())
    }

    @Test
    fun navigationInvalidatesAndBackNavigationWaitsForCommitConfirmation() {
        val authorization = FirstPartyBridgeAuthorization()
        authorization.confirmCommittedPage(
            approvedUrl,
            approvedUrl,
            policy,
        )
        assertTrue(authorization.isAuthorized())

        authorization.invalidate()
        assertFalse(authorization.isAuthorized())

        assertFalse(
            authorization.confirmCommittedPage(
                approvedUrl,
                "https://evil.example/mobile",
                policy,
            )
        )
        assertFalse(authorization.isAuthorized())

        assertTrue(
            authorization.confirmCommittedPage(
                approvedUrl,
                approvedUrl,
                policy,
            )
        )
        assertTrue(authorization.isAuthorized())
    }

    @Test
    fun authorizationReadsRemainSafeAcrossThreads() {
        val authorization = FirstPartyBridgeAuthorization()
        var backgroundRead = true
        val thread = Thread { backgroundRead = authorization.isAuthorized() }
        thread.start()
        thread.join()

        assertFalse(backgroundRead)
    }

    @Test
    fun authorizationUpdatesAreRejectedOffTheMainThread() {
        val authorization = FirstPartyBridgeAuthorization()
        var rejected = false
        val thread = Thread {
            try {
                authorization.confirmCommittedPage(
                    approvedUrl,
                    approvedUrl,
                    policy,
                )
            } catch (_: IllegalStateException) {
                rejected = true
            }
        }
        thread.start()
        thread.join()

        assertTrue(rejected)
        assertFalse(authorization.isAuthorized())
    }
}
