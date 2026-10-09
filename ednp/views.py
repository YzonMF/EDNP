from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.shortcuts import redirect, render
from django.utils import timezone

from accounts.middleware import SIGNED_OUT_PARAM

from . import login_throttle

LAST_USERNAME_COOKIE = 'last_username'
LAST_USERNAME_MAX_AGE = 60 * 60 * 24 * 90  # 90 days


def _home_url_for(user):
    return 'registrar:dashboard' if user.is_staff else 'clergy:dashboard'


def _setup_window_closed(user):
    """True for an account that was never activated and whose registrar-issued
    temporary password is past its deadline. Accounts with no security row
    (created before this feature, or via createsuperuser) have no deadline."""
    security = getattr(user, 'security', None)
    if security is None or security.is_activated or security.setup_expires_at is None:
        return False
    return security.setup_expires_at <= timezone.now()


def index(request):
    if request.user.is_authenticated:
        return redirect(_home_url_for(request.user))

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')

        if login_throttle.is_locked_out(username):
            return render(request, 'index.html', {
                'login_error': True,
                'login_error_message': login_throttle.LOCKOUT_MESSAGE,
                'username': username,
            })

        user = authenticate(request, username=username, password=password)
        if user is not None and _setup_window_closed(user):
            # Right password, but the registrar-issued one has run out. Not a
            # failed attempt, so it doesn't count toward the lockout.
            return render(request, 'index.html', {
                'login_error': True,
                'login_error_message': 'This temporary password has expired. Please ask the registrar to reset your account.',
                'username': username,
            })
        if user is not None:
            login_throttle.clear(username)
            auth_login(request, user)
            response = redirect(_home_url_for(user))
            response.set_cookie(LAST_USERNAME_COOKIE, username, max_age=LAST_USERNAME_MAX_AGE)
            return response

        login_throttle.register_failure(username)
        return render(request, 'index.html', {
            'login_error': True,
            'login_error_message': 'Invalid username or password.',
            'username': username,
        })

    context = {'username': request.COOKIES.get(LAST_USERNAME_COOKIE, '')}
    if request.GET.get(SIGNED_OUT_PARAM):
        context['login_error'] = True
        context['login_error_message'] = 'You were signed out because your account was used to sign in on another device.'
    return render(request, 'index.html', context)


def logout_view(request):
    auth_logout(request)
    return redirect('index')
