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

    def test_changing_your_password_stays_signed_in_in_both_portals(self):
        from django.urls import reverse
        from clergy.models import ClergyProfile
        from registrar.models import ClergyRecord
        reg = activate(get_user_model().objects.create_user('reg', password='pw12345!x', is_staff=True))
        fr = activate(get_user_model().objects.create_user('fr', password='pw12345!x'))
        ClergyProfile.objects.create(user=fr, record=ClergyRecord.objects.create(id='CLG-001', name='Fr. A'))
        for user, home in ((reg, 'registrar:dashboard'), (fr, 'clergy:dashboard')):
            c = trust_device(Client(), user)
            c.post('/', {'username': user.username, 'password': 'pw12345!x'})
            c.post(reverse('my_security'), {'action': 'change_password', 'current_password': 'pw12345!x',
                                           'new_password': 'Sturdy!Pass-2026', 'confirm_password': 'Sturdy!Pass-2026'})
            self.assertTrue(get_user_model().objects.get(pk=user.pk).check_password('Sturdy!Pass-2026'))
            self.assertEqual(c.get(reverse(home)).status_code, 200, user.username)


class LoginCsrfAndCacheTests(TestCase):
    """Double-clicking Sign In or submitting a stale login tab after signing in
    rotates the CSRF cookie on the first request; a second POST by an already
    signed-in browser must land on the dashboard rather than a 403 page, while
    unauthenticated or state-changing requests still enforce CSRF."""

    def setUp(self):
        self.user = activate(get_user_model().objects.create_user('fr_csrf', password='pw12345!x'))

    def test_auth_screens_send_no_store_cache_headers(self):
        from django.urls import reverse
        for path in ('/', reverse('verify_device'), reverse('forgot_password')):
            r = self.client.get(path)
            self.assertIn('no-store', r.headers.get('Cache-Control', ''), path)

    def test_stale_or_duplicate_login_post_when_already_signed_in_redirects_to_dashboard(self):
        from django.urls import reverse
        c = trust_device(Client(enforce_csrf_checks=True), self.user)
        login_page = c.get('/')
        stale_csrf = login_page.cookies['csrftoken'].value

        # First POST signs in and rotates csrftoken.
        first = c.post('/', {'username': 'fr_csrf', 'password': 'pw12345!x', 'csrfmiddlewaretoken': stale_csrf})
        self.assertRedirects(first, reverse('clergy:dashboard'), fetch_redirect_response=False)

        # Second POST arrives with the pre-login (now mismatched) token; since the
        # session is already signed in, it redirects to the dashboard instead of 403.
        c.cookies['csrftoken'] = 'rotated-away-token-value-1234567890'
        second = c.post('/', {'username': 'fr_csrf', 'password': 'pw12345!x', 'csrfmiddlewaretoken': stale_csrf})
        self.assertRedirects(second, reverse('clergy:dashboard'), fetch_redirect_response=False)

        # Real state-changing endpoints still reject a bad CSRF token with 403.
        bad_action = c.post(reverse('my_security'), {'action': 'revoke_others', 'csrfmiddlewaretoken': stale_csrf})
        self.assertEqual(bad_action.status_code, 403)

    def test_unauthenticated_login_post_without_csrf_still_returns_403(self):
        c = Client(enforce_csrf_checks=True)
        r = c.post('/', {'username': 'fr_csrf', 'password': 'pw12345!x'})
        self.assertEqual(r.status_code, 403)
