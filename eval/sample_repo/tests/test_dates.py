import unittest
from shopkit.dates import is_leap_year, days_in_month


class TestDates(unittest.TestCase):
    def test_leap(self):
        self.assertTrue(is_leap_year(2024))
        self.assertFalse(is_leap_year(2023))

    def test_century_rule(self):
        self.assertFalse(is_leap_year(1900))
        self.assertTrue(is_leap_year(2000))

    def test_feb(self):
        self.assertEqual(days_in_month(2100, 2), 28)


if __name__ == "__main__":
    unittest.main()
