"""Sign-in lockout: after MAX_ATTEMPTS wrong passwords for one account, further
attempts are refused until LOCKOUT_SECONDS have passed since the last one.

The counters are rows in the database (accounts.LoginThrottle), not entries in
Django's cache. That cache is per-process, and the site runs several gunicorn
workers, so each worker kept its own count and every restart wiped them all. A row
is shared by every worker and survives a restart, and the count goes up in one
atomic UPDATE, so guesses made at the same moment can't overwrite each other.

What is counted is the *account*, not the text that was typed. MySQL compares
usernames ignoring case and accents, so 'CLG-001' and 'ĆLG-001' sign in as the same
person, and a counter keyed on the typed text gave every spelling its own five
tries. A name that matches no account is counted too, under a normalised spelling,
so the lockout looks the same for names that exist and names that don't and can't
be used to find out which usernames are real.

Only a keyed hash of what is counted is stored, so the table is not a list of the
usernames people have typed (some people type their password there).
"""

import logging
import random
import time
import unicodedata
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import DatabaseError, IntegrityError, OperationalError, connection, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.crypto import salted_hmac

from accounts.models import LoginThrottle

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60  # 15 minutes

LOCKOUT_MESSAGE = 'Too many failed attempts for this account. Please wait 15 minutes and try again.'

# A username is at most 150 characters, so anything longer can't name an account; don't hash megabytes.
_MAX_TYPED_LENGTH = 255

# Counts nobody has added to for this long are worthless and get swept away (see _tidy_up).
_SWEEP_AFTER_SECONDS = 2 * LOCKOUT_SECONDS
_SWEEP_EVERY_SECONDS = 5 * 60
_SWEEP_BATCH = 1000
_SWEEP_MAX_BATCHES = 10
_SWEEP_GATE = 'login_throttle:swept'

# MySQL error numbers for "deadlock found" and "lock wait timeout": it picks one of two
# statements that collide on locks, rolls it back and says to try again.
_RETRYABLE = (1213, 1205)
_ATTEMPTS = 3


def _normalise(text):
    """Lower-case and accent-free, so look-alike spellings of a name that matches
    no account share one counter."""
    decomposed = unicodedata.normalize('NFKD', text)
    return ''.join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def _resolve(username):
    """(hash of what to count, the real username of the account it refers to or None).

    Asks the database the same question the sign-in asks, so it lands on exactly the
    account the sign-in would have used, however the name was spelled."""
    typed = (username or '').strip()[:_MAX_TYPED_LENGTH]
    found = get_user_model().objects.filter(username=typed).values_list('pk', 'username').first()
    key = f'account:{found[0]}' if found else f'name:{_normalise(typed)}'
    return salted_hmac('ednp.login-throttle', key, algorithm='sha256').hexdigest(), (found[1] if found else None)


def _window_start(now=None):
    return (now or timezone.now()) - timedelta(seconds=LOCKOUT_SECONDS)


def is_locked_out(username):
    if not (username or '').strip():
        return False
    key_hash, _ = _resolve(username)
    return LoginThrottle.objects.filter(
        key_hash=key_hash, failures__gte=MAX_ATTEMPTS, updated_at__gt=_window_start(),
    ).exists()


def register_failure(username):
    """Counts one wrong password. Returns (the account's real username, or None if
    the name matches no account; how many failures are now on record)."""
    if not (username or '').strip():
        return None, 0
    key_hash, account = _resolve(username)
    now = timezone.now()
    _retrying(lambda: _count(key_hash, now))
    _tidy_up(now)
    failures = LoginThrottle.objects.filter(key_hash=key_hash).values_list('failures', flat=True).first() or 0
    return account, failures


def _count(key_hash, now):
    row = LoginThrottle.objects.filter(key_hash=key_hash)
    # Each step is a single atomic UPDATE, so simultaneous guesses never lose a count.
    # A count whose window has run out starts again at 1; a live one goes up by 1.
    if row.filter(updated_at__lte=_window_start(now)).update(failures=1, updated_at=now):
        return
    if row.update(failures=F('failures') + 1, updated_at=now):
        return
    try:
        with transaction.atomic():
            LoginThrottle.objects.create(key_hash=key_hash, failures=1, updated_at=now)
    except IntegrityError:
        # A request for the same name created the row a moment ago; count this one on top of it.
        row.update(failures=F('failures') + 1, updated_at=now)


def _retrying(step):
    """Runs `step`, running it again if MySQL abandoned it as a deadlock victim.
    Every step here is a single statement (or one tiny transaction) that is safe to repeat.
    Only outside a larger transaction: inside one, MySQL has already rolled all of it
    back and carrying on would be wrong, so the error is passed on."""
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            return step()
        except OperationalError as exc:
            retryable = bool(exc.args) and exc.args[0] in _RETRYABLE and not connection.in_atomic_block
            if not retryable or attempt == _ATTEMPTS:
                raise
            time.sleep(random.uniform(0.005, 0.03))   # don't collide the same way again


def clear(username):
    if not (username or '').strip():
        return
    key_hash, _ = _resolve(username)
    LoginThrottle.objects.filter(key_hash=key_hash).delete()


def _tidy_up(now):
    """Housekeeping, so the table doesn't grow without end. It never gets in the way of a
    sign-in: it runs at most every few minutes per process, and if it fails that is only logged."""
    if not cache.add(_SWEEP_GATE, 1, _SWEEP_EVERY_SECONDS):
        return
    try:
        _sweep(now)
    except DatabaseError:
        log.warning('Could not clear old sign-in counters', exc_info=True)


def _sweep(now):
    """Deletes counts nobody has added to for a long time. By primary key, in batches: a range
    DELETE on updated_at takes its locks in the opposite order to the UPDATEs in _count (index
    first, row second, against row first, index second), and MySQL deadlocked the two."""
    stale_before = now - timedelta(seconds=_SWEEP_AFTER_SECONDS)
    for _ in range(_SWEEP_MAX_BATCHES):
        stale = list(LoginThrottle.objects.filter(updated_at__lte=stale_before).values_list('pk', flat=True)[:_SWEEP_BATCH])
        if not stale:
            return
        LoginThrottle.objects.filter(pk__in=stale).delete()
