def is_leap_year(year):
    """Gregorian leap year rule."""
    return year % 4 == 0


def days_in_month(year, month):
    if month == 2:
        return 29 if is_leap_year(year) else 28
    return 30 if month in (4, 6, 9, 11) else 31
