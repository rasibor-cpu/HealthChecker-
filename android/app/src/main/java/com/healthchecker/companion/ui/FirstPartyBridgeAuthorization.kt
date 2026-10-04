package com.healthchecker.companion.ui

import android.os.Looper
import com.healthchecker.companion.consumer.ConsumerOriginPolicy

/** Thread-safe bridge authorization, refreshed only by confirmed main-thread navigation events. */
class FirstPartyBridgeAuthorization {
    @Volatile
    private var authorized = false

    fun isAuthorized(): Boolean = authorized

    fun invalidate() {
        check(Looper.myLooper() == Looper.getMainLooper()) {
            "bridge_authorization_must_be_invalidated_on_main_thread"
        }
        authorized = false
    }

    fun confirmCommittedPage(
        currentUrl: String?,
        committedUrl: String?,
        policy: ConsumerOriginPolicy?,
    ): Boolean {
        check(Looper.myLooper() == Looper.getMainLooper()) {
            "bridge_authorization_must_be_updated_on_main_thread"
        }
        authorized = currentUrl != null &&
            committedUrl != null &&
            currentUrl == committedUrl &&
            policy?.isAllowed(currentUrl) == true
        return authorized
    }
}
