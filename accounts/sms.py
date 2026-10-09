"""Sending one-time codes by SMS.

Two backends, picked by settings.SMS_BACKEND: 'semaphore' sends a real text
through Semaphore's OTP route; 'test' sends nothing and lets the caller show
the code on screen. Everything else in the app calls send_code() and doesn't
care which one is active.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

from .phone import for_semaphore

logger = logging.getLogger(__name__)

SEMAPHORE_URLS = {
    'standard': 'https://api.semaphore.co/api/v4/messages',   # 1 credit per text
    'otp': 'https://api.semaphore.co/api/v4/otp',             # 2 credits per text; Semaphore's priority route
}
REQUEST_TIMEOUT_SECONDS = 10

# {otp} is where the code goes. Must not begin with "TEST" — Semaphore silently drops
# messages that do. Kept under 160 characters: each extra 160 is another text, and credits.
MESSAGE_TEMPLATE = 'EDNP Clergy Records: your verification code is {otp}. It expires in 5 minutes. Never share it with anyone.'


class SmsError(Exception):
    """The text could not be sent. The message is safe to show the user."""


def is_test_backend():
    return settings.SMS_BACKEND == 'test'


def send_code(e164, code):
    """Delivers `code` to the phone number. In test mode does nothing."""
    if is_test_backend():
        return
    _send_via_semaphore(e164, code)


def _send_via_semaphore(e164, code):
    route = settings.SEMAPHORE_ROUTE
    payload = {
        'apikey': settings.SEMAPHORE_API_KEY,
        'number': for_semaphore(e164),
    }
    if route == 'otp':
        # Semaphore puts our code where {otp} is.
        payload['message'] = MESSAGE_TEMPLATE
        payload['code'] = code
    else:
        # The regular route just sends the text we give it, so the code is already in it.
        payload['message'] = MESSAGE_TEMPLATE.replace('{otp}', code)
    if settings.SEMAPHORE_SENDER_NAME:
        payload['sendername'] = settings.SEMAPHORE_SENDER_NAME

    request = urllib.request.Request(
        SEMAPHORE_URLS[route],
        data=urllib.parse.urlencode(payload).encode(),
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        # Never log the payload: it holds the API key and the code.
        logger.error('Semaphore rejected the %s request: HTTP %s', route, exc.code)
        raise SmsError("We couldn't send the text message. Please try again in a moment.") from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        logger.error('Semaphore %s request failed: %s', route, type(exc).__name__)
        raise SmsError("We couldn't reach the SMS service. Please try again in a moment.") from exc

    # Success is a list of message objects; failures come back as a dict of
    # field errors (bad number, no credits, ...).
    if not isinstance(body, list) or not body:
        logger.error('Semaphore returned an unexpected %s response shape: %s', route, type(body).__name__)
        raise SmsError("We couldn't send the text message. Please try again in a moment.")
    if str(body[0].get('status', '')).lower() in ('failed', 'refunded'):
        logger.error('Semaphore reported %s status %s', route, body[0].get('status'))
        raise SmsError("We couldn't send the text message. Please check the number and try again.")
