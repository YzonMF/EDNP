from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from . import devices, sms
from .models import OtpChallenge, TrustedDevice
from .testing import activate, trust_device

User = get_user_model()
PHONE = '+639171234567'


class DeviceTestCase(TestCase):
    def setUp(self):
        self.user = activate(User.objects.create_user('fr', password='pw12345!x'), phone=PHONE)
        self.client = Client()

    def login(self, client=None, password='pw12345!x'):
        return (client or self.client).post('/', {'username': 'fr', 'password': password})

    def signed_in(self, client=None):
        return '_auth_user_id' in (client or self.client).session

    def device_code(self, client=None):
        return (client or self.client).session['device_test_code']

    def enter_code(self, code=None, remember=True, client=None):
        c = client or self.client
        data = {'action': 'verify_code', 'code': code or self.device_code(c)}
        if remember:
            data['remember'] = '1'
        return c.post(reverse('verify_device'), data)


class NewDeviceTests(DeviceTestCase):
    def test_correct_password_alone_does_not_sign_in_from_an_unknown_device(self):
        r = self.login()
        self.assertRedirects(r, reverse('verify_device'), fetch_redirect_response=False)
        self.assertFalse(self.signed_in())
        self.assertEqual(self.client.get('/clergy/dashboard/').status_code, 302)
        self.assertTrue(OtpChallenge.objects.filter(user=self.user, purpose='device', target=PHONE).exists())

    def test_wrong_password_sends_no_text(self):
        self.login(password='nope')
        self.assertFalse(OtpChallenge.objects.exists())   # no credits spent on someone guessing passwords
        self.assertFalse(self.signed_in())

    def test_the_code_page_shows_a_masked_number(self):
        self.login()
        page = self.client.get(reverse('verify_device'))
        self.assertContains(page, '09•••••••67')
        self.assertNotContains(page, '09171234567')

    def test_right_code_signs_in_and_remembers_the_device(self):
        self.login()
        r = self.enter_code()
        self.assertRedirects(r, reverse('clergy:dashboard'), fetch_redirect_response=False)
        self.assertTrue(self.signed_in())
        cookie = r.cookies[devices.cookie_name(self.user)]
        self.assertTrue(cookie['httponly'])
        device = TrustedDevice.objects.get(user=self.user)
        self.assertNotEqual(device.token_hash, cookie.value)   # only a hash is stored
        self.assertEqual(len(device.token_hash), 64)

        # Next time from this same browser: straight in, no new text.
        self.client.get(reverse('logout'))
        before = OtpChallenge.objects.count()
        self.assertRedirects(self.login(), reverse('clergy:dashboard'), fetch_redirect_response=False)
        self.assertEqual(OtpChallenge.objects.count(), before)

    def test_unticking_remember_signs_in_once_without_trusting_the_browser(self):
        self.login()
        r = self.enter_code(remember=False)
        self.assertTrue(self.signed_in())
        self.assertNotIn(devices.cookie_name(self.user), r.cookies)
        self.assertFalse(TrustedDevice.objects.exists())

    def test_wrong_code_does_not_sign_in(self):
        self.login()
        wrong = '000000' if self.device_code() != '000000' else '111111'
        self.enter_code(wrong)
        self.assertFalse(self.signed_in())
        self.assertFalse(TrustedDevice.objects.exists())

    def test_code_page_without_a_password_step_goes_back_to_login(self):
        self.assertRedirects(self.client.get(reverse('verify_device')), reverse('index'), fetch_redirect_response=False)
        self.assertRedirects(self.enter_code('123456'), reverse('index'), fetch_redirect_response=False)
        self.assertFalse(self.signed_in())

    def test_the_half_finished_login_expires(self):
        self.login()
        session = self.client.session
        session['pending_login']['at'] -= 11 * 60
        session.save()
        self.assertRedirects(self.client.get(reverse('verify_device')), reverse('index'), fetch_redirect_response=False)

    def test_cancel_discards_the_pending_login(self):
        self.login()
        self.client.post(reverse('verify_device'), {'action': 'cancel'})
        self.assertRedirects(self.client.get(reverse('verify_device')), reverse('index'), fetch_redirect_response=False)

    def test_reloading_the_login_reuses_the_code_already_sent(self):
        self.login()
        r = self.login()   # inside the resend cooldown
        self.assertRedirects(r, reverse('verify_device'), fetch_redirect_response=False)
        self.assertEqual(OtpChallenge.objects.count(), 1)

    def test_resend_inside_the_cooldown_stays_on_the_page_with_a_message(self):
        self.login()
        page = self.client.post(reverse('verify_device'), {'action': 'resend'}, follow=True)
        self.assertContains(page, 'Please wait')

    def test_sms_outage_shows_an_error_and_leaves_nothing_pending(self):
        with mock.patch('accounts.sms.send_code', side_effect=sms.SmsError('The SMS service is down.')):
            r = self.login()
        self.assertContains(r, 'The SMS service is down.')
        self.assertFalse(self.signed_in())
        self.assertRedirects(self.client.get(reverse('verify_device')), reverse('index'), fetch_redirect_response=False)

    def test_new_device_approval_signs_out_the_older_session(self):
        laptop = trust_device(Client(), self.user)
        self.login(laptop)
        self.assertTrue(self.signed_in(laptop))
        self.login()
        self.enter_code()
        self.assertTrue(self.signed_in())
        self.assertIn('signed_out=1', laptop.get('/')['Location'])


class TrustedDeviceTests(DeviceTestCase):
    def test_trusted_device_goes_straight_in_with_no_text(self):
        trust_device(self.client, self.user)
        self.assertRedirects(self.login(), reverse('clergy:dashboard'), fetch_redirect_response=False)
        self.assertFalse(OtpChallenge.objects.exists())

    def test_another_persons_trust_does_not_carry_over_on_a_shared_browser(self):
        other = activate(User.objects.create_user('other', password='pw12345!x'))
        trust_device(self.client, other)
        self.assertRedirects(self.login(), reverse('verify_device'), fetch_redirect_response=False)

    def test_a_guessed_or_stolen_token_for_someone_else_is_useless(self):
        other = activate(User.objects.create_user('other', password='pw12345!x'))
        other_client = trust_device(Client(), other)
        token = other_client.cookies[devices.cookie_name(other)].value
        self.client.cookies[devices.cookie_name(self.user)] = token   # their token, our cookie name
        self.assertRedirects(self.login(), reverse('verify_device'), fetch_redirect_response=False)

    def test_forged_token_is_rejected(self):
        self.client.cookies[devices.cookie_name(self.user)] = 'not-a-real-token'
        self.assertRedirects(self.login(), reverse('verify_device'), fetch_redirect_response=False)

    def test_expired_device_needs_a_code_again(self):
        trust_device(self.client, self.user)
        TrustedDevice.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertRedirects(self.login(), reverse('verify_device'), fetch_redirect_response=False)

    def test_revoked_device_needs_a_code_again(self):
        trust_device(self.client, self.user)
        TrustedDevice.objects.all().delete()
        self.assertRedirects(self.login(), reverse('verify_device'), fetch_redirect_response=False)

    def test_regular_use_keeps_a_device_trusted(self):
        trust_device(self.client, self.user)
        TrustedDevice.objects.update(expires_at=timezone.now() + timedelta(days=1))
        self.login()
        self.assertGreater(TrustedDevice.objects.get().expires_at, timezone.now() + timedelta(days=30))

    def test_browser_labels(self):
        cases = {
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36': 'Chrome on Windows',
            'Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/120.0 Safari/537.36 Edg/120.0': 'Edge on Windows',
            'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Safari/604.1': 'Safari on iPhone',
            'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36': 'Chrome on Android',
            '': 'Browser on unknown device',
        }
        for ua, expected in cases.items():
            request = mock.Mock(META={'HTTP_USER_AGENT': ua})
            self.assertEqual(devices.label_for(request), expected)


class AdminBackDoorTests(DeviceTestCase):
    def test_django_admin_login_is_not_a_way_around_the_device_check(self):
        for url in ('/admin/login/', '/admin/'):
            r = self.client.get(url, follow=True)
            self.assertEqual(r.redirect_chain[-1][0], reverse('index'), url)  # ends at the portal login
            self.assertEqual(r.status_code, 200)
        # And posting credentials straight at the admin login signs nobody in.
        self.client.post('/admin/login/', {'username': 'fr', 'password': 'pw12345!x'})
        self.assertFalse(self.signed_in())
