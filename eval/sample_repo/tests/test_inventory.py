import unittest
from shopkit.inventory import Inventory


class TestInventory(unittest.TestCase):
    def test_instances_are_independent(self):
        a = Inventory()
        a.add("rice")
        b = Inventory()
        self.assertEqual(b.count(), 0)

    def test_given_items(self):
        self.assertEqual(Inventory(["x", "y"]).count(), 2)


if __name__ == "__main__":
    unittest.main()
