import unittest

from backend.health_vault.recovery_profiles import (
    QUESTION_CATALOG,
    RecoveryProfileError,
    catalog,
    create_recovery_profile,
    deserialize_profile,
    serialize_profile,
)


class HC311F8DRecoveryProfilesTests(unittest.TestCase):

    def setUp(self):
        self.questions = [
            "Q01", "Q02", "Q03", "Q04", "Q05",
            "Q06", "Q07", "Q08", "Q09", "Q10",
        ]

    def test_catalog_has_at_least_twenty_questions(self):
        self.assertGreaterEqual(len(QUESTION_CATALOG), 20)

    def test_catalog_ids_unique(self):
        ids = [item[0] for item in QUESTION_CATALOG]
        self.assertEqual(len(ids), len(set(ids)))

    def test_two_users_get_independent_salts(self):
        a = create_recovery_profile(
            "user_a",
            self.questions,
        )

        b = create_recovery_profile(
            "user_b",
            self.questions,
        )

        self.assertNotEqual(
            a.question_salt_hex,
            b.question_salt_hex,
        )

    def test_same_questions_same_set_identifier(self):
        a = create_recovery_profile(
            "user_a",
            self.questions,
        )

        b = create_recovery_profile(
            "user_b",
            self.questions,
        )

        self.assertEqual(
            a.question_set_id,
            b.question_set_id,
        )

    def test_profile_contains_no_answers(self):
        profile = create_recovery_profile(
            "user_a",
            self.questions,
        )

        payload = serialize_profile(profile).decode()

        self.assertNotIn('"answer"', payload)
        self.assertNotIn('"answers"', payload)
        self.assertNotIn("passphrase", payload)
        self.assertNotIn("data_key", payload)

    def test_roundtrip(self):
        original = create_recovery_profile(
            "user_a",
            self.questions,
        )

        recovered = deserialize_profile(
            serialize_profile(original)
        )

        self.assertEqual(original, recovered)

    def test_too_few_questions_rejected(self):
        with self.assertRaises(RecoveryProfileError):
            create_recovery_profile(
                "user_a",
                self.questions[:9],
            )

    def test_unknown_question_rejected(self):
        bad = list(self.questions)
        bad[-1] = "Q999"

        with self.assertRaises(RecoveryProfileError):
            create_recovery_profile(
                "user_a",
                bad,
            )

    def test_duplicate_question_rejected(self):
        bad = list(self.questions)
        bad[-1] = bad[0]

        with self.assertRaises(RecoveryProfileError):
            create_recovery_profile(
                "user_a",
                bad,
            )

    def test_invalid_profile_id_rejected(self):
        with self.assertRaises(RecoveryProfileError):
            create_recovery_profile(
                "../bad/profile",
                self.questions,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
