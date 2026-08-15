import os
import unittest

from tools.hc311_private_answer_enrollment import (
    PrivateEnrollmentError,
    derive_confirmed_passphrase,
)


class QR4PrivateEnrollmentTests(unittest.TestCase):

    def setUp(self):
        self.answers = [
            "Airport Road",
            "Primary School",
            "Michael",
            "Benin",
            "Smith",
            "Lagos",
            "Blue",
            "Abuja",
            "Youth Club",
            "Oakville",
        ]

    def test_matching_answers_derive_passphrase(self):
        result = derive_confirmed_passphrase(
            self.answers,
            self.answers,
            os.urandom(16),
        )

        self.assertEqual(len(result), 64)

    def test_wrong_confirmation_fails(self):
        changed = list(self.answers)
        changed[4] = "Wrong"

        with self.assertRaises(
            PrivateEnrollmentError
        ):
            derive_confirmed_passphrase(
                self.answers,
                changed,
                os.urandom(16),
            )

    def test_case_and_whitespace_normalize(self):
        confirm = [
            " airport   road ",
            "PRIMARY SCHOOL",
            "MICHAEL",
            "BENIN",
            "SMITH",
            "LAGOS",
            "BLUE",
            "ABUJA",
            "YOUTH CLUB",
            "OAKVILLE",
        ]

        result = derive_confirmed_passphrase(
            self.answers,
            confirm,
            os.urandom(16),
        )

        self.assertEqual(len(result), 64)

    def test_wrong_answer_count_fails(self):
        with self.assertRaises(
            PrivateEnrollmentError
        ):
            derive_confirmed_passphrase(
                self.answers[:9],
                self.answers[:9],
                os.urandom(16),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)