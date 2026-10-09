from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from .testing import activate, trust_device


class SingleSessionTests(TestCase):
    def setUp(self):
        self.user = activate(get_user_model().objects.create_user('fr_juan', password='pw12345!x', is_staff=True))

    def _login(self):
        c = trust_device(Client(), self.user)
        c.post('/', {'username': 'fr_juan', 'password': 'pw12345!x'})
        return c

    def test_new_login_signs_out_older_device_with_message(self):
        phone, laptop = self._login(), self._login()
        r = phone.get('/', follow=False)  # stale session: bounced to login w/ notice
        self.assertEqual(r.status_code, 302)
        self.assertIn('signed_out=1', r['Location'])
        self.assertContains(phone.get(r['Location']), 'another device')
        self.assertEqual(laptop.get('/').status_code, 302)  # still logged in -> redirected to dashboard
        self.assertNotIn('signed_out', laptop.get('/')['Location'])

    def test_idle_device_stays_signed_in_without_other_login(self):
        phone = self._login()
        self.assertNotIn('signed_out', phone.get('/')['Location'])

    def test_signing_in_as_a_different_user_on_an_already_signed_in_browser(self):
        # Django flushes the old session here, so there is briefly no session
        # key; the signal must cope instead of failing on a NOT NULL column.
        other = activate(get_user_model().objects.create_user('other', password='pw12345!x', is_staff=True))
        c = self._login()
        c.force_login(other)
        self.assertEqual(other.active_session.session_key, c.session.session_key)

    def test_other_users_unaffected(self):
        other = activate(get_user_model().objects.create_user('other', password='pw12345!x', is_staff=True))
        phone = self._login()
        trust_device(Client(), other).post('/', {'username': 'other', 'password': 'pw12345!x'})
        self.assertNotIn('signed_out', phone.get('/')['Location'])


class PasswordChangeKeepsSessionTests(TestCase):
    """Changing a password rotates the session key; the single-session rule
    must follow it, or the user is signed out of the device they changed it on."""

    def test_registrar_change_password_stays_signed_in(self):
        from django.urls import reverse
        reg = activate(get_user_model().objects.create_user('reg', password='pw12345!x', is_staff=True))
        c = trust_device(Client(), reg)
        c.post('/', {'username': 'reg', 'password': 'pw12345!x'})
        c.post(reverse('registrar:change_password'), {
            'current_password': 'pw12345!x', 'new_password': 'Sturdy!Pass-2026', 'confirm_password': 'Sturdy!Pass-2026'})
        self.assertTrue(get_user_model().objects.get(username='reg').check_password('Sturdy!Pass-2026'))
        self.assertEqual(c.get(reverse('registrar:dashboard')).status_code, 200)

    def test_clergy_saving_my_profile_stays_signed_in(self):
        from django.urls import reverse
        from clergy.models import ClergyProfile
        from registrar.models import ClergyRecord
        user = activate(get_user_model().objects.create_user('fr', password='pw12345!x'))
        ClergyProfile.objects.create(user=user, record=ClergyRecord.objects.create(id='CLG-001', name='Fr. A'))
        c = trust_device(Client(), user)
        c.post('/', {'username': 'fr', 'password': 'pw12345!x'})
        r = c.post(reverse('clergy:my_profile'), {'username': 'fr', 'name': 'Fr. A'})
        self.assertRedirects(r, reverse('clergy:my_profile'), fetch_redirect_response=False)
        self.assertEqual(c.get(reverse('clergy:dashboard')).status_code, 200)
