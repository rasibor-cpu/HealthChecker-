package com.healthchecker.companion.ui

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConsumerInAppBackPolicyTest {
    @Test
    fun jsonTrueMeansInAppNavigationConsumedBack() {
        assertTrue(ConsumerInAppBackPolicy.didHandleInApp("true"))
        assertTrue(ConsumerInAppBackPolicy.didHandleInApp("\"true\""))
        assertTrue(ConsumerInAppBackPolicy.didHandleInApp("  \"true\"  "))
    }

    @Test
    fun dashboardOrMissingNavAllowsActivityFinish() {
        assertFalse(ConsumerInAppBackPolicy.didHandleInApp("false"))
        assertFalse(ConsumerInAppBackPolicy.didHandleInApp("\"false\""))
        assertFalse(ConsumerInAppBackPolicy.didHandleInApp("null"))
        assertFalse(ConsumerInAppBackPolicy.didHandleInApp(null))
        assertFalse(ConsumerInAppBackPolicy.didHandleInApp(""))
    }

    @Test
    fun scriptInvokesConsumerNavWithoutJavascriptInterface() {
        val script = ConsumerInAppBackPolicy.HANDLE_SCRIPT
        assertTrue(script.contains("HCConsumerNav.handleSystemBack()"))
        assertFalse(script.contains("addJavascriptInterface"))
        assertFalse(script.contains("goBack()"))
    }
}
