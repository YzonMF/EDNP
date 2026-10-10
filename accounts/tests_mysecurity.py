from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from clergy.models import ClergyProfile
from registrar.models import ClergyRecord

from . import devices
from .models import AccountAuditLog, OtpChallenge, TrustedDevice, UserSecurity
from .testing import activate, trust_device

User = get_user_model()
OLD = '+639171234567'
NEW = '+639189876543'
PASSWORD = 'pw12345!x'
URL = reverse('my_security')
CLERGY_SECURITY = reverse('clergy:my_profile') + '?tab=security'


class SecurityTestCase(TestCase):
    def setUp(self):
        cache.clear()   # (the lockout counter is in the database now, which each test rolls back)
        self.record = ClergyRecord.objects.create(id='CLG-001', name='Fr. Juan', phone=OLD)
        self.user = User.objects.create_user('fr', password=PASSWORD)
        ClergyProfile.objects.create(user=self.user, record=self.record)
        activate(self.user, phone=OLD)
        self.client = trust_device(Client(), self.user)
        self.client.post('/', {'username': 'fr', 'password': PASSWORD})

    def post(self, **data):
        return self.client.post(URL, data, follow=True)

    def send(self, phone='0918 987 6543', password=PASSWORD):
        return self.post(action='send_change_code', phone=phone, current_password=password)

    def code(self):
        return self.client.session['phone_change_test_code']

    def phone(self):
        return UserSecurity.objects.get(user=self.user).phone


class PageTests(SecurityTestCase):
    def test_clergy_sees_it_inside_their_own_portal_with_the_number_masked(self):
        page = self.client.get(CLERGY_SECURITY)
        self.assertContains(page, 'Clergy Portal')
        self.assertContains(page, '09•••••••67')
        self.assertNotContains(page, '09171234567')
        self.assertContains(page, 'Fr. Juan')   # the clergy sidebar needs the profile

    def test_registrar_sees_it_inside_the_registrar_portal(self):
        reg = activate(User.objects.create_user('reg', password=PASSWORD, is_staff=True))
        c = trust_device(Client(), reg)
        c.post('/', {'username': 'reg', 'password': PASSWORD})
        page = c.get(reverse('registrar:my_profile'))
        self.assertContains(page, 'Registrar Portal')
        self.assertContains(page, 'Mobile number')

    def test_it_needs_a_signed_in_activated_user(self):
        self.assertEqual(Client().get(URL).status_code, 302)
        provisioned = User.objects.create_user('new', password=PASSWORD)
        c = Client()
        c.force_login(provisioned)   # no security row yet -> still in setup
        self.assertRedirects(c.get(URL), reverse('activate'), fetch_redirect_response=False)

    def test_current_device_is_marked_and_expired_ones_are_hidden(self):
        TrustedDevice.objects.create(user=self.user, token_hash='e' * 64, label='Old Firefox',
                                     expires_at=timezone.now() - timedelta(days=1))
        page = self.client.get(CLERGY_SECURITY)
        self.assertContains(page, 'This device')
        self.assertNotContains(page, 'Old Firefox')


class ChangeNumberTests(SecurityTestCase):
    def test_full_change_updates_everything_and_is_logged(self):
        other_browser = TrustedDevice.objects.create(user=self.user, token_hash='b' * 64, label='Other',
                                                     expires_at=timezone.now() + timedelta(days=5))
        self.send()
        self.assertTrue(OtpChallenge.objects.filter(user=self.user, purpose='phone_change', target=NEW).exists())
        self.assertEqual(self.phone(), OLD)           # nothing changes until the code is entered
        page = self.post(action='verify_change_code', code=self.code())
        self.assertContains(page, 'has been changed')

        self.assertEqual(self.phone(), NEW)
        self.record.refresh_from_db()
        self.assertEqual(self.record.phone, NEW)
        self.assertFalse(TrustedDevice.objects.filter(pk=other_browser.pk).exists())   # old trust doesn't carry over
        self.assertEqual(TrustedDevice.objects.filter(user=self.user).count(), 1)       # the browser in use is kept
        entry = AccountAuditLog.objects.get(action='phone_changed')
        self.assertEqual((entry.actor_name, entry.target_name), ('Fr. Juan', 'Fr. Juan'))

    def test_the_new_number_is_what_future_device_codes_go_to(self):
        self.send()
        self.post(action='verify_change_code', code=self.code())
        self.client.get(reverse('logout'))
        fresh = Client()
        fresh.post('/', {'username': 'fr', 'password': PASSWORD})   # an unrecognised browser
        self.assertTrue(OtpChallenge.objects.filter(purpose='device', target=NEW).exists())

    def test_wrong_password_sends_nothing_and_counts_toward_the_lockout(self):
        for _ in range(5):
            self.send(password='wrong')
        self.assertFalse(OtpChallenge.objects.exists())
        page = self.send()   # now even the right password is refused
        self.assertContains(page, 'Too many failed attempts')
        self.assertFalse(OtpChallenge.objects.exists())

    def test_right_password_resets_the_failure_count(self):
        for _ in range(3):
            self.send(password='wrong')
        self.send()
        for _ in range(3):
            self.post(action='cancel_change')
            self.send(password='wrong')
        self.assertNotContains(self.send(), 'Too many failed attempts')

    def test_bad_same_or_taken_numbers_send_nothing(self):
        activate(User.objects.create_user('other', password=PASSWORD), phone=NEW)
        for phone in ('12345', '0917 123 4567', '+63 918 987 6543'):
            self.send(phone=phone)
        self.assertFalse(OtpChallenge.objects.exists())
        self.assertEqual(self.phone(), OLD)

    def test_wrong_code_changes_nothing(self):
        self.send()
        wrong = '000000' if self.code() != '000000' else '111111'
        self.assertContains(self.post(action='verify_change_code', code=wrong), 'incorrect')
        self.assertEqual(self.phone(), OLD)

    def test_cancel_discards_the_pending_change(self):
        self.send()
        self.post(action='cancel_change')
        self.assertNotIn('phone_change_pending', self.client.session)
        self.post(action='verify_change_code', code='123456')
        self.assertEqual(self.phone(), OLD)

    def test_verifying_without_asking_for_a_code_does_nothing(self):
        self.post(action='verify_change_code', code='123456')
        self.assertEqual(self.phone(), OLD)

    def test_resend_is_rate_limited(self):
        self.send()
        self.assertContains(self.post(action='resend_change'), 'Please wait')


class DeviceManagementTests(SecurityTestCase):
    def make_device(self, user=None, label='Edge on Windows'):
        return TrustedDevice.objects.create(user=user or self.user, token_hash=f'{label}'.ljust(64, 'x')[:64],
                                            label=label, expires_at=timezone.now() + timedelta(days=5))

    def test_remove_a_device(self):
        device = self.make_device()
        self.post(action='revoke_device', device_id=device.pk)
        self.assertFalse(TrustedDevice.objects.filter(pk=device.pk).exists())

    def test_cannot_remove_someone_elses_device_by_guessing_its_id(self):
        other = activate(User.objects.create_user('other', password=PASSWORD))
        theirs = self.make_device(user=other, label='Their phone')
        self.post(action='revoke_device', device_id=theirs.pk)
        self.assertTrue(TrustedDevice.objects.filter(pk=theirs.pk).exists())

    def test_garbage_device_id_is_harmless(self):
        self.assertEqual(self.post(action='revoke_device', device_id='abc').status_code, 200)

    def test_removing_the_current_device_means_a_code_next_time(self):
        current = devices.current_device(self._request(), self.user)
        self.post(action='revoke_device', device_id=current.pk)
        self.client.get(reverse('logout'))
        r = self.client.post('/', {'username': 'fr', 'password': PASSWORD})
        self.assertRedirects(r, reverse('verify_device'), fetch_redirect_response=False)

    def test_remove_all_others_keeps_this_browser(self):
        self.make_device(label='A')
        self.make_device(label='B')
        page = self.post(action='revoke_others')
        self.assertContains(page, 'Removed 2 other devices')
        self.assertEqual(TrustedDevice.objects.filter(user=self.user).count(), 1)
        self.assertEqual(self.client.get(reverse('clergy:dashboard')).status_code, 200)

    def _request(self):
        class R:
            COOKIES = {name: morsel.value for name, morsel in self.client.cookies.items()}
        return R()
