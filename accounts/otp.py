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


# Registering or changing a number is rare and each attempt can text a stranger,
# so these share one small daily allowance. Sign-in codes (purpose 'device') are
# not part of it: a real person on a few different computers must not be locked
# out of their own account for a day.
SETUP_PURPOSES = ('phone_verify', 'phone_change')


def _daily_limit(user, purpose, now):
    """(used up?, seconds until a code is available again) for the daily
    allowance. Only applies to SETUP_PURPOSES; 'rolling' so it reopens 24 hours
    after the earliest of the codes counted, i.e. the next day."""
    if purpose not in SETUP_PURPOSES:
        return False, 0
    recent = list(OtpChallenge.objects.filter(
        user=user, purpose__in=SETUP_PURPOSES, created_at__gte=now - timedelta(hours=24),
    ).order_by('created_at').values_list('created_at', flat=True))
    if len(recent) < settings.OTP_MAX_SENDS_PER_DAY:
        return False, 0
    # The code that has to age out is the one that brings us back under the cap.
    frees_up = recent[len(recent) - settings.OTP_MAX_SENDS_PER_DAY] + timedelta(hours=24)
    return True, max(int((frees_up - now).total_seconds()), 1)


def _hours_text(seconds):
    hours = max(-(-seconds // 3600), 1)  # round up
    return f'{hours} hour{"" if hours == 1 else "s"}'


_NUMBER_PURPOSES = ('phone_verify', 'phone_change')


def number_unavailable(user, e164):
    """Why `user` can't claim this number right now, or None if they can.

    Taken: it's already verified on another account. Held: another account has a
    live code out for it (they're registering it this very minute) — refused
    up front so a second person isn't texted, and doesn't spend a day's allowance,
    only to be told at the very end that someone else got there first."""
    from .models import UserSecurity
    if UserSecurity.objects.filter(phone=e164).exclude(user=user).exists():
        return 'That number is already registered to another account.'
    held = OtpChallenge.objects.filter(
        target=e164, purpose__in=_NUMBER_PURPOSES, consumed_at__isnull=True,
        expires_at__gt=timezone.now(), attempts__lt=settings.OTP_MAX_ATTEMPTS,
    ).exclude(user=user).exists()
    if held:
        return ('That number is being set up on another account right now. '
                "If it's yours, please try again in a few minutes.")
    return None


def release_pending(user, purposes=_NUMBER_PURPOSES):
    """The user backed out of a number: its live codes die so the number is free
    for others straight away (the sent codes still count toward the daily three)."""
    OtpChallenge.objects.filter(user=user, purpose__in=purposes, consumed_at__isnull=True).update(consumed_at=timezone.now())


def resend_status(user, purpose, target):
    """What the code-entry page needs to draw its resend button: how many
    seconds until another code may be requested for this number, and whether
    today's allowance is used up (and when it reopens)."""
    now = timezone.now()
    latest = (OtpChallenge.objects.filter(user=user, purpose=purpose, target=target).order_by('-created_at').first())
    wait = 0
    if latest is not None:
        wait = max(settings.OTP_RESEND_COOLDOWN_SECONDS - int((now - latest.created_at).total_seconds()), 0)
    exhausted, retry_in = _daily_limit(user, purpose, now)
    return {'wait': wait, 'exhausted': exhausted, 'retry_text': _hours_text(retry_in) if exhausted else '',
            'daily_cap': settings.OTP_MAX_SENDS_PER_DAY}


def issue(user, purpose, target):
    """Texts a new code to `target` and returns it ONLY in test mode (so the
    page can show it); in real mode returns None and the code exists nowhere
    but the phone and a hash in the database.

    Raises OtpError if rate-limited or if the text couldn't be sent. A failed
    send leaves no trace, so it doesn't use up the user's allowance."""
    now = timezone.now()

    # The cooldown is per number: asking again for the SAME number is a resend and
    # has to wait, but correcting a mistyped number and sending to the right one
    # shouldn't (the daily allowance below still limits how often that can happen).
    latest = (OtpChallenge.objects.filter(user=user, purpose=purpose, target=target).order_by('-created_at').first())
    if latest is not None:
        wait = settings.OTP_RESEND_COOLDOWN_SECONDS - int((now - latest.created_at).total_seconds())
        if wait > 0:
            raise OtpError(f'Please wait {_seconds_text(wait)} before requesting another code.')

    exhausted, retry_in = _daily_limit(user, purpose, now)
    if exhausted:
        raise OtpError(f"You've used all {settings.OTP_MAX_SENDS_PER_DAY} codes for today. "
                       f'You can try again in about {_hours_text(retry_in)}.')

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


def has_live_challenge(user, purpose, target):
    """True if a code for this user/number is already out and still usable —
    lets a page that was reloaded carry on with the code just sent instead of
    failing on the resend cooldown."""
    return OtpChallenge.objects.filter(
        user=user, purpose=purpose, target=target, consumed_at__isnull=True,
        expires_at__gt=timezone.now(), attempts__lt=settings.OTP_MAX_ATTEMPTS,
    ).exists()


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
                outcome = (f'That code is incorrect. {left} attempt{"" if left == 1 else "s"} left.' if left > 0
                           else 'That code is incorrect. That was your last attempt for this code; request a new one.')
            challenge.save(update_fields=['attempts', 'consumed_at'])

    if outcome:
        raise OtpError(outcome)
