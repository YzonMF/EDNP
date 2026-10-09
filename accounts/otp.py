"""Issuing and checking the six-digit codes texted to a user's phone.

Every send costs credits and every code is a way into an account, so both
directions are limited: sends are rate-limited per account and per phone
number (a stranger's number can't be spammed through us), and each code dies
after a few wrong guesses, on expiry, once used, or when a newer one replaces it.
"""

import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from . import sms
from .models import OtpChallenge


class OtpError(Exception):
    """A code could not be issued or accepted; the message is safe to show."""


def _hash(user, purpose, target, code):
    # Bound to who/what/where it was issued for, so a code can't be replayed
    # for another account, purpose or number.
    return salted_hmac('ednp.otp', f'{user.pk}:{purpose}:{target}:{code}').hexdigest()


def _generate_code():
    return ''.join(secrets.choice('0123456789') for _ in range(settings.OTP_LENGTH))


def _seconds_text(seconds):
    return f'{seconds} second{"" if seconds == 1 else "s"}'


def issue(user, purpose, target):
    """Texts a new code to `target` and returns it ONLY in test mode (so the
    page can show it); in real mode returns None and the code exists nowhere
    but the phone and a hash in the database.

    Raises OtpError if rate-limited or if the text couldn't be sent. A failed
    send leaves no trace, so it doesn't use up the user's allowance."""
    now = timezone.now()

    latest = (OtpChallenge.objects.filter(user=user, purpose=purpose).order_by('-created_at').first())
    if latest is not None:
        wait = settings.OTP_RESEND_COOLDOWN_SECONDS - int((now - latest.created_at).total_seconds())
        if wait > 0:
            raise OtpError(f'Please wait {_seconds_text(wait)} before requesting another code.')

    hour_ago = now - timedelta(hours=1)
    cap = settings.OTP_MAX_SENDS_PER_HOUR
    if (OtpChallenge.objects.filter(user=user, created_at__gte=hour_ago).count() >= cap
            or OtpChallenge.objects.filter(target=target, created_at__gte=hour_ago).count() >= cap):
        raise OtpError('Too many codes requested. Please try again in an hour.')

    code = _generate_code()
    challenge = OtpChallenge.objects.create(
        user=user, purpose=purpose, target=target,
        code_hash=_hash(user, purpose, target, code),
        expires_at=now + timedelta(seconds=settings.OTP_TTL_SECONDS),
    )
    try:
        sms.send_code(target, code)
    except sms.SmsError as exc:
        challenge.delete()
        raise OtpError(str(exc)) from exc

    # Only now that the new code is really on its way does the old one die.
    OtpChallenge.objects.filter(user=user, purpose=purpose, consumed_at__isnull=True).exclude(pk=challenge.pk).update(consumed_at=now)
    return code if sms.is_test_backend() else None


def verify(user, purpose, target, submitted):
    """Raises OtpError unless `submitted` is the live code for this
    user/purpose/number; on success the code is used up."""
    code = re.sub(r'\D', '', submitted or '')
    now = timezone.now()
    outcome = None  # decided inside the transaction, raised after it commits

    with transaction.atomic():
        challenge = (OtpChallenge.objects.select_for_update()
                     .filter(user=user, purpose=purpose, target=target, consumed_at__isnull=True)
                     .order_by('-created_at').first())
        if challenge is None or challenge.expires_at <= now:
            outcome = 'That code has expired. Request a new one.'
        elif challenge.attempts >= settings.OTP_MAX_ATTEMPTS:
            outcome = 'Too many wrong attempts. Request a new code.'
        else:
            # Counted before comparing, and committed even when the guess is
            # wrong — that is what makes the attempt limit hold.
            challenge.attempts += 1
            if constant_time_compare(_hash(user, purpose, target, code), challenge.code_hash):
                challenge.consumed_at = now
            else:
                left = settings.OTP_MAX_ATTEMPTS - challenge.attempts
                outcome = ('That code is incorrect.' if left > 0 else 'That code is incorrect. Request a new code.')
            challenge.save(update_fields=['attempts', 'consumed_at'])

    if outcome:
        raise OtpError(outcome)
