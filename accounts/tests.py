from django.contrib.auth import get_user_model
from django.test import Client, TestCase


class SingleSessionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('fr_juan', password='pw12345!x', is_staff=True)

    def _login(self):
        c = Client()
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

    def test_other_users_unaffected(self):
        get_user_model().objects.create_user('other', password='pw12345!x', is_staff=True)
        phone = self._login()
        Client().post('/', {'username': 'other', 'password': 'pw12345!x'})
        self.assertNotIn('signed_out', phone.get('/')['Location'])
