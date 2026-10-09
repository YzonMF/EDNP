from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from registrar.models import ClergyRecord
from registrar.provisioning import provision_account

from . import setup
from .models import OtpChallenge, UserSecurity
from .testing import activate
from .views import TEST_CODE_KEY

User = get_user_model()

NEW_PASSWORD = 'Sturdy!Pass-2026'


class ActivationTestCase(TestCase):
    def setUp(self):
        self.record = ClergyRecord.objects.create(id='CLG-001', name='Fr. Registrar Typed')
        self.user, self.temp_password = provision_account(self.record)
        self.client = Client()
        self.client.post('/', {'username': 'CLG-001', 'password': self.temp_password})

    def send_code(self, name='Rev. Juan Dela Cruz', phone='0917 123 4567'):
        return self.client.post(reverse('activate'), {'action': 'send_code', 'name': name, 'phone': phone}, follow=True)

    def code(self):
        return self.client.session[TEST_CODE_KEY]

    def verify(self, code=None):
        return self.client.post(reverse('activate'), {'action': 'verify_code', 'code': code or self.code()}, follow=True)

    def set_password(self, new=NEW_PASSWORD, confirm=None):
        return self.client.post(reverse('activate'), {
            'action': 'set_password', 'new_password': new, 'confirm_password': confirm or new}, follow=True)

    def finish_phone_step(self):
        self.send_code()
        self.verify()


class GateTests(ActivationTestCase):
    def test_unactivated_user_is_sent_to_setup_from_every_page(self):
        for url in (reverse('clergy:dashboard'), reverse('clergy:my_requirements'), '/clergy/', '/', '/registrar/dashboard/'):
            r = self.client.get(url)
            self.assertRedirects(r, reverse('activate'), fetch_redirect_response=False, msg_prefix=url)

    def test_setup_and_logout_stay_reachable(self):
        self.assertEqual(self.client.get(reverse('activate')).status_code, 200)
        self.assertEqual(self.client.get(reverse('logout')).status_code, 302)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_activated_user_is_not_gated_and_setup_bounces_home(self):
        activate(User.objects.create_user('done', password='pw12345!x'))
        c = Client()
        c.force_login(User.objects.get(username='done'))
        self.assertEqual(c.get(reverse('clergy:dashboard')).status_code, 200)
        self.assertRedirects(c.get(reverse('activate')), reverse('clergy:dashboard'), fetch_redirect_response=False)

    def test_account_with_no_security_row_is_gated_too(self):
        # e.g. created before this feature, or by createsuperuser
        legacy = User.objects.create_user('legacy', password='pw12345!x', is_staff=True)
        c = Client()
        c.force_login(legacy)
        self.assertRedirects(c.get('/registrar/dashboard/'), reverse('activate'), fetch_redirect_response=False)


class FullFlowTests(ActivationTestCase):
    def test_happy_path_activates_the_account(self):
        page = self.send_code()
        self.assertContains(page, '09•••••••67')       # shows where the code went, masked
        self.assertContains(page, self.code())            # test mode shows the code on screen
        self.assertTrue(OtpChallenge.objects.filter(user=self.user, target='+639171234567').exists())

        page = self.verify()
        self.assertContains(page, 'Choose your own password')

        page = self.set_password()
        self.assertContains(page, 'Account activated')
        self.assertEqual(page.redirect_chain[-1][0], reverse('clergy:dashboard'))

        security = UserSecurity.objects.get(user=self.user)
        self.assertTrue(security.is_activated)
        self.assertIsNone(security.setup_expires_at)      # the 30-day clock stops applying
        self.assertEqual(security.phone, '+639171234567')
        self.assertEqual(security.phone_verified_via, 'test')

        self.record.refresh_from_db()
        self.assertEqual(self.record.name, 'Rev. Juan Dela Cruz')   # confirmed by the clergy member
        self.assertEqual(self.record.phone, '+639171234567')

        self.assertEqual(self.client.get(reverse('clergy:dashboard')).status_code, 200)  # session survived the change
        self.assertIsNotNone(Client().post('/', {'username': 'CLG-001', 'password': NEW_PASSWORD}))
        self.assertTrue(User.objects.get(pk=self.user.pk).check_password(NEW_PASSWORD))
        self.assertFalse(User.objects.get(pk=self.user.pk).check_password(self.temp_password))

    def test_abandoning_midway_resumes_at_the_right_step(self):
        self.finish_phone_step()
        again = Client()
        again.post('/', {'username': 'CLG-001', 'password': self.temp_password})
        page = again.get(reverse('activate'))
        self.assertContains(page, 'Choose your own password')   # not back at the phone step
        self.assertEqual(setup.pending_stage(self.user), setup.PASSWORD)

    def test_password_step_cannot_be_reached_or_forced_before_the_phone_is_verified(self):
        r = self.set_password()
        self.assertFalse(UserSecurity.objects.get(user=self.user).is_activated)
        self.assertFalse(User.objects.get(pk=self.user.pk).check_password(NEW_PASSWORD))
        self.assertContains(r, 'Send code')

    def test_cannot_verify_without_a_code_having_been_sent(self):
        self.client.post(reverse('activate'), {'action': 'verify_code', 'code': '123456'})
        self.assertFalse(UserSecurity.objects.filter(user=self.user, phone__isnull=False).exists())

    def test_wrong_code_does_not_verify(self):
        self.send_code()
        wrong = '000000' if self.code() != '000000' else '111111'
        page = self.verify(wrong)
        self.assertContains(page, 'incorrect')
        self.assertFalse(UserSecurity.objects.filter(user=self.user, phone__isnull=False).exists())

    def test_the_number_verified_is_the_number_the_code_was_sent_to(self):
        self.send_code(phone='0917 123 4567')
        # A forged verify request naming another number is ignored: the number
        # comes from the session, not the form.
        self.client.post(reverse('activate'), {'action': 'verify_code', 'code': self.code(), 'phone': '0918 999 9999'})
        self.assertEqual(UserSecurity.objects.get(user=self.user).phone, '+639171234567')

    def test_change_number_discards_the_pending_one(self):
        self.send_code()
        self.client.post(reverse('activate'), {'action': 'change_number'})
        self.assertNotIn(TEST_CODE_KEY, self.client.session)
        self.assertContains(self.client.get(reverse('activate')), 'Send code')


class ValidationTests(ActivationTestCase):
    def test_name_is_required_and_must_look_like_a_name(self):
        for bad in ('', '   ', '12345', 'x' * 200):
            page = self.send_code(name=bad)
            self.assertFalse(OtpChallenge.objects.exists(), bad)

    def test_bad_phone_numbers_send_nothing(self):
        for bad in ('', '12345', '0817 123 4567'):
            self.send_code(phone=bad)
        self.assertFalse(OtpChallenge.objects.exists())

    def test_one_number_belongs_to_one_account_whatever_the_formatting(self):
        other = activate(User.objects.create_user('other', password='pw12345!x'), phone='+639171234567')
        page = self.send_code(phone='+63 917-123-4567')
        self.assertContains(page, 'already registered to another account')
        self.assertFalse(OtpChallenge.objects.filter(user=self.user).exists())  # no text was sent
        self.assertEqual(UserSecurity.objects.get(user=other).phone, '+639171234567')

    def test_you_can_reverify_your_own_number(self):
        UserSecurity.objects.filter(user=self.user).update(phone='+639171234567')
        page = self.send_code()
        self.assertNotContains(page, 'already registered')

    def test_password_rules(self):
        self.finish_phone_step()
        for new, confirm, fragment in [
            (NEW_PASSWORD, 'Different!Pass-1', 'do not match'),
            (self.temp_password, self.temp_password, 'different password'),
            ('short', 'short', 'too short'),
            ('12345678', '12345678', 'entirely numeric'),
        ]:
            page = self.set_password(new, confirm)
            self.assertContains(page, fragment)
            self.assertFalse(UserSecurity.objects.get(user=self.user).is_activated, new)

    def test_resend_is_rate_limited(self):
        self.send_code()
        page = self.client.post(reverse('activate'), {'action': 'resend'}, follow=True)
        self.assertContains(page, 'Please wait')


class RegistrarSetupTests(TestCase):
    """Registrar staff have no clergy record, but they still need a real name
    (it is what reports say they were 'Generated by') and a verified number."""

    def setUp(self):
        self.staff = User.objects.create_user('reg', password='pw12345!x', is_staff=True)
        self.client = Client()
        self.client.force_login(self.staff)

    def test_registrar_gives_a_name_and_number_and_their_own_password(self):
        page = self.client.get(reverse('activate'))
        self.assertContains(page, 'name="name"')   # the name step applies to them too
        self.client.post(reverse('activate'), {'action': 'send_code', 'name': 'Rev. Ana Reyes', 'phone': '0917 555 0000'})
        self.client.post(reverse('activate'), {'action': 'verify_code', 'code': self.client.session[TEST_CODE_KEY]})
        r = self.client.post(reverse('activate'), {'action': 'set_password', 'new_password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD})
        self.assertRedirects(r, reverse('registrar:dashboard'), fetch_redirect_response=False)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.get_full_name(), 'Rev. Ana Reyes')
        self.assertTrue(UserSecurity.objects.get(user=self.staff).is_activated)

    def test_the_name_cannot_be_left_blank(self):
        self.client.post(reverse('activate'), {'action': 'send_code', 'name': '  ', 'phone': '0917 555 0000'})
        self.assertFalse(OtpChallenge.objects.exists())

    def test_a_staff_account_activated_without_a_name_is_sent_back_for_one(self):
        activate(self.staff)
        self.staff.first_name = ''
        self.staff.save()
        self.assertEqual(setup.pending_stage(self.staff), setup.PHONE)

    def test_reports_say_who_generated_them(self):
        activate(self.staff)
        self.staff.first_name = 'Rev. Ana Reyes'
        self.staff.save()
        from registrar import reports
        self.assertEqual(reports.build_report('status_report', generated_by=self.staff)['generated_by'], 'Rev. Ana Reyes')


class TestModeNumbersMustBeReverifiedForRealTests(TestCase):
    def test_number_verified_in_test_mode_is_not_trusted_once_real_sms_is_on(self):
        user = activate(User.objects.create_user('u', password='pw12345!x'))  # verified_via='test'
        self.assertIsNone(setup.pending_stage(user))
        with override_settings(SMS_BACKEND='semaphore', SEMAPHORE_API_KEY='k'):
            self.assertEqual(setup.pending_stage(user), setup.PHONE)
        UserSecurity.objects.filter(user=user).update(phone_verified_via='sms')
        with override_settings(SMS_BACKEND='semaphore', SEMAPHORE_API_KEY='k'):
            self.assertIsNone(setup.pending_stage(user))


class ActivationTrustsTheDeviceTests(ActivationTestCase):
    def test_finishing_setup_trusts_this_browser_so_the_next_sign_in_needs_no_code(self):
        from . import devices
        from .models import TrustedDevice

        self.send_code()
        self.verify()
        r = self.client.post(reverse('activate'), {
            'action': 'set_password', 'new_password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD})
        self.assertIn(devices.cookie_name(self.user), r.cookies)
        self.assertEqual(TrustedDevice.objects.filter(user=self.user).count(), 1)

        self.client.get(reverse('logout'))
        r = self.client.post('/', {'username': 'CLG-001', 'password': NEW_PASSWORD})
        self.assertRedirects(r, reverse('clergy:dashboard'), fetch_redirect_response=False)


class ProgressBarTests(ActivationTestCase):
    def test_step_follows_where_the_user_is(self):
        self.assertEqual(self.client.get(reverse('activate')).context['step'], 1)      # details
        self.send_code()
        self.assertEqual(self.client.get(reverse('activate')).context['step'], 2)      # verify code
        self.verify()
        page = self.client.get(reverse('activate'))
        self.assertEqual(page.context['step'], 3)                                      # new password
        self.assertContains(page, '3 · New password')
