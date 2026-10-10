from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login as auth_login, logout as auth_logout
from django.shortcuts import redirect, render
from django.utils import timezone

from accounts import devices, otp, setup, sms
from accounts.middleware import SIGNED_OUT_PARAM
from accounts.models import UserSecurity
from accounts.phone import mask
from clergy import notifications

from . import login_throttle, security_log

LAST_USERNAME_COOKIE = 'last_username'
LAST_USERNAME_MAX_AGE = 60 * 60 * 24 * 90  # 90 days

# Between a correct password and a correct code, the person is NOT signed in:
# only this marker (who, and when) sits in their session.
PENDING_LOGIN_KEY = 'pending_login'
PENDING_LOGIN_MAX_AGE_SECONDS = 10 * 60
DEVICE_TEST_CODE_KEY = 'device_test_code'
MODEL_BACKEND = 'django.contrib.auth.backends.ModelBackend'


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


def _signed_in_response(user, username):
    response = redirect(_home_url_for(user))
    response.set_cookie(LAST_USERNAME_COOKIE, username, max_age=LAST_USERNAME_MAX_AGE)
    return response


def _start_device_check(request, user, username):
    """Texts a code to the user's verified number and sends them to enter it.
    If a code is already out (they reloaded or went back), reuse it rather than
    tripping the resend cooldown."""
    security = UserSecurity.objects.get(user=user)
    try:
        test_code = otp.issue(user, 'device', security.phone)
    except otp.OtpError as exc:
        if not otp.has_live_challenge(user, 'device', security.phone):
            return render(request, 'index.html', {
                'login_error': True, 'login_error_message': str(exc), 'username': username,
            })
        test_code = None
    request.session[PENDING_LOGIN_KEY] = {'user_id': user.pk, 'username': username, 'at': timezone.now().timestamp()}
    if test_code:
        request.session[DEVICE_TEST_CODE_KEY] = test_code
    return redirect('verify_device')


def _pending_user(request):
    """The user mid-way through a new-device check, or None if there isn't a
    fresh one (missing, expired, or the account has changed since)."""
    pending = request.session.get(PENDING_LOGIN_KEY)
    if not pending or timezone.now().timestamp() - pending['at'] > PENDING_LOGIN_MAX_AGE_SECONDS:
        return None
    user = get_user_model().objects.filter(pk=pending['user_id'], is_active=True).first()
    if user is None or setup.pending_stage(user) is not None:
        return None
    return user


def _clear_pending(request):
    request.session.pop(PENDING_LOGIN_KEY, None)
    request.session.pop(DEVICE_TEST_CODE_KEY, None)


def verify_device(request):
    if request.user.is_authenticated:
        return redirect(_home_url_for(request.user))

    user = _pending_user(request)
    if user is None:
        _clear_pending(request)
        return redirect('index')
    security = UserSecurity.objects.get(user=user)
    username = request.session[PENDING_LOGIN_KEY]['username']

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'cancel':
            _clear_pending(request)
            return redirect('index')

        if action == 'resend':
            try:
                test_code = otp.issue(user, 'device', security.phone)
            except otp.OtpError as exc:
                messages.error(request, str(exc))
            else:
                if test_code:
                    request.session[DEVICE_TEST_CODE_KEY] = test_code
                messages.success(request, 'A new code is on its way.')
            return redirect('verify_device')

        if action == 'verify_code':
            try:
                otp.verify(user, 'device', security.phone, request.POST.get('code'))
            except otp.OtpError as exc:
                messages.error(request, str(exc))
                return redirect('verify_device')
            _clear_pending(request)
            user.backend = MODEL_BACKEND  # we authenticated it ourselves, in two steps
            auth_login(request, user)
            response = _signed_in_response(user, username)
            remember = bool(request.POST.get('remember'))
            if remember:
                devices.trust(response, request, user)
            notifications.new_sign_in(request, user, security.phone, remember)
            return response

        return redirect('verify_device')

    return render(request, 'verify_device.html', {
        'masked_phone': mask(security.phone),
        'sent': True,   # the code is texted the moment the password is accepted
        'resend': otp.resend_status(user, 'device', security.phone),
        'test_mode': sms.is_test_backend(),
        'test_code': request.session.get(DEVICE_TEST_CODE_KEY) if sms.is_test_backend() else None,
    })


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
            # Fully set up, but this browser isn't one they've proven yet: a
            # correct password alone isn't enough. (Anyone still mid-setup has
            # no verified number to text, so setup comes first, below.)
            if setup.pending_stage(user) is None and not devices.is_trusted(request, user):
                return _start_device_check(request, user, username)
            auth_login(request, user)
            return _signed_in_response(user, username)

        account, failures = login_throttle.register_failure(username)
        locked = failures >= login_throttle.MAX_ATTEMPTS
        security_log.sign_in_failed(request, account, failures, locked=locked)
        if locked and account is not None:
            # Only a real account has an owner to tell. The few milliseconds this adds can't
            # usefully show which names are real: each try costs five wrong passwords and
            # locks that account for 15 minutes.
            notifications.sign_in_locked(request, get_user_model().objects.filter(username=account).first())
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
