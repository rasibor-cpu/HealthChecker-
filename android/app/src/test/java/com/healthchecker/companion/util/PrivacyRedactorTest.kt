package com.healthchecker.companion.util

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PrivacyRedactorTest {
    @Test
    fun redactsBearerJsonUrlMultilineAndHealthValues() {
        val samples = listOf(
            "Authorization=Bearer abc.def.ghi",
            "Authorization: Bearer xyzTOKEN",
            "authorization: bearer partial",
            "Bearer deadbeefcafe",
            """{"token":"secret-token-value","pair_code":"123456"}""",
            "pair_code=ABCDEF device_id=dev-1234567890",
            "https://user:pass@example.com/path?token=querytok&x=1",
            "Authorization=Bearer line1\ncontinuation",
            """{"value":72,"heart_rate":80,"systolic":120}""",
            "response_body={\"observations\":[{\"value\":99}]}",
            "device_token=super-secret-device-token"
        )
        for (s in samples) {
            val out = PrivacyRedactor.redact(s)
            assertFalse("leaked in: $s -> $out", out.contains("abc.def.ghi"))
            assertFalse("leaked in: $s -> $out", out.contains("xyzTOKEN"))
            assertFalse("leaked in: $s -> $out", out.contains("deadbeefcafe"))
            assertFalse("leaked in: $s -> $out", out.contains("secret-token-value"))
            assertFalse("leaked in: $s -> $out", out.contains("123456"))
            assertFalse("leaked in: $s -> $out", out.contains("ABCDEF"))
            assertFalse("leaked in: $s -> $out", out.contains("dev-1234567890"))
            assertFalse("leaked in: $s -> $out", out.contains("user:pass"))
            assertFalse("leaked in: $s -> $out", out.contains("querytok"))
            assertFalse("leaked in: $s -> $out", out.contains("super-secret-device-token"))
            assertTrue(out.contains("[redacted]") || !s.contains("Bearer"))
        }
        val hr = PrivacyRedactor.redact("""{"heart_rate":88,"value":88}""")
        assertFalse(hr.contains("88"))
    }

    @Test
    fun doesNotRetainBearerPrefixOrSuffixFragments() {
        val out = PrivacyRedactor.redact("Authorization=Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig")
        assertFalse(out.contains("eyJ"))
        assertFalse(out.contains("payload"))
        assertFalse(out.contains("sig"))
        assertTrue(out.contains("[redacted]"))
    }
}
