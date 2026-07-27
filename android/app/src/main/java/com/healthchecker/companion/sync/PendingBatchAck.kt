package com.healthchecker.companion.sync

/**
 * Pure decision helper for durable pending-batch acknowledgement.
 * Late acks for a prior batch must not clear a newer pending identity.
 */
object PendingBatchAck {
    fun shouldClearPending(
        pendingBatchId: String,
        ackOk: Boolean,
        cursorAdvanced: Boolean,
        ackBatchId: String?,
        status: String
    ): Boolean {
        // Permanent auth failures: clear/quarantine pending so dead tokens are not replayed forever.
        // Cursor must not advance on these paths (caller enforces via cursorAdvanced=false).
        if (status == "unauthorized" || status == "revoked") {
            return true
        }
        if (!ackOk || !cursorAdvanced) {
            return false
        }
        // Require exact batch identity match — late ack of an older batch cannot clear a newer one.
        return !ackBatchId.isNullOrBlank() && ackBatchId == pendingBatchId
    }
}
