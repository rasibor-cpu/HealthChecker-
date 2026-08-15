import unittest

from backend.health_vault.recovery_enrollment import (
    RecoveryEnrollmentError,
    enroll_recovery_profile,
    serialize_enrollment_metadata,
)


QUESTIONS = [
    "Q01", "Q02", "Q03", "Q04", "Q05",
    "Q06", "Q07", "Q08", "Q09", "Q10",
]

ANSWERS = [
    "Blue",
    "Lagos",
    "Benin City",
    "Airport Road",
    "Example Primary School",
    "Toyota",
    "January Friend",
    "Maple Estate",
    "Mr Smith",
    "Family Beach",
]


class HC311F8DRecoveryEnrollmentTests(unittest.TestCase):

    def test_matching_confirmation_succeeds(self):
        result = enroll_recovery_profile(
            "user_a",
            QUESTIONS,
            ANSWERS,
            ANSWERS,
        )

        self.assertEqual(
            result.profile.profile_id,
            "user_a",
        )

        self.assertEqual(
            len(result.recovery_passphrase),
            64,
        )

    def test_single_wrong_confirmation_fails(self):
        confirm = list(ANSWERS)
        confirm[4] = "Wrong School"

        with self.assertRaises(RecoveryEnrollmentError):
            enroll_recovery_profile(
                "user_a",
                QUESTIONS,
                ANSWERS,
                confirm,
            )

    def test_answer_count_mismatch_fails(self):
        with self.assertRaises(RecoveryEnrollmentError):
            enroll_recovery_profile(
                "user_a",
                QUESTIONS,
                ANSWERS[:-1],
                ANSWERS,
            )

    def test_metadata_contains_no_answers(self):
        result = enroll_recovery_profile(
            "user_a",
            QUESTIONS,
            ANSWERS,
            ANSWERS,
        )

        payload = serialize_enrollment_metadata(result)

        for answer in ANSWERS:
            self.assertNotIn(
                answer.encode("utf-8"),
                payload,
            )

    def test_metadata_contains_no_recovery_passphrase(self):
        result = enroll_recovery_profile(
            "user_a",
            QUESTIONS,
            ANSWERS,
            ANSWERS,
        )

        payload = serialize_enrollment_metadata(result)

        self.assertNotIn(
            result.recovery_passphrase.encode("utf-8"),
            payload,
        )

    def test_two_profiles_have_independent_salts(self):
        a = enroll_recovery_profile(
            "user_a",
            QUESTIONS,
            ANSWERS,
            ANSWERS,
        )

        b = enroll_recovery_profile(
            "user_b",
            QUESTIONS,
            ANSWERS,
            ANSWERS,
        )

        self.assertNotEqual(
            a.profile.question_salt_hex,
            b.profile.question_salt_hex,
        )

        self.assertNotEqual(
            a.recovery_passphrase,
            b.recovery_passphrase,
        )

    def test_question_order_is_profile_specific(self):
        reversed_questions = list(reversed(QUESTIONS))
        reversed_answers = list(reversed(ANSWERS))

        a = enroll_recovery_profile(
            "user_a",
            QUESTIONS,
            ANSWERS,
            ANSWERS,
        )

        b = enroll_recovery_profile(
            "user_b",
            reversed_questions,
            reversed_answers,
            reversed_answers,
        )

        self.assertNotEqual(
            a.profile.question_set_id,
            b.profile.question_set_id,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
