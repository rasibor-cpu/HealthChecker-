import unittest

from backend.health_vault.recovery_question_bank import (
    QUESTION_BANK,
    QUESTION_BANK_VERSION,
    REQUIRED_SELECTION_COUNT,
    categories,
    question_by_id,
    question_count,
    questions_for_category,
)


class HC311F8DRecoveryQuestionBankTests(unittest.TestCase):

    def test_version(self):
        self.assertEqual(QUESTION_BANK_VERSION, 1)

    def test_exact_question_count(self):
        self.assertEqual(question_count(), 60)

    def test_ten_categories(self):
        self.assertEqual(len(categories()), 10)

    def test_six_questions_per_category(self):
        for category in categories():
            self.assertEqual(
                len(questions_for_category(category)),
                6,
            )

    def test_question_ids_unique(self):
        ids = [q.question_id for q in QUESTION_BANK]
        self.assertEqual(len(ids), len(set(ids)))

    def test_prompts_unique(self):
        prompts = [q.prompt for q in QUESTION_BANK]
        self.assertEqual(len(prompts), len(set(prompts)))

    def test_no_empty_prompts(self):
        for q in QUESTION_BANK:
            self.assertTrue(q.prompt.strip())

    def test_no_empty_categories(self):
        for q in QUESTION_BANK:
            self.assertTrue(q.category.strip())

    def test_lookup(self):
        q = question_by_id("WK01")
        self.assertEqual(q.category, "Work & Career")

    def test_required_selection_count(self):
        self.assertEqual(REQUIRED_SELECTION_COUNT, 10)

    def test_catalog_contains_no_user_answers(self):
        for q in QUESTION_BANK:
            self.assertFalse(
                hasattr(q, "answer")
            )

    def test_catalog_contains_no_crypto_secret(self):
        for q in QUESTION_BANK:
            self.assertFalse(
                hasattr(q, "key")
            )
            self.assertFalse(
                hasattr(q, "passphrase")
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
