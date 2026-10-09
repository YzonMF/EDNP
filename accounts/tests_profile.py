from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from clergy.models import ClergyProfile
from registrar.models import ClergyRecord

from .models import AccountAuditLog, TrustedDevice, UserSecurity
from .testing import activate, trust_device
from .tests_mysecurity import CLERGY_SECURITY, OLD, PASSWORD, URL, SecurityTestCase

User = get_user_model()


class LayoutTests(SecurityTestCase):
    def test_get_on_the_action_endpoint_just_goes_to_the_right_page(self):
        self.assertRedirects(self.client.get(URL), CLERGY_SECURITY, fetch_redirect_response=False)
        reg = activate(User.objects.create_user('reg', password=PASSWORD, is_staff=True))
        c = trust_device(Client(), reg)
        c.post('/', {'username': 'reg', 'password': PASSWORD})
        self.assertRedirects(c.get(URL), reverse('registrar:my_profile'), fetch_redirect_response=False)

    def test_clergy_personal_tab_shows_the_number_but_holds_no_credentials(self):
        page = self.client.get(reverse('clergy:my_profile'))
        self.assertContains(page, OLD)                       # shown on Personal Information...
        self.assertContains(page, 'Change my number')        # ...but changed on the Security tab
        self.assertNotContains(page, 'name="username"')
        self.assertNotContains(page, 'name="new_password"')
        self.assertNotContains(page, 'Trusted devices')

    def test_clergy_security_tab_has_everything_and_no_personal_form(self):
        page = self.client.get(CLERGY_SECURITY)
        for text in ('Username &amp; Password', 'Mobile number', 'Trusted devices'):
            self.assertContains(page, text)
        self.assertNotContains(page, 'name="date_of_birth"')

    def test_saving_personal_information_leaves_credentials_and_session_alone(self):
        r = self.client.post(reverse('clergy:my_profile'), {
            'name': 'Fr. Juan Dela Cruz', 'gender': 'Male', 'date_of_birth': '1980-01-01',
            'baptism_date': '1981-01-01', 'confirmation_date': '1995-01-01',
            'address': 'Baguio City', 'email': 'juan@example.org', 'username': 'hacker', 'password': 'x'})
        self.assertRedirects(r, reverse('clergy:my_profile'), fetch_redirect_response=False)
        self.record.refresh_from_db()
        self.assertEqual(self.record.name, 'Fr. Juan Dela Cruz')
        self.assertEqual(self.record.phone, OLD)             # the number can't be changed from here
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, 'fr')           # nor the username
        self.assertTrue(self.user.check_password(PASSWORD))  # nor the password
        self.assertEqual(self.client.get(reverse('clergy:dashboard')).status_code, 200)


class UsernameTests(SecurityTestCase):
    def change(self, new, password=PASSWORD):
        return self.post(action='change_username', username=new, current_password=password)

    def test_change_username_logs_it_and_keeps_the_session(self):
        self.change('fr.juan')
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, 'fr.juan')
        entry = AccountAuditLog.objects.get(action='username_changed')
        self.assertEqual((entry.target_name, entry.note), ('Fr. Juan', 'fr → fr.juan'))
        self.assertEqual(self.client.get(reverse('clergy:dashboard')).status_code, 200)

    def test_needs_the_current_password(self):
        self.change('fr.juan', password='wrong')
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, 'fr')

    def test_taken_invalid_empty_and_unchanged_names_are_refused(self):
        User.objects.create_user('Taken', password=PASSWORD)
        for bad in ('taken', 'has space', 'x' * 151, '', 'fr'):
            self.change(bad)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, 'fr')
        self.assertFalse(AccountAuditLog.objects.exists())


class PasswordTests(SecurityTestCase):
    NEW = 'Sturdy!Pass-2026'

    def change(self, new=NEW, confirm=None, current=PASSWORD):
        return self.post(action='change_password', current_password=current, new_password=new, confirm_password=confirm or new)

    def test_change_password_keeps_the_session_and_the_device_trust(self):
        self.change()
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.NEW))
        self.assertEqual(self.client.get(reverse('clergy:dashboard')).status_code, 200)   # still signed in
        self.assertEqual(TrustedDevice.objects.filter(user=self.user).count(), 1)
        self.assertIsNotNone(UserSecurity.objects.get(user=self.user).password_changed_at)

    def test_each_mistake_is_refused_without_changing_anything(self):
        self.change(current='wrong')
        self.change(confirm='Different!Pass-1')
        self.change(new=PASSWORD, confirm=PASSWORD)     # same as current
        self.change(new='short', confirm='short')
        self.change(new='12345678', confirm='12345678')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_guessing_the_current_password_here_hits_the_sign_in_lockout(self):
        for _ in range(5):
            self.change(current='wrong')
        self.assertContains(self.change(), 'Too many failed attempts')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))


class RegistrarProfileTests(TestCase):
    def setUp(self):
        cache.clear()
        self.reg = activate(User.objects.create_user('reg', password=PASSWORD, is_staff=True))
        self.client = trust_device(Client(), self.reg)
        self.client.post('/', {'username': 'reg', 'password': PASSWORD})

    def test_one_page_replaces_change_password_and_my_security(self):
        page = self.client.get(reverse('registrar:my_profile'))
        for text in ('Full name', 'Username &amp; Password', 'Mobile number', 'Trusted devices', 'My Profile'):
            self.assertContains(page, text)
        self.assertNotContains(page, 'Change Password</a>')
        self.assertNotContains(page, 'My Security</a>')

    def test_the_old_change_password_address_goes_to_my_profile(self):
        self.assertRedirects(self.client.get(reverse('registrar:change_password')),
                             reverse('registrar:my_profile'), fetch_redirect_response=False)

    def test_name_change_is_logged_with_old_and_new_and_flows_into_reports(self):
        self.client.post(URL, {'action': 'update_name', 'name': 'Rev. Ana Reyes'})
        self.reg.refresh_from_db()
        self.assertEqual(self.reg.get_full_name(), 'Rev. Ana Reyes')
        entry = AccountAuditLog.objects.get(action='name_changed')
        self.assertEqual((entry.actor_name, entry.note), ('Rev. Ana Reyes', 'Test User → Rev. Ana Reyes'))
        from registrar import reports
        self.assertEqual(reports.build_report('status_report', generated_by=self.reg)['generated_by'], 'Rev. Ana Reyes')

    def test_blank_or_unchanged_name_is_ignored(self):
        self.client.post(URL, {'action': 'update_name', 'name': '   '})
        self.client.post(URL, {'action': 'update_name', 'name': 'Test User'})
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_clergy_cannot_use_the_name_action(self):
        record = ClergyRecord.objects.create(id='CLG-009', name='Fr. Real')
        user = User.objects.create_user('fr2', password=PASSWORD)
        ClergyProfile.objects.create(user=user, record=record)
        activate(user)
        c = trust_device(Client(), user)
        c.post('/', {'username': 'fr2', 'password': PASSWORD})
        c.post(URL, {'action': 'update_name', 'name': 'Someone Else'})
        user.refresh_from_db()
        self.assertEqual(user.first_name, '')
        self.assertFalse(AccountAuditLog.objects.exists())


class LoneRegistrarTests(TestCase):
    def test_a_lone_registrar_sees_only_the_activity_log(self):
        reg = activate(User.objects.create_user('reg', password=PASSWORD, is_staff=True))
        self.client.force_login(reg)
        page = self.client.get(reverse('registrar:account_security'))
        self.assertNotContains(page, 'Registrar Accounts')
        self.assertContains(page, 'Recent Account Activity')

    def test_the_registrar_list_appears_once_there_is_someone_to_recover(self):
        reg = activate(User.objects.create_user('reg', password=PASSWORD, is_staff=True))
        activate(User.objects.create_user('reg2', password=PASSWORD, is_staff=True))
        self.client.force_login(reg)
        self.assertContains(self.client.get(reverse('registrar:account_security')), 'Registrar Accounts')
