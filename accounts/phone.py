"""Philippine mobile numbers: one canonical form for storing and comparing,
so 0917 123 4567, 09171234567 and +63 917 123 4567 are recognised as the same
number (which is what makes "one number per account" actually hold)."""

import re

_PATTERNS = [
    re.compile(r'^\+639(\d{9})$'),
    re.compile(r'^639(\d{9})$'),
    re.compile(r'^09(\d{9})$'),
    re.compile(r'^9(\d{9})$'),
]


def normalize_ph_mobile(raw):
    """Returns '+639XXXXXXXXX'. Raises ValueError if it isn't a PH mobile number."""
    cleaned = re.sub(r'[\s\-().]', '', raw or '')
    for pattern in _PATTERNS:
        match = pattern.match(cleaned)
        if match:
            return '+639' + match.group(1)
    raise ValueError('Enter a valid Philippine mobile number, like 0917 123 4567.')


def mask(e164):
    """+639171234567 -> 09•••••••67: only the first two and last two digits, for
    saying which number a code went to without printing the whole thing."""
    local = '0' + e164[3:]
    return f'{local[:2]}{"•" * (len(local) - 4)}{local[-2:]}'


def display(e164):
    """+639171234567 -> 0917 123 4567, for showing a user the number THEY typed."""
    local = '0' + e164[3:]
    return f'{local[:4]} {local[4:7]} {local[7:]}'


def for_semaphore(e164):
    """Semaphore takes Philippine numbers as 639XXXXXXXXX (no plus sign)."""
    return e164.lstrip('+')
