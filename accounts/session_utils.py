from django.contrib.auth import update_session_auth_hash

from .models import ActiveSession


def keep_signed_in_after_password_change(request, user):
    """Call right after set_password() on the signed-in user.

    update_session_auth_hash keeps the session valid after a password change
    but also rotates its key — and the single-session rule treats a session
    whose key isn't the recorded one as "signed in elsewhere" and signs it out.
    So the recorded key has to follow the rotation, or changing your password
    logs you out of the very device you changed it on."""
    update_session_auth_hash(request, user)
    ActiveSession.objects.update_or_create(user=user, defaults={'session_key': request.session.session_key})
