import io
import json
import urllib.error
import urllib.parse
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from . import otp, sms
from .models import OtpChallenge
from .phone import for_semaphore, mask, normalize_ph_mobile

User = get_user_model()


class PhoneTests(SimpleTestCase):
    def test_every_common_way_of_writing_a_number_is_the_same_number(self):
        for raw in ('0917 123 4567', '09171234567', '+63 917 123 4567', '639171234567', '917-123-4567', '(0917) 123.4567'):
            self.assertEqual(normalize_ph_mobile(raw), '+639171234567', raw)

    def test_rejects_things_that_are_not_ph_mobile_numbers(self):
        for raw in ('', None, '12345', '0817 123 4567', '+1 415 555 0100', '0917 123 456', '0917 123 45678', 'abc'):
            with self.assertRaises(ValueError, msg=raw):
                normalize_ph_mobile(raw)

    def test_mask_and_semaphore_format(self):
        self.assertEqual(mask('+639171234567'), '09•••••••67')
        self.assertEqual(for_semaphore('+639171234567'), '639171234567')


def _response(payload):
    body = io.BytesIO(json.dumps(payload).encode())
    body.__enter__ = lambda self=body: self
    body.__exit__ = lambda *a: False
    return body


@override_settings(SMS_BACKEND='semaphore', SEMAPHORE_API_KEY='secret-key', SEMAPHORE_SENDER_NAME='')
class SemaphoreBackendTests(SimpleTestCase):
    OK = [{'message_id': 1, 'status': 'Pending', 'code': '123456'}]

    def _send(self, response=None, side_effect=None):
        with mock.patch('accounts.sms.urllib.request.urlopen', return_value=response, side_effect=side_effect) as urlopen:
            try:
                sms.send_code('+639171234567', '123456')
            finally:
                self.urlopen = urlopen

    @override_settings(SEMAPHORE_ROUTE='otp')
    def test_posts_our_code_to_the_otp_route(self):
        self._send(_response(self.OK))
        request = self.urlopen.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.semaphore.co/api/v4/otp')
        self.assertEqual(request.get_method(), 'POST')
        sent = urllib.parse.parse_qs(request.data.decode())
        self.assertEqual(sent['number'], ['639171234567'])
        self.assertEqual(sent['code'], ['123456'])
        self.assertEqual(sent['apikey'], ['secret-key'])
        self.assertIn('{otp}', sent['message'][0])
        self.assertFalse(sent['message'][0].upper().startswith('TEST'))  # Semaphore silently drops these
        self.assertNotIn('sendername', sent)

    def test_the_default_is_the_one_credit_regular_route_with_the_code_in_the_text(self):
        self._send(_response(self.OK))
        request = self.urlopen.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.semaphore.co/api/v4/messages')
        sent = urllib.parse.parse_qs(request.data.decode())
        self.assertEqual(sent['number'], ['639171234567'])
        self.assertEqual(sent['apikey'], ['secret-key'])
        self.assertIn('123456', sent['message'][0])           # the regular route only sends what it is given
        self.assertNotIn('{otp}', sent['message'][0])
        self.assertNotIn('code', sent)
        self.assertFalse(sent['message'][0].upper().startswith('TEST'))
        self.assertLessEqual(len(sent['message'][0]), 160)    # longer than this would be a second text
        self.assertNotIn('sendername', sent)

    @override_settings(SEMAPHORE_ROUTE='otp')
    def test_the_otp_route_still_sends_our_own_code(self):
        self._send(_response(self.OK))
        sent = urllib.parse.parse_qs(self.urlopen.call_args.args[0].data.decode())
        self.assertEqual(sent['code'], ['123456'])
        self.assertIn('{otp}', sent['message'][0])

    @override_settings(SEMAPHORE_SENDER_NAME='EDNP')
    def test_sender_name_is_sent_when_configured(self):
        self._send(_response(self.OK))
        sent = urllib.parse.parse_qs(self.urlopen.call_args.args[0].data.decode())
        self.assertEqual(sent['sendername'], ['EDNP'])

    def test_failures_become_a_friendly_error_that_never_leaks_the_key(self):
        cases = [
            dict(response=_response({'apikey': ['Invalid']})),                 # error dict, not a list
            dict(response=_response([{'status': 'Failed'}])),                  # accepted but failed
            dict(response=_response([])),
            dict(side_effect=urllib.error.HTTPError('u', 401, 'no', {}, None)),
            dict(side_effect=urllib.error.URLError('down')),
            dict(side_effect=TimeoutError()),
        ]
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(sms.SmsError) as ctx:
                    self._send(**case)
                self.assertNotIn('secret-key', str(ctx.exception))

    @override_settings(SMS_BACKEND='test')
    def test_test_backend_never_touches_the_network(self):
        with mock.patch('accounts.sms.urllib.request.urlopen') as urlopen:
            sms.send_code('+639171234567', '123456')
        urlopen.assert_not_called()


class OtpTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('u', password='pw12345!x')
        self.target = '+639171234567'

    def _issue(self, purpose='phone_verify', target=None):
        return otp.issue(self.user, purpose, target or self.target)

    def _skip_cooldown(self):
        OtpChallenge.objects.update(created_at=timezone.now() - timedelta(minutes=2))

    def test_issue_in_test_mode_returns_the_code_and_it_verifies_once(self):
        code = self._issue()
        self.assertRegex(code, r'^\d{6}$')
        otp.verify(self.user, 'phone_verify', self.target, code)
        with self.assertRaises(otp.OtpError):  # single use
            otp.verify(self.user, 'phone_verify', self.target, code)

    def test_only_a_hash_is_stored(self):
        code = self._issue()
        self.assertNotIn(code, OtpChallenge.objects.get().code_hash)

    def test_spaces_in_the_submitted_code_are_ignored(self):
        code = self._issue()
        otp.verify(self.user, 'phone_verify', self.target, f'{code[:3]} {code[3:]}')

    def test_wrong_guesses_are_counted_and_lock_the_code_even_for_the_right_answer(self):
        code = self._issue()
        wrong = '000000' if code != '000000' else '111111'
        for _ in range(settings.OTP_MAX_ATTEMPTS):
            with self.assertRaises(otp.OtpError):
                otp.verify(self.user, 'phone_verify', self.target, wrong)
        self.assertEqual(OtpChallenge.objects.get().attempts, settings.OTP_MAX_ATTEMPTS)  # kept despite the exceptions
        with self.assertRaisesMessage(otp.OtpError, 'Too many wrong attempts'):
            otp.verify(self.user, 'phone_verify', self.target, code)

    def test_expired_code_is_refused(self):
        code = self._issue()
        OtpChallenge.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaisesMessage(otp.OtpError, 'expired'):
            otp.verify(self.user, 'phone_verify', self.target, code)

    def test_a_newer_code_replaces_the_older_one(self):
        old = self._issue()
        self._skip_cooldown()
        new = self._issue()
        with self.assertRaises(otp.OtpError):
            otp.verify(self.user, 'phone_verify', self.target, old)
        otp.verify(self.user, 'phone_verify', self.target, new)

    def test_code_is_bound_to_user_purpose_and_number(self):
        code = self._issue()
        other = User.objects.create_user('o', password='pw12345!x')
        with self.assertRaises(otp.OtpError):
            otp.verify(other, 'phone_verify', self.target, code)
        with self.assertRaises(otp.OtpError):
            otp.verify(self.user, 'device', self.target, code)
        with self.assertRaises(otp.OtpError):
            otp.verify(self.user, 'phone_verify', '+639179999999', code)

    def test_resend_cooldown(self):
        self._issue()
        with self.assertRaisesMessage(otp.OtpError, 'Please wait'):
            self._issue()

    def test_hourly_cap_per_account(self):
        # Sign-in codes aren't under the daily setup allowance, only the hourly cap.
        for i in range(5):
            self._issue(purpose='device', target=f'+63917000000{i}')
            self._skip_cooldown()
        with self.assertRaisesMessage(otp.OtpError, 'Too many codes'):
            self._issue(purpose='device', target='+639178888888')

    def test_hourly_cap_per_number_across_accounts(self):
        # Five different accounts can't be used to spam one stranger's phone.
        for i in range(5):
            user = User.objects.create_user(f'x{i}', password='pw12345!x')
            otp.issue(user, 'phone_verify', self.target)
        with self.assertRaisesMessage(otp.OtpError, 'Too many codes'):
            self._issue()

    def test_failed_send_leaves_no_trace_and_keeps_the_previous_code_valid(self):
        code = self._issue()
        self._skip_cooldown()
        with mock.patch('accounts.sms.send_code', side_effect=sms.SmsError('down')):
            with self.assertRaisesMessage(otp.OtpError, 'down'):
                self._issue()
        self.assertEqual(OtpChallenge.objects.count(), 1)
        otp.verify(self.user, 'phone_verify', self.target, code)

    @override_settings(SMS_BACKEND='semaphore', SEMAPHORE_API_KEY='k')
    def test_real_mode_never_hands_the_code_back(self):
        with mock.patch('accounts.sms.send_code') as send:
            returned = self._issue()
        self.assertIsNone(returned)
        self.assertRegex(send.call_args.args[1], r'^\d{6}$')  # but it was texted
