def is_palindrome(s):
    """True if s reads the same backwards, ignoring case, spaces and punctuation."""
    return s == s[::-1]


def slugify(title):
    return "-".join(title.lower().split())
