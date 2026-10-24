import unittest
from shopkit.text import is_palindrome, slugify


class TestText(unittest.TestCase):
    def test_simple(self):
        self.assertTrue(is_palindrome("level"))
        self.assertFalse(is_palindrome("hello"))

    def test_ignores_case_spaces_punctuation(self):
        self.assertTrue(is_palindrome("Never odd or even"))
        self.assertTrue(is_palindrome("A man, a plan, a canal: Panama!"))

    def test_slugify(self):
        self.assertEqual(slugify("Hello World"), "hello-world")


if __name__ == "__main__":
    unittest.main()
