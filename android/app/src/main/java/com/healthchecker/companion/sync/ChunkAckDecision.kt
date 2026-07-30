package com.healthchecker.companion.sync

import com.healthchecker.companion.host.HostClient

/**
 * Pure HC-306I-R11 chunk acknowledgment policy.
 *
 * Timeout / transport failures leave the same chunk pending (retryable).
 * Only a durable acknowledgment matching the active chunk identity advances progress.
 * Cursor/token-scope finalization is required only for the final chunk.
 */
object ChunkAckDecision {
    sealed class Action {
        data class RetrySameChunk(val reason: String) : Action()
        data class ClearAndFail(val reason: String) : Action()
        data class AdvanceToNextChunk(val next: PendingBatch) : Action()
        data class FinalizeCursor(val cursorToken: String?) : Action()
    }

    fun decide(pending: PendingBatch, ack: HostClient.DeliveryAck): Action {
        val chunk = pending.activeChunk()
            ?: return Action.RetrySameChunk("pending_chunk_missing")

        if (ack.status == "unauthorized" || ack.status == "revoked") {
            return Action.ClearAndFail(ack.status)
        }

        // Timeout / transport / non-matching ack: never advance plan or cursor.
        val durableAck = ack.ok && ack.ackBatchId == chunk.batchId
        if (!durableAck) {
            return Action.RetrySameChunk(ack.error ?: ack.status.ifBlank { "delivery_not_durable" })
        }

        if (!pending.isFinalChunk()) {
            val next = pending.advanceChunk()
                ?: return Action.RetrySameChunk("pending_chunk_advance_failed")
            return Action.AdvanceToNextChunk(next)
        }

        if (!ack.cursorAdvanced) {
            return Action.RetrySameChunk(ack.error ?: "partial_no_cursor")
        }
        return Action.FinalizeCursor(ack.nextCursorToken ?: pending.nextChangesToken)
    }
}
