"""Simple per-username login lockout, backed by Django's cache framework
(no extra service required — the default LocMemCache is enough for a
single-process deployment). Keyed by username rather than IP, since the
goal is to stop any one account being brute-forced, regardless of where the
attempts come from.
"""

from django.core.cache import cache

MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60  # 15 minutes

LOCKOUT_MESSAGE = 'Too many failed attempts for this account. Please wait 15 minutes and try again.'


def _key(username):
    return f'login_attempts:{username.strip().lower()}'


def is_locked_out(username):
    if not username:
        return False
    return cache.get(_key(username), 0) >= MAX_ATTEMPTS


def register_failure(username):
    if not username:
        return
    key = _key(username)
    attempts = cache.get(key, 0) + 1
    cache.set(key, attempts, LOCKOUT_SECONDS)


def clear(username):
    if not username:
        return
    cache.delete(_key(username))
