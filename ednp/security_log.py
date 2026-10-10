"""Security events, written to the 'ednp.security' logger (see LOGGING in settings):
wrong passwords and lockouts, and the bulk exports. They go to the console, where the
host collects them, so an attack in progress or a download nobody expected leaves a trace.

Never put a password, a texted code, or a typed-in username that matches no account in
a message here: people type their password into the username box.
"""

import ipaddress
import logging

log = logging.getLogger('ednp.security')


def _ip(text):
    """`text` as a normalised IP address, or None if it isn't one."""
    try:
        return str(ipaddress.ip_address((text or '').strip()))
    except ValueError:
        return None


def caller(request):
    """Where a request came from, for the log only. Behind a proxy REMOTE_ADDR is the
    proxy itself, so the last X-Forwarded-For entry is shown beside it. Both are checked
    to be real IP addresses, so a forged header can't write text into the log. Neither is
    ever used to decide anything: nothing guarantees that header comes from the proxy."""
    remote = _ip(request.META.get('REMOTE_ADDR')) or '?'
    forwarded = _ip((request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[-1]) or '-'
    return f'from={remote} fwd={forwarded}'


def sign_in_failed(request, account, failures, locked):
    """A wrong password on the sign-in page. `account` is the real username it was
    tried against, or None when the name matches no account."""
    if locked:
        log.warning('sign-in locked account=%s failures=%s %s', account or '-', failures, caller(request))
    else:
        log.info('sign-in failed account=%s failures=%s %s', account or '-', failures, caller(request))


def password_check_failed(request, account, failures, locked):
    """A wrong password where a signed-in user has to re-enter it (changing their number, say)."""
    level = logging.WARNING if locked else logging.INFO
    log.log(level, 'password check failed account=%s failures=%s%s %s',
            account, failures, ' (now locked)' if locked else '', caller(request))


def export(request, user, what):
    """Someone took a copy of the data out: a database backup or the documents archive."""
    log.info('%s by=%s %s', what, user.username, caller(request))
