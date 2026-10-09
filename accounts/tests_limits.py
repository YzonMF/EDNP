from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from registrar.provisioning import provision_account
from registrar.models import ClergyRecord

from . import otp, sms
from .models import OtpChallenge
from .testing import activate
from .tests_mysecurity import CLERGY_SECURITY, OLD, PASSWORD, SecurityTestCase

User = get_user_model()
A, B, C = '+639171111111', '+639172222222', '+639173333333'


class LimitTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('u', password='pw12345!x')

    def issue(self, purpose='phone_verify', target=A):
        return otp.issue(self.user, purpose, target)

    def age(self, **kwargs):
        """Pretend every code so far was sent this long ago."""
        OtpChallenge.objects.update(created_at=timezone.now() - timedelta(**kwargs))


class WrongGuessTests(LimitTestCase):
    def test_three_wrong_guesses_then_the_code_is_dead_with_a_count_shown(self):
        code = self.issue()
        wrong = '000000' if code != '000000' else '111111'
        messages = []
        for _ in range(3):
            with self.assertRaises(otp.OtpError) as ctx:
                otp.verify(self.user, 'phone_verify', A, wrong)
            messages.append(str(ctx.exception))
        self.assertIn('2 attempts left', messages[0])
        self.assertIn('1 attempt left', messages[1])
        self.assertIn('last attempt', messages[2])
        with self.assertRaisesMessage(otp.OtpError, 'Too many wrong attempts'):
            otp.verify(self.user, 'phone_verify', A, code)   # even the right code is refused now


class DailyAllowanceTests(LimitTestCase):
    def test_three_codes_a_day_for_registering_or_changing_a_number_then_the_next_day(self):
        self.issue('phone_verify', A)
        self.age(minutes=5)
        self.issue('phone_change', B)           # registering and changing share one allowance
        self.age(minutes=5)
        self.issue('phone_verify', C)
        self.age(minutes=5)
        with self.assertRaises(otp.OtpError) as ctx:
            self.issue('phone_verify', A)
        self.assertIn('all 3 codes for today', str(ctx.exception))
        self.assertIn('hours', str(ctx.exception))

        self.age(hours=25)                       # the next day
        self.assertTrue(self.issue('phone_verify', A))

    def test_it_reopens_one_slot_at_a_time_as_the_oldest_code_ages_out(self):
        for number in (A, B, C):
            self.issue('phone_verify', number)
            self.age(minutes=5)
        # Make only the oldest code old enough to fall out of the 24-hour window.
        oldest = OtpChallenge.objects.order_by('created_at').first()
        OtpChallenge.objects.filter(pk=oldest.pk).update(created_at=timezone.now() - timedelta(hours=25))
        self.assertTrue(self.issue('phone_verify', A))

    def test_a_failed_text_does_not_use_up_a_code(self):
        for _ in range(5):
            with mock.patch('accounts.sms.send_code', side_effect=sms.SmsError('down')):
                with self.assertRaises(otp.OtpError):
                    self.issue('phone_verify', A)
        self.assertEqual(OtpChallenge.objects.count(), 0)
        self.assertTrue(self.issue('phone_verify', A))

    def test_signing_in_on_a_new_device_is_not_held_to_the_daily_three(self):
        for i in range(4):
            self.assertTrue(self.issue('device', f'+63917000000{i}'))
            self.age(minutes=5)

    def test_one_persons_allowance_is_not_anothers(self):
        for number in (A, B, C):
            self.issue('phone_verify', number)
            self.age(minutes=5)
        other = User.objects.create_user('o', password='pw12345!x')
        self.assertTrue(otp.issue(other, 'phone_verify', '+639174444444'))


class CooldownTests(LimitTestCase):
    def test_resending_to_the_same_number_waits_but_a_corrected_number_does_not(self):
        self.issue('phone_verify', A)
        with self.assertRaisesMessage(otp.OtpError, 'Please wait'):
            self.issue('phone_verify', A)
        self.assertTrue(self.issue('phone_verify', B))   # went Back and fixed a typo


class ResendStatusTests(LimitTestCase):
    def test_wait_counts_down_to_zero(self):
        self.issue('phone_verify', A)
        self.assertAlmostEqual(otp.resend_status(self.user, 'phone_verify', A)['wait'], 60, delta=2)
        self.age(seconds=20)
        self.assertAlmostEqual(otp.resend_status(self.user, 'phone_verify', A)['wait'], 40, delta=2)
        self.age(seconds=61)
        self.assertEqual(otp.resend_status(self.user, 'phone_verify', A)['wait'], 0)
        self.assertEqual(otp.resend_status(self.user, 'phone_verify', B)['wait'], 0)   # other number: no wait

    def test_exhausted_state_says_when_it_reopens(self):
        for number in (A, B, C):
            self.issue('phone_verify', number)
            self.age(minutes=5)
        status = otp.resend_status(self.user, 'phone_verify', A)
        self.assertTrue(status['exhausted'])
        self.assertEqual(status['retry_text'], '24 hours')
        self.age(hours=10)
        self.assertEqual(otp.resend_status(self.user, 'phone_verify', A)['retry_text'], '14 hours')
        self.assertFalse(otp.resend_status(self.user, 'device', A)['exhausted'])


class SetupPageTests(TestCase):
    def setUp(self):
        cache.clear()
        record = ClergyRecord.objects.create(id='CLG-001', name='Fr. A')
        self.user, password = provision_account(record)
        self.client = Client()
        self.client.post('/', {'username': 'CLG-001', 'password': password})
        self.url = reverse('activate')

    def send(self, phone='0917 111 1111'):
        return self.client.post(self.url, {'action': 'send_code', 'name': 'Fr. A', 'phone': phone}, follow=True)

    def test_code_screen_has_back_and_a_greyed_countdown_resend(self):
        page = self.send()
        self.assertContains(page, 'Back')
        self.assertContains(page, 'code-send')
        self.assertContains(page, 'Resend in ')
        self.assertContains(page, 'disabled')
        self.assertContains(page, 'data-wait="')
        wait = page.context['resend']['wait']
        self.assertTrue(55 <= wait <= 60)

    def test_resend_button_is_enabled_once_the_wait_is_over(self):
        self.send()
        OtpChallenge.objects.update(created_at=timezone.now() - timedelta(seconds=90))
        page = self.client.get(self.url)
        self.assertEqual(page.context['resend']['wait'], 0)
        self.assertNotRegex(page.content.decode(), r'Resend in \d+s')

    def test_back_returns_to_the_number_form_and_a_different_number_can_be_sent_at_once(self):
        self.send()
        page = self.client.post(self.url, {'action': 'change_number'}, follow=True)
        self.assertContains(page, 'Send a code first')       # back to the unsent state
        page = self.send('0918 222 2222')            # straight away, no 60-second wait
        self.assertContains(page, 'Code sent to')
        self.assertTrue(OtpChallenge.objects.filter(target='+639182222222').exists())

    def test_after_three_codes_the_page_says_so_and_offers_no_button(self):
        for phone in ('0917 111 1111', '0917 222 2222', '0917 333 3333'):
            self.send(phone)
            OtpChallenge.objects.update(created_at=timezone.now() - timedelta(minutes=5))
            self.client.post(self.url, {'action': 'change_number'})
        page = self.client.get(self.url)
        self.assertContains(page, "You've used all 3 codes for today")
        self.assertContains(page, 'No more today')
        blocked = self.send('0917 444 4444')
        self.assertContains(blocked, "all 3 codes for today")
        self.assertFalse(OtpChallenge.objects.filter(target='+639174444444').exists())

    def test_the_resend_form_is_refused_on_the_server_even_if_the_button_is_forced_on(self):
        self.send()
        page = self.client.post(self.url, {'action': 'resend'}, follow=True)   # inside the 60s
        self.assertContains(page, 'Please wait')
        self.assertEqual(OtpChallenge.objects.count(), 1)


class DevicePageTests(TestCase):
    def test_the_new_device_code_page_has_the_countdown_too(self):
        cache.clear()
        user = activate(User.objects.create_user('fr', password=PASSWORD), phone=OLD)
        c = Client()
        c.post('/', {'username': 'fr', 'password': PASSWORD})
        page = c.get(reverse('verify_device'))
        self.assertContains(page, 'Resend in ')
        self.assertTrue(55 <= page.context['resend']['wait'] <= 60)


class SecurityPageTests(SecurityTestCase):
    def test_changing_the_number_shows_the_countdown_and_uses_the_shared_allowance(self):
        self.send()
        page = self.client.get(CLERGY_SECURITY)
        self.assertContains(page, 'Resend in ')
        self.assertTrue(55 <= page.context['resend']['wait'] <= 60)
        self.assertEqual(page.context['resend']['exhausted'], False)
