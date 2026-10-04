package com.healthchecker.companion.ui

data class ConsumerSafeNavigationState(
    val route: String,
    val recordId: String? = null,
) {
    companion object {
        private val allowedRoutes = setOf(
            "dashboard",
            "records",
            "trends",
            "observations",
            "timeline",
            "reports",
            "settings",
            "import",
        )
        private val safeRecordId = Regex("^[A-Za-z0-9_-]{1,128}$")

        fun normalize(route: String?, recordId: String?): ConsumerSafeNavigationState? {
            val safeRoute = route?.trim()?.lowercase() ?: return null
            if (safeRoute !in allowedRoutes) return null
            val safeId = recordId?.trim()?.takeIf { it.isNotEmpty() }
            if (safeId != null && (safeRoute != "records" || !safeRecordId.matches(safeId))) {
                return null
            }
            return ConsumerSafeNavigationState(safeRoute, safeId)
        }
    }
}
