"""Forgot password: reset by a code texted to the verified number, without the
page ever revealing whether a number belongs to an account."""

import re
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from clergy.models import ClergyProfile
from ednp import login_throttle
from registrar.models import ClergyRecord

from . import devices, forgot, otp
from .models import AccountAuditLog, OtpChallenge, ResetRequest, TrustedDevice, UserSecurity
from .testing import activate, trust_device
from .views import FORGOT_KEY, FORGOT_OK_KEY, FORGOT_TEST_CODE_KEY

User = get_user_model()
OLD_PASSWORD = 'pw12345!x'
NEW_PASSWORD = 'Sturdy!Pass-2026'
REAL = '0917 123 4567'
REAL_E164 = '+639171234567'
UNKNOWN = '0918 555 0000'


def messages_of(page):
    return [m.message for m in page.context['messages']]


def normalise_wait(text):
    return re.sub(r'\d+ seconds?', 'N seconds', text)


class ForgotCase(TestCase):
    def setUp(self):
        cache.clear()
        self.record = ClergyRecord.objects.create(id='CLG-001', name='Fr. Juan', phone=REAL_E164)
        self.user = User.objects.create_user('fr', password=OLD_PASSWORD)
        ClergyProfile.objects.create(user=self.user, record=self.record)
        activate(self.user, phone=REAL_E164)
        self.client = Client()
        self.url = reverse('forgot_password')

    def post(self, **data):
        return self.client.post(self.url, data, follow=True)

    def send(self, phone=REAL):
        return self.post(action='send_code', phone=phone)

    def code(self):
        return self.client.session[FORGOT_TEST_CODE_KEY]

    def verify(self, code=None):
        return self.post(action='verify_code', code=code or self.code())

    def reset(self, new=NEW_PASSWORD, confirm=None):
        return self.post(action='set_password', new_password=new, confirm_password=confirm or new)

    def dashboard_status(self, client=None):
        return (client or self.client).get(reverse('clergy:dashboard')).status_code


class EntryPointTests(ForgotCase):
    def test_the_login_page_links_to_it_and_the_page_loads_signed_out(self):
        self.assertContains(self.client.get(reverse('index')), reverse('forgot_password'))
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Forgot your password?')
        self.assertContains(page, 'Send code')

    def test_someone_already_signed_in_is_sent_home(self):
        c = trust_device(Client(), self.user)
        c.post('/', {'username': 'fr', 'password': OLD_PASSWORD})
        self.assertRedirects(c.get(self.url), reverse('clergy:dashboard'), fetch_redirect_response=False)

    def test_pressing_enter_never_triggers_a_text(self):
        html = self.client.get(self.url).content.decode()
        self.assertLess(html.index('default-submit" name="action" value="send_code"'), html.index('class="code-send"'))
        html = self.send().content.decode()
        self.assertLess(html.index('default-submit" name="action" value="verify_code"'), html.index('class="code-send"'))

    def test_verifying_before_asking_for_a_code_says_to_send_first(self):
        self.assertIn('Press Send code first, then enter the code we text you.', messages_of(self.verify('123456')))

    def test_a_bad_number_is_refused_politely(self):
        page = self.send('12345')
        self.assertIn('Enter a valid Philippine mobile number', ' '.join(messages_of(page)))
        self.assertFalse(ResetRequest.objects.exists())


class NothingRevealsWhoIsRegisteredTests(ForgotCase):
    """A number with an account and a number without must look the same."""

    def snapshot(self, phone):
        page = self.send(phone)
        context = page.context
        return {
            'status': page.status_code,
            'stage': context['stage'],
            'sent': context['sent'],
            'messages': [normalise_wait(m) for m in messages_of(page)],
            'resend_wait_is_cooldown': 55 <= context['resend']['wait'] <= 60,
            'exhausted': context['resend']['exhausted'],
            'masked_shape': re.sub(r'\d', '#', context['pending_masked']),
            'sentence': 'If that number is registered to an account' in page.content.decode(),
        }

    def test_the_screen_after_asking_is_the_same_for_a_real_and_an_unknown_number(self):
        real = self.snapshot(REAL)
        self.client = Client()
        unknown = self.snapshot(UNKNOWN)
        self.assertEqual(real, unknown)
        self.assertEqual(real['stage'], 'code')
        self.assertTrue(real['sentence'])

    def test_only_a_real_account_is_actually_texted(self):
        self.send(REAL)
        self.assertEqual(OtpChallenge.objects.filter(purpose='password_reset').count(), 1)
        self.client = Client()
        self.send(UNKNOWN)
        self.assertEqual(OtpChallenge.objects.filter(purpose='password_reset').count(), 1)

    @override_settings(SMS_BACKEND='semaphore', SEMAPHORE_API_KEY='k')
    def test_no_text_goes_to_a_number_without_an_account(self):
        UserSecurity.objects.filter(user=self.user).update(phone_verified_via='sms')
        with mock.patch('accounts.sms._send_via_semaphore') as send:
            self.send(UNKNOWN)
            self.assertEqual(send.call_count, 0)
            self.client = Client()
            self.send(REAL)
            self.assertEqual(send.call_count, 1)

    def test_the_cooldown_message_is_the_same_for_both(self):
        self.send(REAL)
        again_real = normalise_wait(' '.join(messages_of(self.post(action='resend'))))
        self.client = Client()
        self.send(UNKNOWN)
        again_unknown = normalise_wait(' '.join(messages_of(self.post(action='resend'))))
        self.assertEqual(again_real, again_unknown)
        self.assertIn('Please wait', again_real)

    def test_the_daily_limit_message_is_the_same_for_both(self):
        def exhaust(phone):
            self.client = Client()
            self.send(phone)
            for _ in range(2):
                ResetRequest.objects.filter(number_hash=forgot._hash(otp_e164(phone))).update(created_at=timezone.now() - timedelta(minutes=5))
                self.post(action='resend')
            ResetRequest.objects.filter(number_hash=forgot._hash(otp_e164(phone))).update(created_at=timezone.now() - timedelta(minutes=5))
            return ' '.join(messages_of(self.post(action='resend')))

        real, unknown = exhaust(REAL), exhaust(UNKNOWN)
        self.assertEqual(real, unknown)
        self.assertIn("all 3 codes for today", real)

    @override_settings(FORGOT_MAX_SENDS_PER_HOUR_SITEWIDE=2)
    def test_the_site_wide_ceiling_counts_unknown_numbers_too(self):
        for phone in ('0918 000 0001', '0918 000 0002'):
            self.client = Client()          # a separate visitor each time
            self.send(phone)
        self.client = Client()
        page = self.send(REAL)              # a real account is held to it, same as anyone
        self.assertIn('Too many password resets', ' '.join(messages_of(page)))
        self.assertFalse(OtpChallenge.objects.filter(purpose='password_reset').exists())

    def test_requests_are_counted_for_unknown_numbers_and_only_hashes_are_kept(self):
        self.send(UNKNOWN)
        row = ResetRequest.objects.get()
        self.assertNotIn('5550000', row.number_hash)
        self.assertEqual(row.number_hash, forgot._hash(otp_e164(UNKNOWN)))

    def test_a_failure_to_send_to_a_real_account_is_not_shown(self):
        with mock.patch('accounts.otp.issue', side_effect=otp.OtpError('SMS service down')):
            page = self.send(REAL)
        self.assertEqual(messages_of(page), [])
        self.assertEqual(page.context['stage'], 'code')     # looks exactly like a success

    def test_wrong_codes_look_the_same_and_never_say_how_many_guesses_are_left(self):
        def three_wrong(phone):
            self.client = Client()
            self.send(phone)
            out = []
            for _ in range(3):
                out.append(' '.join(messages_of(self.verify('000000'))))
            return out
        real, unknown = three_wrong(REAL), three_wrong(UNKNOWN)
        self.assertEqual(real, unknown)
        self.assertEqual(real[2], 'Too many wrong codes. Please start again.')
        self.assertFalse(any('attempt' in m for m in real))


def otp_e164(phone):
    from .phone import normalize_ph_mobile
    return normalize_ph_mobile(phone)


class WhoMayResetTests(ForgotCase):
    def asked_for_real_account(self):
        self.send(REAL)
        return OtpChallenge.objects.filter(purpose='password_reset').exists()

    def test_an_activated_account_with_a_verified_number_can(self):
        self.assertTrue(self.asked_for_real_account())

    def test_an_account_still_on_its_registrar_password_cannot(self):
        UserSecurity.objects.filter(user=self.user).update(activated_at=None)
        self.assertFalse(self.asked_for_real_account())

    def test_a_deactivated_account_cannot(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertFalse(self.asked_for_real_account())

    @override_settings(SMS_BACKEND='semaphore', SEMAPHORE_API_KEY='k')
    def test_a_number_only_verified_in_test_mode_cannot_once_real_sms_is_on(self):
        with mock.patch('accounts.sms._send_via_semaphore') as send:
            self.assertFalse(self.asked_for_real_account())
            self.assertEqual(send.call_count, 0)
        UserSecurity.objects.filter(user=self.user).update(phone_verified_via='sms')
        ResetRequest.objects.all().delete()     # the first try counted toward this number's cooldown, as any try does
        with mock.patch('accounts.sms._send_via_semaphore') as send:
            self.client = Client()
            self.assertTrue(self.asked_for_real_account())
            self.assertEqual(send.call_count, 1)

    def test_registrar_staff_can_too(self):
        reg = activate(User.objects.create_user('reg', password=OLD_PASSWORD, is_staff=True), phone='+639181112233')
        self.send('0918 111 2233')
        self.assertTrue(OtpChallenge.objects.filter(user=reg, purpose='password_reset').exists())

    def test_different_ways_of_typing_the_number_find_the_same_account(self):
        self.send('+63 917 123 4567')
        self.assertTrue(OtpChallenge.objects.filter(user=self.user, purpose='password_reset').exists())


class CodeStepTests(ForgotCase):
    def test_the_right_code_moves_on_to_choosing_a_password(self):
        self.send()
        page = self.verify()
        self.assertEqual(page.context['stage'], 'password')
        self.assertContains(page, 'Choose a new password')
        self.assertNotIn(FORGOT_KEY, self.client.session)

    def test_a_wrong_code_gives_the_generic_message(self):
        self.send()
        page = self.verify('000000')
        self.assertIn("That code isn't right, or it has expired.", ' '.join(messages_of(page)))
        self.assertEqual(page.context['stage'], 'code')

    def test_an_expired_code_is_just_wrong(self):
        self.send()
        code = self.code()
        OtpChallenge.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.verify(code).context['stage'], 'code')

    def test_three_wrong_codes_end_it_and_even_the_right_code_no_longer_works(self):
        self.send()
        code = self.code()
        for _ in range(3):
            self.verify('000000')
        self.assertEqual(self.post(action='verify_code', code=code).context['stage'], 'number')

    def test_a_code_cannot_be_used_twice(self):
        self.send()
        code = self.code()
        self.assertEqual(self.verify(code).context['stage'], 'password')
        session = self.client.session
        session.pop(FORGOT_OK_KEY)
        session[FORGOT_KEY] = {'phone': REAL_E164, 'wrong': 0}      # back at the code screen for the same number
        session.save()
        self.assertEqual(self.verify(code).context['stage'], 'code')   # the same code is spent

    def test_resend_is_refused_inside_the_cooldown_and_sends_a_new_code_after(self):
        self.send()
        first = self.code()
        self.assertIn('Please wait', ' '.join(messages_of(self.post(action='resend'))))
        OtpChallenge.objects.update(created_at=timezone.now() - timedelta(seconds=90))
        ResetRequest.objects.update(created_at=timezone.now() - timedelta(seconds=90))
        self.post(action='resend')
        self.assertEqual(OtpChallenge.objects.filter(purpose='password_reset').count(), 2)
        self.assertEqual(self.verify(first).context['stage'], 'code')     # the old code was replaced
        self.assertEqual(self.verify().context['stage'], 'password')

    def test_back_returns_to_the_number_form(self):
        self.send()
        page = self.post(action='change_number')
        self.assertEqual(page.context['stage'], 'number')
        self.assertContains(page, 'Send a code first')

    def test_the_button_counts_down_after_sending(self):
        page = self.send()
        self.assertRegex(page.content.decode(), r'Resend in \d+s')
        self.assertContains(page, '09•••••••67')


class ChoosingTheNewPasswordTests(ForgotCase):
    def verified(self):
        self.send()
        self.verify()

    def test_the_password_page_cannot_be_reached_or_used_without_the_code(self):
        r = self.reset()
        self.assertEqual(r.context['stage'], 'number')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD_PASSWORD))

    def test_mistakes_are_refused_and_change_nothing(self):
        self.verified()
        self.reset(confirm='Different!Pass-1')
        self.reset(new='short', confirm='short')
        self.reset(new='12345678', confirm='12345678')
        self.reset(new=OLD_PASSWORD)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD_PASSWORD))
        self.assertEqual(self.client.session.get(FORGOT_OK_KEY) is not None, True)   # still allowed to try again

    def test_success_changes_the_password_and_signs_this_browser_in(self):
        self.verified()
        r = self.client.post(self.url, {'action': 'set_password', 'new_password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD})
        self.assertRedirects(r, reverse('clergy:dashboard'), fetch_redirect_response=False)
        self.assertIn(devices.cookie_name(self.user), r.cookies)           # this browser is trusted now
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))
        self.assertFalse(self.user.check_password(OLD_PASSWORD))
        self.assertEqual(self.dashboard_status(), 200)
        self.assertIsNotNone(UserSecurity.objects.get(user=self.user).password_changed_at)

    def test_every_other_device_and_session_is_shut_out(self):
        other = trust_device(Client(), self.user)
        other.post('/', {'username': 'fr', 'password': OLD_PASSWORD})
        self.assertEqual(self.dashboard_status(other), 200)
        old_trust = list(TrustedDevice.objects.filter(user=self.user).values_list('pk', flat=True))

        self.verified()
        self.reset()

        self.assertNotEqual(self.dashboard_status(other), 200)                          # its session is gone
        self.assertFalse(TrustedDevice.objects.filter(pk__in=old_trust).exists())       # and so is its trust
        self.assertEqual(TrustedDevice.objects.filter(user=self.user).count(), 1)       # only this browser
        again = Client()                                                                # the old browser signing in again
        again.cookies.update(other.cookies)
        r = again.post('/', {'username': 'fr', 'password': NEW_PASSWORD})
        self.assertRedirects(r, reverse('verify_device'), fetch_redirect_response=False)

    def test_a_locked_out_account_is_unlocked(self):
        for _ in range(5):
            login_throttle.register_failure('fr')
        self.assertTrue(login_throttle.is_locked_out('fr'))
        self.verified()
        self.reset()
        self.assertFalse(login_throttle.is_locked_out('fr'))

    def test_it_is_recorded_in_the_account_activity_log(self):
        self.verified()
        self.reset()
        entry = AccountAuditLog.objects.get(action='password_reset')
        self.assertEqual((entry.actor_name, entry.target_name, entry.note), ('Fr. Juan', 'Fr. Juan', 'via mobile number'))

    def test_no_code_is_left_alive_afterwards(self):
        self.verified()
        self.reset()
        self.assertFalse(OtpChallenge.objects.filter(user=self.user, consumed_at__isnull=True).exists())

    def test_the_approval_expires_after_ten_minutes(self):
        self.verified()
        session = self.client.session
        session[FORGOT_OK_KEY]['at'] -= 11 * 60
        session.save()
        self.assertEqual(self.client.get(self.url).context['stage'], 'number')
        self.assertEqual(self.reset().context['stage'], 'number')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD_PASSWORD))

    def test_the_approval_is_void_if_the_number_changed_in_the_meantime(self):
        self.verified()
        UserSecurity.objects.filter(user=self.user).update(phone='+639189998877')
        self.assertEqual(self.client.get(self.url).context['stage'], 'number')
        self.reset()
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD_PASSWORD))

    def test_a_visitor_cannot_use_someone_elses_approval(self):
        self.verified()
        stranger = Client()
        stranger.post(self.url, {'action': 'set_password', 'new_password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD})
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD_PASSWORD))
