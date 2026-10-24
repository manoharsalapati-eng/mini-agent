class Cart:
    def __init__(self):
        self.items = []  # (name, unit_price, qty)

    def add(self, name, unit_price, qty=1):
        if qty <= 0:
            raise ValueError("qty must be positive")
        self.items.append((name, unit_price, qty))

    def subtotal(self):
        return sum(price * qty for _, price, qty in self.items)

    def total(self, discount_percent=0):
        """Subtotal after a percentage discount, e.g. discount_percent=10 -> 10% off. Rounded to 2 dp."""
        return round(self.subtotal() * (1 - discount_percent), 2)
