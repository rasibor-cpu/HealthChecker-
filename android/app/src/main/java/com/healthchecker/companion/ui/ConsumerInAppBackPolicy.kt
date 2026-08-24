package com.healthchecker.companion.ui

/**
 * HC325-R4 — Android system Back must follow the in-app consumer hierarchy.
 *
 * The consumer is a same-document WebView. WebView history is not used because
 * it can cross the approved origin/path boundary. Instead the page's
 * [HCConsumerNav.handleSystemBack] decides whether Back was consumed.
 */
object ConsumerInAppBackPolicy {
    const val HANDLE_SCRIPT =
        "(function(){try{return !!(window.HCConsumerNav&&HCConsumerNav.handleSystemBack());}catch(e){return false;}})()"

    /**
     * `evaluateJavascript` returns a JSON token. `true` becomes `"true"`.
     * Any other result means the page is at Dashboard/Welcome (or nav is
     * unavailable) and the Activity may finish.
     */
    fun didHandleInApp(jsResult: String?): Boolean {
        val normalized = jsResult?.trim()?.trim('"')?.lowercase() ?: return false
        return normalized == "true"
    }
}
