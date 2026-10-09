import io
from datetime import timedelta

from django.contrib.auth import authenticate, get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts import setup
from accounts.models import AccountAuditLog, OtpChallenge, TrustedDevice, UserSecurity
from accounts.testing import activate, trust_device
from clergy.models import ClergyProfile

from .models import ClergyRecord
from .provisioning import provision_account, reset_lost_phone

User = get_user_model()
PHONE = '+639171234567'


def make_activated_clergy(username='CLG-001', record_id='CLG-001', name='Fr. Juan'):
    record = ClergyRecord.objects.create(id=record_id, name=name, phone=PHONE)
    user = User.objects.create_user(username, password='their-own-pw-1')
    ClergyProfile.objects.create(user=user, record=record)
    activate(user, phone=PHONE)
    return user, record


class ResetLostPhoneTests(TestCase):
    def setUp(self):
        self.user, self.record = make_activated_clergy()

    def test_account_goes_back_to_brand_new(self):
        trust_device(Client(), self.user)
        OtpChallenge.objects.create(user=self.user, purpose='device', target=PHONE, code_hash='x',
                                    expires_at=timezone.now() + timedelta(minutes=5))
        password = reset_lost_phone(self.user)

        security = UserSecurity.objects.get(user=self.user)
        self.assertIsNone(security.phone)
        self.assertIsNone(security.phone_verified_at)
        self.assertIsNone(security.activated_at)
        self.assertIsNone(security.password_changed_at)
        self.assertGreater(security.setup_expires_at, timezone.now())
        self.assertFalse(TrustedDevice.objects.filter(user=self.user).exists())
        self.assertFalse(OtpChallenge.objects.filter(user=self.user).exists())
        self.record.refresh_from_db()
        self.assertIsNone(self.record.phone)
        self.assertEqual(setup.pending_stage(self.user), setup.PHONE)
        self.assertEqual(authenticate(username='CLG-001', password=password), self.user)

    def test_the_old_password_dies_with_the_number(self):
        reset_lost_phone(self.user)
        self.assertIsNone(authenticate(username='CLG-001', password='their-own-pw-1'))

    def test_the_freed_number_can_belong_to_someone_else(self):
        reset_lost_phone(self.user)
        other = activate(User.objects.create_user('other', password='pw12345!x'), phone=PHONE)  # would violate unique if kept
        self.assertEqual(UserSecurity.objects.get(user=other).phone, PHONE)

    def test_open_sessions_and_old_devices_are_shut_out_and_recovery_runs_through_setup(self):
        laptop = trust_device(Client(), self.user)
        laptop.post('/', {'username': 'CLG-001', 'password': 'their-own-pw-1'})
        self.assertEqual(laptop.get(reverse('clergy:dashboard')).status_code, 200)

        password = reset_lost_phone(self.user)

        # The open session is gone: the new password invalidates every session made with the old one.
        self.assertRedirects(laptop.get(reverse('clergy:dashboard')), '/?next=/clergy/dashboard/', fetch_redirect_response=False)
        stale = laptop.post('/', {'username': 'CLG-001', 'password': 'their-own-pw-1'})
        self.assertContains(stale, 'Invalid username or password')                            # old password is dead

        # The owner arrives with the new slip. Their old trusted-device cookie is
        # worthless and no number is on file, so they go straight into setup.
        r = laptop.post('/', {'username': 'CLG-001', 'password': password})
        self.assertRedirects(r, reverse('clergy:dashboard'), fetch_redirect_response=False)
        self.assertRedirects(laptop.get(reverse('clergy:dashboard')), reverse('activate'), fetch_redirect_response=False)
        self.assertFalse(OtpChallenge.objects.filter(purpose='device').exists())


class RegistrarResetViewTests(TestCase):
    def setUp(self):
        self.registrar = activate(User.objects.create_user('reg', password='pw12345!x', is_staff=True))
        self.registrar.first_name = 'Rev. Ana Reyes'
        self.registrar.save()
        self.client.force_login(self.registrar)
        self.user, self.record = make_activated_clergy()
        self.url = reverse('registrar:reset_account', args=[self.record.id])

    def test_lost_phone_reset_prints_a_slip_and_is_logged_with_the_optional_note(self):
        r = self.client.post(self.url, {'kind': 'lost_phone', 'note': 'phone lost, ID checked'})
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertEqual(r['Cache-Control'], 'no-store')
        entry = AccountAuditLog.objects.get()
        self.assertEqual((entry.actor_name, entry.target_name, entry.action, entry.note),
                         ('Rev. Ana Reyes', 'Fr. Juan', 'lost_phone_reset', 'phone lost, ID checked'))
        self.assertIsNone(UserSecurity.objects.get(user=self.user).phone)

    def test_the_note_is_optional(self):
        self.client.post(self.url, {'kind': 'lost_phone'})
        self.assertEqual(AccountAuditLog.objects.get().note, '')

    def test_plain_reprint_is_refused_for_an_activated_account(self):
        # A stale page can't turn a reprint into a full reset by accident.
        r = self.client.post(self.url, {'kind': 'reprint'}, follow=True)
        self.assertContains(r, 'already activated')
        self.assertEqual(UserSecurity.objects.get(user=self.user).phone, PHONE)
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_reprint_for_a_pending_account_is_logged_too(self):
        record = ClergyRecord.objects.create(id='CLG-002', name='Fr. Pending')
        provision_account(record)
        self.client.post(reverse('registrar:reset_account', args=[record.id]), {'kind': 'reprint'})
        self.assertEqual(AccountAuditLog.objects.get().action, 'password_reprinted')

    def test_clergy_cannot_reset_anyone(self):
        c = Client()
        c.force_login(self.user)
        r = c.post(self.url, {'kind': 'lost_phone'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(UserSecurity.objects.get(user=self.user).phone, PHONE)
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_creating_accounts_is_logged_with_the_clergy_members_name(self):
        self.client.post(reverse('registrar:create_record'), {'name': 'Fr. New', 'status': 'Active', 'rank': 'priest'})
        entry = AccountAuditLog.objects.get(action='account_created')
        self.assertEqual((entry.actor_name, entry.target_name), ('Rev. Ana Reyes', 'Fr. New'))


class RegistrarRecoversRegistrarTests(TestCase):
    def setUp(self):
        self.ana = activate(User.objects.create_user('ana', password='pw12345!x', is_staff=True))
        self.ana.first_name = 'Rev. Ana Reyes'
        self.ana.save()
        self.ben = activate(User.objects.create_user('ben', password='pw12345!x', is_staff=True))
        self.ben.first_name = 'Rev. Ben Cruz'
        self.ben.save()
        self.client.force_login(self.ana)

    def reset_url(self, user):
        return reverse('registrar:reset_staff_account', args=[user.pk])

    def test_one_registrar_can_recover_another(self):
        r = self.client.post(self.reset_url(self.ben), {'note': 'lost phone'})
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertIsNone(UserSecurity.objects.get(user=self.ben).phone)
        entry = AccountAuditLog.objects.get()
        self.assertEqual((entry.actor_name, entry.target_name, entry.note), ('Rev. Ana Reyes', 'Rev. Ben Cruz', 'lost phone'))

    def test_nobody_can_reset_their_own_account(self):
        r = self.client.post(self.reset_url(self.ana), follow=True)
        self.assertContains(r, "reset your own account")
        self.assertTrue(UserSecurity.objects.get(user=self.ana).is_activated)
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_only_staff_accounts_can_be_reset_this_way(self):
        clergy, _ = make_activated_clergy()
        self.assertEqual(self.client.post(self.reset_url(clergy)).status_code, 404)

    def test_clergy_cannot_reach_it(self):
        clergy, _ = make_activated_clergy()
        c = Client()
        c.force_login(clergy)
        self.assertEqual(c.post(self.reset_url(self.ben)).status_code, 302)
        self.assertTrue(UserSecurity.objects.get(user=self.ben).is_activated)

    def test_page_lists_registrars_and_recent_activity(self):
        self.client.post(self.reset_url(self.ben), {'note': 'lost phone'})
        page = self.client.get(reverse('registrar:account_security'))
        self.assertContains(page, 'Rev. Ben Cruz')
        self.assertContains(page, 'Account reset (lost phone)')
        self.assertContains(page, '(you)')


class ResetCommandTests(TestCase):
    def test_recovers_the_only_registrar_from_the_server(self):
        reg = activate(User.objects.create_user('reg', password='pw12345!x', is_staff=True))
        out = io.StringIO()
        call_command('reset_account', 'reg', note='lone registrar, phone lost', stdout=out)
        password = next(line.split(': ')[1] for line in out.getvalue().splitlines() if line.startswith('Temporary password'))
        self.assertEqual(authenticate(username='reg', password=password), reg)
        self.assertIsNone(UserSecurity.objects.get(user=reg).phone)
        entry = AccountAuditLog.objects.get()
        self.assertEqual((entry.actor_name, entry.action), ('Server command', 'lost_phone_reset'))

    def test_unknown_username_is_a_clear_error(self):
        with self.assertRaises(CommandError):
            call_command('reset_account', 'nobody')
