package com.healthchecker.companion.healthconnect

/**
 * Distinguishes a legitimate empty Health Connect query from a false empty
 * (query never performed). Used by manual and worker sync paths.
 */
enum class QueryDisposition {
    /** Query ran successfully; observations/tombstones may be empty. */
    PERFORMED_OK,

    /** Query ran for granted types while others remain missing (warning). */
    PERFORMED_PARTIAL,

    /** Fatal: no query performed (permissions/config/API). Must not deliver. */
    NOT_PERFORMED_FATAL
}

/**
 * Pure delivery gate — keep manual and worker behavior equivalent.
 */
object SyncDeliveryGate {
    fun shouldDeliver(fetch: HealthConnectReader.FetchResult): Boolean =
        fetch.queryPerformed &&
            fetch.disposition != QueryDisposition.NOT_PERFORMED_FATAL

    fun shouldMarkSuccess(
        fetch: HealthConnectReader.FetchResult,
        ackOk: Boolean,
        cursorAdvanced: Boolean
    ): Boolean =
        shouldDeliver(fetch) && ackOk && cursorAdvanced

    fun visibleError(fetch: HealthConnectReader.FetchResult): String? {
        if (fetch.disposition == QueryDisposition.NOT_PERFORMED_FATAL) {
            return fetch.error ?: "query_not_performed"
        }
        return null
    }

    fun visiblePartialWarning(fetch: HealthConnectReader.FetchResult): Boolean =
        fetch.partialPermissionWarning ||
            fetch.disposition == QueryDisposition.PERFORMED_PARTIAL
}
