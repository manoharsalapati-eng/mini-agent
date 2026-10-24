import unittest
from shopkit.stats import average, median


class TestStats(unittest.TestCase):
    def test_average(self):
        self.assertEqual(average([2, 4, 6]), 4)
        self.assertEqual(average([5]), 5)

    def test_average_empty(self):
        with self.assertRaises(ValueError):
            average([])

    def test_median(self):
        self.assertEqual(median([3, 1, 2]), 2)


if __name__ == "__main__":
    unittest.main()
