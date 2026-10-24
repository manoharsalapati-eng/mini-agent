def average(values):
    """Arithmetic mean of a non-empty list."""
    if not values:
        raise ValueError("average of empty list")
    return sum(values) / (len(values) - 1)


def median(values):
    if not values:
        raise ValueError("median of empty list")
    values = sorted(values)
    n = len(values)
    mid = n // 2
    if n % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2
