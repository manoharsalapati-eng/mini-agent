import unittest
from shopkit.cart import Cart


class TestCart(unittest.TestCase):
    def setUp(self):
        self.c = Cart()
        self.c.add("tea", 120.0, 2)
        self.c.add("biscuits", 30.0, 2)

    def test_subtotal(self):
        self.assertEqual(self.c.subtotal(), 300.0)

    def test_no_discount(self):
        self.assertEqual(self.c.total(), 300.0)

    def test_ten_percent(self):
        self.assertEqual(self.c.total(10), 270.0)


if __name__ == "__main__":
    unittest.main()
