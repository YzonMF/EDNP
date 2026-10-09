"""The code-entry screens: Send turns into Resend inside the field, the number is
masked, and a number can't be handed to two people at once."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from registrar.models import ClergyRecord
from registrar.provisioning import provision_account

from . import otp
from .models import OtpChallenge, UserSecurity
from .phone import display, mask
from .testing import activate
from .tests_mysecurity import CLERGY_SECURITY, OLD, PASSWORD, SecurityTestCase
from .views import TEST_CODE_KEY

User = get_user_model()
NUMBER = '0917 123 4567'


def new_clergy(n):
    record = ClergyRecord.objects.create(id=f'CLG-{n:03d}', name=f'Fr. {n}')
    user, password = provision_account(record)
    client = Client()
    client.post('/', {'username': user.username, 'password': password})
    return user, client


def send(client, phone=NUMBER, name='Fr. X'):
    return client.post(reverse('activate'), {'action': 'send_code', 'name': name, 'phone': phone}, follow=True)


def messages_of(page):
    return [m.message for m in page.context['messages']]


class MaskTests(TestCase):
    def test_only_the_first_two_and_last_two_digits_show(self):
        self.assertEqual(mask('+639171234567'), '09•••••••67')
        self.assertEqual(display('+639171234567'), '0917 123 4567')

    def test_the_sent_to_line_never_prints_the_middle_digits(self):
        cache.clear()
        _, client = new_clergy(1)
        page = send(client)
        self.assertContains(page, 'Code sent to <b>09•••••••67</b>')
        self.assertNotContains(page, 'sent to <b>0917')


class ButtonStateTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user, self.client = new_clergy(1)
        self.url = reverse('activate')

    def test_before_a_code_is_sent_the_button_says_send_and_nothing_can_be_verified(self):
        page = self.client.get(self.url)
        html = page.content.decode()
        self.assertRegex(html, r'class="code-send"[^>]*>\s*Send code\s*</button>')   # the button's own label
        self.assertContains(page, 'value="send_code"')
        self.assertNotContains(page, 'value="resend"')
        self.assertNotContains(page, 'Back</button>')
        self.assertIn('Send a code first', html)
        self.assertRegex(html, r'<input id="code"[^>]*\bdisabled\b')                  # no code field to fill yet
        self.assertRegex(html, r'<button class="btn" type="submit" name="action" value="verify_code" disabled')

    def test_after_sending_it_turns_into_a_greyed_resend_with_a_countdown(self):
        page = send(self.client)
        html = page.content.decode()
        self.assertContains(page, 'value="resend"')
        self.assertRegex(html, r'Resend in \d+s')
        self.assertRegex(html, r'class="code-send"[^>]*\bdisabled\b')
        self.assertContains(page, 'Back')
        self.assertRegex(html, r'name="name"[^>]*\breadonly\b')    # what the code was sent for can't drift
        self.assertRegex(html, r'name="phone"[^>]*\breadonly\b')

    def test_resend_is_clickable_once_the_wait_is_over(self):
        send(self.client)
        OtpChallenge.objects.update(created_at=timezone.now() - timedelta(seconds=90))
        html = self.client.get(self.url).content.decode()
        self.assertNotRegex(html, r'class="code-send"[^>]*\bdisabled\b')
        self.assertRegex(html, r'class="code-send"[^>]*>\s*Resend\s*</button>')

    def test_pressing_enter_never_triggers_a_text(self):
        # The browser submits a form with its FIRST submit button, so a hidden one that
        # verifies (or, before any code, sends) has to come before the in-field button.
        html = self.client.get(self.url).content.decode()
        self.assertLess(html.index('default-submit" name="action" value="send_code"'), html.index('class="code-send"'))
        html = send(self.client).content.decode()
        self.assertLess(html.index('default-submit" name="action" value="verify_code"'), html.index('class="code-send"'))

    def test_verifying_before_sending_says_to_send_first(self):
        page = self.client.post(self.url, {'action': 'verify_code', 'code': '123456'}, follow=True)
        self.assertIn('Press Send code first, then enter the code we text you.', messages_of(page))

    def test_no_template_comment_leaks_onto_any_code_screen(self):
        # A multi-line {# #} comment printed itself onto the page once.
        pages = [send(self.client).content.decode()]
        other = activate(User.objects.create_user('fr', password=PASSWORD), phone='+639181112233')
        c = Client()
        c.post('/', {'username': 'fr', 'password': PASSWORD})
        pages.append(c.get(reverse('verify_device')).content.decode())
        for html in pages:
            self.assertNotIn('{#', html)
            self.assertNotIn('{%', html)
            self.assertNotIn('#}', html)

    def test_the_sign_in_code_screen_has_an_in_field_resend_with_a_countdown(self):
        activate(User.objects.create_user('fr', password=PASSWORD), phone='+639181112233')
        c = Client()
        c.post('/', {'username': 'fr', 'password': PASSWORD})
        html = c.get(reverse('verify_device')).content.decode()
        self.assertRegex(html, r'Resend in \d+s')
        self.assertIn('value="resend"', html)
        self.assertLess(html.index('default-submit" name="action" value="verify_code"'), html.index('class="code-send"'))


class NumberHeldTests(TestCase):
    """Two people mid-setup must not both be texted the same number."""

    def setUp(self):
        cache.clear()
        self.a, self.ca = new_clergy(1)
        self.b, self.cb = new_clergy(2)

    def test_a_number_someone_else_is_registering_is_refused_before_any_text_goes_out(self):
        send(self.ca)
        with mock.patch('accounts.sms._send_via_semaphore') as sent:
            page = send(self.cb)
        self.assertIn('being set up on another account right now', ' '.join(messages_of(page)))
        self.assertFalse(OtpChallenge.objects.filter(user=self.b).exists())     # nothing sent, no allowance spent

    def test_going_back_frees_the_number_for_others_at_once(self):
        send(self.ca)
        self.ca.post(reverse('activate'), {'action': 'change_number'})
        page = send(self.cb)
        self.assertContains(page, 'Code sent to')

    def test_an_abandoned_code_stops_holding_the_number_when_it_expires(self):
        send(self.ca)
        OtpChallenge.objects.filter(user=self.a).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertContains(send(self.cb), 'Code sent to')

    def test_a_used_up_code_stops_holding_it_too(self):
        send(self.ca)
        for _ in range(3):
            self.ca.post(reverse('activate'), {'action': 'verify_code', 'code': '000000'})
        self.assertContains(send(self.cb), 'Code sent to')

    def test_once_the_first_person_verifies_the_second_is_told_it_is_registered(self):
        send(self.ca)
        self.ca.post(reverse('activate'), {'action': 'verify_code', 'code': self.ca.session[TEST_CODE_KEY]})
        page = send(self.cb)
        self.assertIn('That number is already registered to another account.', messages_of(page))

    def test_your_own_pending_code_never_blocks_you(self):
        send(self.ca)
        OtpChallenge.objects.update(created_at=timezone.now() - timedelta(seconds=90))
        page = self.ca.post(reverse('activate'), {'action': 'resend'}, follow=True)
        self.assertNotIn('being set up', ' '.join(messages_of(page)))

    def test_different_formats_of_the_same_number_are_the_same_number(self):
        send(self.ca, '0917 123 4567')
        page = send(self.cb, '+63 917 123 4567')
        self.assertIn('being set up on another account right now', ' '.join(messages_of(page)))

    def test_number_unavailable_is_none_for_a_free_number(self):
        self.assertIsNone(otp.number_unavailable(self.a, '+639170000000'))


class ChangeNumberScreenTests(SecurityTestCase):
    NEW_NUMBER = '0918 765 4321'

    def test_unsent_state_has_a_send_button_in_the_code_field(self):
        html = self.client.get(CLERGY_SECURITY).content.decode()
        self.assertIn('value="send_change_code"', html)
        self.assertIn('Send a code first', html)
        self.assertNotIn('{#', html)
        self.assertLess(html.index('default-submit" name="action" value="send_change_code"'), html.index('class="code-send"'))

    def test_sent_state_shows_resend_countdown_and_the_masked_number(self):
        self.send(self.NEW_NUMBER)
        page = self.client.get(CLERGY_SECURITY)
        html = page.content.decode()
        self.assertRegex(html, r'Resend in \d+s')
        self.assertIn('value="resend_change"', html)
        self.assertContains(page, 'Code sent to <b>09•••••••21</b>')
        self.assertRegex(html, r'name="phone"[^>]*\breadonly\b')
        self.assertContains(page, 'name="action" value="cancel_change"')

    def test_a_number_held_by_someone_mid_setup_is_refused_here_too(self):
        other, other_client = new_clergy(7)
        send(other_client, self.NEW_NUMBER)
        self.send(self.NEW_NUMBER)
        self.assertFalse(OtpChallenge.objects.filter(user=self.user, purpose='phone_change').exists())

    def test_cancelling_frees_the_number(self):
        self.send(self.NEW_NUMBER)
        self.post(action='cancel_change')
        other, other_client = new_clergy(7)
        self.assertContains(send(other_client, self.NEW_NUMBER), 'Code sent to')

    def test_confirming_before_sending_says_to_send_first(self):
        page = self.client.post(reverse('my_security'), {'action': 'verify_change_code', 'code': '123456'}, follow=True)
        self.assertIn('Press Send code first, then enter the code we text you.', messages_of(page))


class VerifyAgainScreenTests(TestCase):
    """Someone who has to verify again (e.g. their number was only 'verified' in test
    mode) must type the number in. It is never pre-filled, because pre-filling meant a
    single press of Send texted a number nobody had confirmed, and burned an attempt."""

    def setUp(self):
        cache.clear()
        self.user = activate(User.objects.create_user('reg', password=PASSWORD, is_staff=True), phone='+639171234567')
        UserSecurity.objects.filter(user=self.user).update(activated_at=None, password_changed_at=None, phone_verified_at=None)
        self.client = Client()
        self.client.force_login(self.user)

    def test_the_number_saved_earlier_is_not_pre_filled(self):
        page = self.client.get(reverse('activate'))
        self.assertTrue(page.context['reverifying'])
        self.assertEqual(page.context['phone'], '')
        self.assertRegex(page.content.decode(), r'name="phone"[^>]*value=""')      # the box really is empty
        self.assertNotContains(page, '09171234567')

    def test_nothing_is_sent_until_a_number_is_typed_and_sent(self):
        self.client.get(reverse('activate'))
        self.assertFalse(OtpChallenge.objects.exists())

    def test_the_number_just_typed_is_shown_back_once_a_code_is_out(self):
        page = send(self.client, '0918 765 4321', name='Rev. Reg')
        self.assertEqual(page.context['phone'], '0918 765 4321')
