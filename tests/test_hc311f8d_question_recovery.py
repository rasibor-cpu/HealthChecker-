import os
import unittest

from backend.health_vault.vault_question_recovery import (
    QuestionRecoveryError,
    combine_answers,
    credential_to_passphrase,
    derive_recovery_credential,
    normalize_answer,
    question_set_id,
)


class HC311F8DQuestionRecoveryTests(unittest.TestCase):

    def setUp(self):
        self.answers = [
            "Blue",
            "Lagos",
            "Benin City",
            "Airport Road",
            "Example Primary School",
            "Toyota",
            "January",
            "Maple Street",
        ]

    def test_normalization_case(self):
        self.assertEqual(normalize_answer("  BLUE  "), "blue")

    def test_normalization_whitespace(self):
        self.assertEqual(
            normalize_answer("New   York"),
            "new york",
        )

    def test_all_questions_required(self):
        with self.assertRaises(QuestionRecoveryError):
            combine_answers(self.answers[:7])

    def test_deterministic_same_answers_same_salt(self):
        salt = os.urandom(16)

        a = derive_recovery_credential(self.answers, salt)
        b = derive_recovery_credential(self.answers, salt)

        self.assertEqual(a, b)

    def test_different_salt_changes_credential(self):
        a = derive_recovery_credential(
            self.answers,
            os.urandom(16),
        )
        b = derive_recovery_credential(
            self.answers,
            os.urandom(16),
        )

        self.assertNotEqual(a, b)

    def test_changed_answer_changes_credential(self):
        salt = os.urandom(16)

        changed = list(self.answers)
        changed[3] = "Different Street"

        self.assertNotEqual(
            derive_recovery_credential(self.answers, salt),
            derive_recovery_credential(changed, salt),
        )

    def test_credential_length(self):
        credential = derive_recovery_credential(
            self.answers,
            os.urandom(16),
        )

        self.assertEqual(len(credential), 32)

    def test_passphrase_representation(self):
        credential = derive_recovery_credential(
            self.answers,
            os.urandom(16),
        )

        value = credential_to_passphrase(credential)

        self.assertEqual(len(value), 64)
        self.assertNotIn(self.answers[0], value)

    def test_question_set_identifier(self):
        ids = [
            "q01", "q02", "q03", "q04",
            "q05", "q06", "q07", "q08",
        ]

        a = question_set_id(ids)
        b = question_set_id(ids)

        self.assertEqual(a, b)
        self.assertEqual(len(a), 64)

    def test_duplicate_question_ids_rejected(self):
        ids = [
            "q01", "q02", "q03", "q04",
            "q05", "q06", "q07", "q07",
        ]

        with self.assertRaises(QuestionRecoveryError):
            question_set_id(ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
