"""Helpers for tests that need a user who has already finished first-login
setup (verified phone + own password), so the activation gate lets them
through to the page actually under test."""

from django.utils import timezone

from .models import UserSecurity


def activate(user, phone=None):
    """Marks `user` as fully set up. Each user gets a distinct number (the
    column is unique) unless one is given."""
    now = timezone.now()
    if not user.get_full_name().strip() and not hasattr(user, 'clergy_profile'):
        user.first_name = 'Test User'   # staff must have a name to count as set up
        user.save(update_fields=['first_name'])
    UserSecurity.objects.update_or_create(user=user, defaults={
        'phone': phone or f'+639{user.pk:09d}',
        'phone_verified_at': now,
        'phone_verified_via': 'test',
        'password_changed_at': now,
        'activated_at': now,
        'setup_expires_at': None,
    })
    return user


def trust_device(client, user):
    """Gives `client` a live trusted-device cookie for `user`, so a sign-in
    from it goes straight through instead of asking for a texted code."""
    import secrets
    from datetime import timedelta

    from . import devices
    from .models import TrustedDevice

    token = secrets.token_urlsafe(16)
    TrustedDevice.objects.create(
        user=user, token_hash=devices._hash(token), label='Test browser',
        expires_at=timezone.now() + timedelta(days=30),
    )
    client.cookies[devices.cookie_name(user)] = token
    return client
