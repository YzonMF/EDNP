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

SEMAPHORE_OTP_URL = 'https://api.semaphore.co/api/v4/otp'
REQUEST_TIMEOUT_SECONDS = 10

# {otp} is Semaphore's placeholder for the code. Must not begin with "TEST" —
# Semaphore silently drops messages that do.
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
    payload = {
        'apikey': settings.SEMAPHORE_API_KEY,
        'number': for_semaphore(e164),
        'message': MESSAGE_TEMPLATE,
        'code': code,
    }
    if settings.SEMAPHORE_SENDER_NAME:
        payload['sendername'] = settings.SEMAPHORE_SENDER_NAME

    request = urllib.request.Request(
        SEMAPHORE_OTP_URL,
        data=urllib.parse.urlencode(payload).encode(),
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        # Never log the payload: it holds the API key and the code.
        logger.error('Semaphore rejected the OTP request: HTTP %s', exc.code)
        raise SmsError("We couldn't send the text message. Please try again in a moment.") from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        logger.error('Semaphore OTP request failed: %s', type(exc).__name__)
        raise SmsError("We couldn't reach the SMS service. Please try again in a moment.") from exc

    # Success is a list of message objects; failures come back as a dict of
    # field errors (bad number, no credits, ...).
    if not isinstance(body, list) or not body:
        logger.error('Semaphore returned an unexpected OTP response shape: %s', type(body).__name__)
        raise SmsError("We couldn't send the text message. Please try again in a moment.")
    if str(body[0].get('status', '')).lower() in ('failed', 'refunded'):
        logger.error('Semaphore reported OTP status %s', body[0].get('status'))
        raise SmsError("We couldn't send the text message. Please check the number and try again.")
