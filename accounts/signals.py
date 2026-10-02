from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from .models import ActiveSession


@receiver(user_logged_in)
def claim_active_session(sender, request, user, **kwargs):
    """Every login path (portal and /admin) makes this session the user's only
    active one. The superseded session is deliberately not deleted here: the
    middleware ends it (and tells that device why) the next time it shows up,
    and `clearsessions` sweeps any that never come back."""
    if request is None:
        return
    ActiveSession.objects.update_or_create(user=user, defaults={'session_key': request.session.session_key})
