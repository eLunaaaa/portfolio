import unittest

from app.services.password_policy import validate_password_policy


class PasswordPolicyTests(unittest.TestCase):
    def test_valid_password(self):
        validate_password_policy("ValidPassword123!")

    def test_missing_requirements(self):
        for value in ("Short1!", "a" * 129 + "A1!", "lowercase123!",
                      "UPPERCASE123!", "NoNumbersHere!", "NoSpecial1234",
                      "NoSpecial123 "):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_password_policy(value)
