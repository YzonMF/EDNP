"""Creating portal accounts with registrar-issued credentials.

Every account the registrar makes — one at a time or from a bulk import —
goes through here, so they all follow the same rules: the username is the
clergy record ID, the password is random and never chosen by a person, and the
password only works for SETUP_WINDOW_DAYS unless the clergy member activates
the account (registers their phone number and picks their own password).
"""

import secrets
from datetime import timedelta

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

from accounts.models import ActiveSession, OtpChallenge, TrustedDevice, UserSecurity
from clergy.models import ClergyProfile

SETUP_WINDOW_DAYS = 30

# No 0/O, 1/l/I — the password is read off a printed slip and typed by hand.
_PASSWORD_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789'
_PASSWORD_GROUP = 5


class ProvisionError(Exception):
    """An account couldn't be created; the message is safe to show the registrar."""


def generate_password():
    """Two groups of five, e.g. 'k7Pmx-9QeRt' — easier to read aloud and copy
    than one run of ten. Retries until it has at least one digit and one
    letter (the numeric-only validator would reject an all-digit one) and
    passes the project's configured password validators."""
    for _ in range(20):
        groups = [''.join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(_PASSWORD_GROUP)) for _ in range(2)]
        password = '-'.join(groups)
        if not any(c.isdigit() for c in password) or not any(c.isalpha() for c in password):
            continue
        try:
            validate_password(password)
        except ValidationError:
            continue
        return password
    raise ProvisionError('Could not generate a valid temporary password. Please try again.')


def _expiry_from_now():
    return timezone.now() + timedelta(days=SETUP_WINDOW_DAYS)


def provision_account(record):
    """Creates the login for `record` and returns (user, plaintext_password).

    The plaintext exists only in the return value — only its hash is stored —
    so the caller has to put it on the printed slip in the same request.
    Must be called inside transaction.atomic() by the caller, so a failure
    later in the batch undoes this too."""
    username = record.id
    if User.objects.filter(username__iexact=username).exists():
        raise ProvisionError(f'The username {username} is already in use by another account.')

    password = generate_password()
    try:
        user = User.objects.create_user(username=username, password=password)
    except IntegrityError:
        raise ProvisionError(f'The username {username} is already in use by another account.')

    # Every profile field starts blank; the clergy member fills in their own
    # details after activation.
    ClergyProfile.objects.create(user=user, record=record, verification='For Verification')
    UserSecurity.objects.create(user=user, setup_expires_at=_expiry_from_now())
    return user, password


def reset_credentials(user):
    """New temporary password and a fresh 30 days, for a slip that was lost or
    an account that expired before it was activated. Refuses an activated
    account: that user chose their own password, and replacing it is the
    forgot-password flow's job, not a registrar reprint."""
    security, _ = UserSecurity.objects.get_or_create(user=user)
    if security.is_activated:
        raise ProvisionError('This account is already activated. The clergy member should use Forgot Password instead.')

    password = generate_password()
    user.set_password(password)
    user.save(update_fields=['password'])
    security.setup_expires_at = _expiry_from_now()
    security.save(update_fields=['setup_expires_at'])
    user.security = security  # so the caller reads the new deadline, not a cached old one
    return password


def reset_lost_phone(user):
    """Puts an account back to a brand-new state after the owner has been
    identified in person: new temporary password, number forgotten, every
    trusted device and pending code revoked, every open session signed out.

    The password changes in the same step as the number is cleared — clearing
    the number alone would leave the old password working, and whoever knows
    it could sign in first and register their own number. Returns the new
    plaintext password for the slip. Call inside transaction.atomic()."""
    security, _ = UserSecurity.objects.get_or_create(user=user)
    password = generate_password()
    user.set_password(password)
    user.save(update_fields=['password'])

    security.phone = None  # frees the number: it may now belong to someone else
    security.phone_verified_at = None
    security.phone_verified_via = ''
    security.password_changed_at = None
    security.activated_at = None
    security.setup_expires_at = _expiry_from_now()
    security.save()

    TrustedDevice.objects.filter(user=user).delete()
    OtpChallenge.objects.filter(user=user).delete()
    # Open sessions already die with the old password (Django ties each session to
    # the password hash); this also stops any of them being re-claimed through the
    # single-session rule, since no real session has the empty key.
    ActiveSession.objects.update_or_create(user=user, defaults={'session_key': ''})

    profile = getattr(user, 'clergy_profile', None)
    if profile is not None and profile.record is not None:
        profile.record.phone = None
        profile.record.save(update_fields=['phone'])
    user.security = security
    return password


def account_status(security):
    """('activated' | 'pending' | 'expired', whole days left or None) for the
    registrar's Portal Account column. `security` may be None for an account
    that predates this feature; it counts as pending with no deadline."""
    if security is not None and security.is_activated:
        return 'activated', None
    if security is None or security.setup_expires_at is None:
        return 'pending', None
    remaining = security.setup_expires_at - timezone.now()
    if remaining.total_seconds() <= 0:
        return 'expired', None
    return 'pending', max(remaining.days, 0)
