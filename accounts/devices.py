"""Trusted ("known") devices.

A browser that has proven it controls the user's phone gets a random token in a
cookie; the database keeps only the token's hash. Signing in from a browser
that isn't trusted needs a code texted to the user's verified number.

The cookie is per-user (named after the user id) so two people sharing one
browser each keep their own trust instead of overwriting each other's.
"""

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import TrustedDevice

COOKIE_PREFIX = 'ednp_device_'


def cookie_name(user):
    return f'{COOKIE_PREFIX}{user.pk}'


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def label_for(request):
    """A short human description of the browser, so a person can recognise
    their own devices in a list ('Chrome on Windows')."""
    ua = request.META.get('HTTP_USER_AGENT', '')
    browser = next((name for needle, name in (
        ('Edg/', 'Edge'), ('OPR/', 'Opera'), ('Firefox/', 'Firefox'), ('Chrome/', 'Chrome'), ('Safari/', 'Safari'),
    ) if needle in ua), 'Browser')
    system = next((name for needle, name in (
        ('Android', 'Android'), ('iPhone', 'iPhone'), ('iPad', 'iPad'), ('Windows', 'Windows'),
        ('Mac OS X', 'Mac'), ('Linux', 'Linux'),
    ) if needle in ua), 'unknown device')
    return f'{browser} on {system}'


def is_trusted(request, user):
    """True if this browser holds a live trusted-device token for `user`.
    Using it renews it, so a device in regular use stays trusted."""
    token = request.COOKIES.get(cookie_name(user))
    if not token:
        return False
    now = timezone.now()
    device = TrustedDevice.objects.filter(user=user, token_hash=_hash(token), expires_at__gt=now).first()
    if device is None:
        return False
    device.last_seen_at = now
    device.expires_at = now + timedelta(days=settings.TRUSTED_DEVICE_DAYS)
    device.save(update_fields=['last_seen_at', 'expires_at'])
    return True


def trust(response, request, user):
    """Marks the current browser as trusted by setting its cookie on `response`."""
    token = secrets.token_urlsafe(32)
    lifetime = timedelta(days=settings.TRUSTED_DEVICE_DAYS)
    TrustedDevice.objects.create(
        user=user, token_hash=_hash(token), label=label_for(request),
        expires_at=timezone.now() + lifetime,
    )
    response.set_cookie(
        cookie_name(user), token, max_age=int(lifetime.total_seconds()),
        httponly=True, secure=not settings.DEBUG, samesite='Lax',
    )


def current_device(request, user):
    """The TrustedDevice row for this browser, if it has one (for marking
    'this device' in a list)."""
    token = request.COOKIES.get(cookie_name(user))
    if not token:
        return None
    return TrustedDevice.objects.filter(user=user, token_hash=_hash(token)).first()
