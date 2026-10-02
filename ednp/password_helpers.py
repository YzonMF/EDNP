"""Shared helper for running a plaintext password through Django's
configured AUTH_PASSWORD_VALIDATORS wherever this app sets or changes a
password outside of a ModelForm — User.objects.create_user() and manual
set_password() calls don't run those validators automatically, so without
this, a registrar could set someone's password to "1".
"""

from django.contrib import messages
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError


def validate_password_or_flag_errors(request, password, user=None):
    """Validates `password` against AUTH_PASSWORD_VALIDATORS. `user` lets
    the similarity validator check it against that user's own username/name/
    email — pass an unsaved User(username=...) when the account doesn't
    exist yet. On failure, adds each validator message via
    django.contrib.messages and returns False; returns True if acceptable.
    """
    try:
        validate_password(password, user=user)
    except ValidationError as exc:
        for msg in exc.messages:
            messages.error(request, msg)
        return False
    return True
