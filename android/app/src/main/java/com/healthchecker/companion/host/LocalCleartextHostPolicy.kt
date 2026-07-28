package com.healthchecker.companion.host

/**
 * Shared debug-local cleartext host policy for [PairingInputs] and [ProductionConfigGate].
 *
 * Literal classification only — no DNS. Does not broaden beyond the historical app gate:
 * localhost, 127.0.0.0/8, 10.0.0.0/8, 192.168.0.0/16.
 * 172.16.0.0/12 and IPv6 loopback were never part of that gate and remain rejected.
 */
object LocalCleartextHostPolicy {

    fun isPermitted(host: String): Boolean {
        val h = host.trim().lowercase()
        if (h.isEmpty()) return false
        // Exact hostname only (historical). Not a DNS lookup.
        if (h == "localhost") return true
        // IPv6 (including ::1) was never in the prior gate — reject without resolving.
        if (h.contains(':') || h.startsWith("[")) return false
        val octets = parseLiteralIpv4(h) ?: return false
        return isIntendedPrivateOrLoopback(octets)
    }

    /**
     * Strict dotted-decimal IPv4: exactly four decimal octets in 0..255.
     * Rejects leading zeros, empty labels, non-digits, and hostname tails.
     */
    fun parseLiteralIpv4(host: String): IntArray? {
        val parts = host.split('.')
        if (parts.size != 4) return null
        val out = IntArray(4)
        for (i in 0 until 4) {
            val part = parts[i]
            if (part.isEmpty() || !part.all { it in '0'..'9' }) return null
            // Disallow leading zeros (e.g. 127.0.0.01) — not canonical literal form.
            if (part.length > 1 && part[0] == '0') return null
            val value = part.toIntOrNull() ?: return null
            if (value !in 0..255) return null
            out[i] = value
        }
        return out
    }

    private fun isIntendedPrivateOrLoopback(octets: IntArray): Boolean {
        val a = octets[0]
        val b = octets[1]
        return when {
            a == 127 -> true // 127.0.0.0/8
            a == 10 -> true // 10.0.0.0/8
            a == 192 && b == 168 -> true // 192.168.0.0/16
            else -> false
        }
    }
}
