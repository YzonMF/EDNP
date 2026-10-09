"""Helpers for tests that need a user who has already finished first-login
setup (verified phone + own password), so the activation gate lets them
through to the page actually under test."""

from django.utils import timezone

from .models import UserSecurity


def activate(user, phone=None):
    """Marks `user` as fully set up. Each user gets a distinct number (the
    column is unique) unless one is given."""
    now = timezone.now()
    UserSecurity.objects.update_or_create(user=user, defaults={
        'phone': phone or f'+639{user.pk:09d}',
        'phone_verified_at': now,
        'phone_verified_via': 'test',
        'password_changed_at': now,
        'activated_at': now,
        'setup_expires_at': None,
    })
    return user
