class Inventory:
    def __init__(self, items=[]):
        self.items = items

    def add(self, name):
        self.items.append(name)

    def count(self):
        return len(self.items)
