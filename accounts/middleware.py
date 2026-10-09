from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse

from . import setup
from .models import ActiveSession

SIGNED_OUT_PARAM = 'signed_out'


class SingleSessionMiddleware:
    """Sign out a session once the same account has logged in elsewhere. Only
    runs for authenticated requests, and costs one indexed lookup. A user with
    no ActiveSession row yet (signed in before this feature shipped) is
    grandfathered in by claiming the row, rather than being kicked out."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated:
            current = request.session.session_key
            active = ActiveSession.objects.filter(user_id=user.pk).values_list('session_key', flat=True).first()
            if active is None:
                ActiveSession.objects.get_or_create(user_id=user.pk, defaults={'session_key': current})
            elif active != current:
                logout(request)
                return redirect(f"{reverse('index')}?{SIGNED_OUT_PARAM}=1")
        return self.get_response(request)


class ActivationGateMiddleware:
    """Keeps a signed-in user on the setup page until first-login setup is
    done (verified phone + their own password). A gate on every request rather
    than a decorator on each view, so a page added later is covered too.

    Sits after SingleSessionMiddleware: a session that was superseded has
    already been signed out by the time we look at request.user."""

    # Reachable while setup is incomplete: the setup page itself and logout.
    ALLOWED_URL_NAMES = ('activate', 'logout')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and not self._is_allowed(request) and setup.pending_stage(user):
            return redirect('activate')
        return self.get_response(request)

    def _is_allowed(self, request):
        return request.path in {reverse(name) for name in self.ALLOWED_URL_NAMES}
