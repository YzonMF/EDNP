"""Forgot password: who may reset by text message, and how often it can be asked.

The page must never reveal whether a number belongs to an account, so every rule
that decides what the visitor SEES is counted per number the same way for numbers
that exist and numbers that don't. That is why the limits live in their own table
(ResetRequest, one row per request, keyed by a hash of the number) instead of on
OtpChallenge, which only exists for real accounts."""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import ResetRequest, UserSecurity
from .otp import _hours_text, _seconds_text


class ForgotLimit(Exception):
    """Too many requests; the message is the same whether or not the number is registered."""


def _hash(e164):
    # Hashed so this table is not a list of the phone numbers people have typed.
    return salted_hmac('ednp.forgot', e164).hexdigest()


def _state(e164, now):
    """(seconds to wait, daily allowance used up?, seconds until it reopens)."""
    mine = list(ResetRequest.objects.filter(
        number_hash=_hash(e164), created_at__gte=now - timedelta(hours=24),
    ).order_by('created_at').values_list('created_at', flat=True))
    wait = 0
    if mine:
        wait = max(settings.OTP_RESEND_COOLDOWN_SECONDS - int((now - mine[-1]).total_seconds()), 0)
    cap = settings.FORGOT_MAX_SENDS_PER_DAY
    if len(mine) < cap:
        return wait, False, 0
    frees_up = mine[len(mine) - cap] + timedelta(hours=24)
    return wait, True, max(int((frees_up - now).total_seconds()), 1)


def status(e164):
    """Same shape as otp.resend_status, so the shared code field can draw it."""
    wait, exhausted, retry_in = _state(e164, timezone.now()) if e164 else (0, False, 0)
    return {'wait': wait, 'exhausted': exhausted, 'retry_text': _hours_text(retry_in) if exhausted else '',
            'daily_cap': settings.FORGOT_MAX_SENDS_PER_DAY}


def record_request(e164):
    """Counts one request for `e164`, or raises ForgotLimit. Call this before
    looking the number up, so a limit can't be told apart from a real account."""
    now = timezone.now()
    ResetRequest.objects.filter(created_at__lt=now - timedelta(days=2)).delete()

    wait, exhausted, retry_in = _state(e164, now)
    if wait > 0:
        raise ForgotLimit(f'Please wait {_seconds_text(wait)} before requesting another code.')
    if exhausted:
        raise ForgotLimit(f"You've used all {settings.FORGOT_MAX_SENDS_PER_DAY} codes for today. "
                          f'You can try again in about {_hours_text(retry_in)}.')
    # A ceiling on the whole site, so that typing many numbers can't be used to
    # burn through SMS credits or to text strangers.
    if ResetRequest.objects.filter(created_at__gte=now - timedelta(hours=1)).count() >= settings.FORGOT_MAX_SENDS_PER_HOUR_SITEWIDE:
        raise ForgotLimit('Too many password resets are being requested right now. Please try again in a little while.')
    ResetRequest.objects.create(number_hash=_hash(e164))


def eligible_user(e164):
    """The account that may reset its password through this number, or None.

    Only an activated account whose number was verified for real by the owner:
    an account still on its registrar-issued password is handled by the
    registrar's reprint, and a number 'verified' in test mode proves nothing."""
    security = (UserSecurity.objects.select_related('user')
                .filter(phone=e164, user__is_active=True, activated_at__isnull=False, phone_verified_at__isnull=False)
                .first())
    if security is None:
        return None
    if settings.SMS_BACKEND != 'test' and security.phone_verified_via != 'sms':
        return None
    return security.user
