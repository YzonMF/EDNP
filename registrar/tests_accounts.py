import io
from datetime import timedelta
from unittest import mock

from django.contrib.auth import authenticate, get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserSecurity
from accounts.testing import activate
from clergy.models import ClergyProfile

from . import bulk, slips
from .models import ClergyRecord
from .provisioning import (SETUP_WINDOW_DAYS, ProvisionError, account_status, generate_password,
                           provision_account, reset_credentials)

User = get_user_model()


class GeneratePasswordTests(TestCase):
    def test_format_and_no_ambiguous_characters(self):
        for _ in range(200):
            password = generate_password()
            self.assertRegex(password, r'^[A-Za-z0-9]{5}-[A-Za-z0-9]{5}$')
            self.assertFalse(set(password) & set('0O1lI'))
            self.assertTrue(any(c.isdigit() for c in password))
            self.assertTrue(any(c.isalpha() for c in password))

    def test_passwords_differ(self):
        self.assertEqual(len({generate_password() for _ in range(50)}), 50)


class ProvisionTests(TestCase):
    def setUp(self):
        self.record = ClergyRecord.objects.create(id='CLG-007', name='Fr. Seven')

    def test_username_is_record_id_and_password_logs_in(self):
        user, password = provision_account(self.record)
        self.assertEqual(user.username, 'CLG-007')
        self.assertEqual(authenticate(username='CLG-007', password=password), user)
        self.assertTrue(ClergyProfile.objects.filter(user=user, record=self.record).exists())

    def test_expiry_is_thirty_days_out_and_not_activated(self):
        user, _ = provision_account(self.record)
        security = UserSecurity.objects.get(user=user)
        self.assertFalse(security.is_activated)
        delta = security.setup_expires_at - timezone.now()
        self.assertAlmostEqual(delta.total_seconds(), SETUP_WINDOW_DAYS * 86400, delta=60)

    def test_taken_username_fails_cleanly_and_does_not_touch_the_other_account(self):
        other = User.objects.create_user('clg-007', password='pw12345!x')  # case-insensitive clash
        with self.assertRaises(ProvisionError):
            provision_account(self.record)
        other.refresh_from_db()
        self.assertTrue(other.check_password('pw12345!x'))
        self.assertFalse(ClergyProfile.objects.exists())

    def test_reset_gives_new_password_and_fresh_window(self):
        user, old = provision_account(self.record)
        UserSecurity.objects.filter(user=user).update(setup_expires_at=timezone.now() - timedelta(days=1))
        new = reset_credentials(user)
        self.assertNotEqual(new, old)
        self.assertIsNone(authenticate(username='CLG-007', password=old))
        self.assertEqual(authenticate(username='CLG-007', password=new), user)
        self.assertEqual(account_status(user.security)[0], 'pending')

    def test_reset_refuses_an_activated_account(self):
        user, _ = provision_account(self.record)
        UserSecurity.objects.filter(user=user).update(activated_at=timezone.now())
        with self.assertRaises(ProvisionError):
            reset_credentials(user)


class AccountStatusTests(TestCase):
    def test_states(self):
        now = timezone.now()
        self.assertEqual(account_status(UserSecurity(activated_at=now)), ('activated', None))
        self.assertEqual(account_status(UserSecurity(setup_expires_at=now + timedelta(days=10, hours=1))), ('pending', 10))
        self.assertEqual(account_status(UserSecurity(setup_expires_at=now - timedelta(seconds=1))), ('expired', None))
        self.assertEqual(account_status(None), ('pending', None))  # account that predates the feature


class ReadNamesTests(TestCase):
    def test_text_one_per_line_trims_and_skips_blanks_and_header(self):
        names = bulk.read_names(text='Full name\n  Juan   Dela Cruz \n\nMaria Santos\n')
        self.assertEqual(names, ['Juan Dela Cruz', 'Maria Santos'])

    def test_csv_first_column_only(self):
        upload = SimpleUploadedFile('n.csv', 'Name,Notes\nJuan Cruz,x\nMaria Santos,y\n'.encode('utf-8-sig'))
        self.assertEqual(bulk.read_names(upload=upload), ['Juan Cruz', 'Maria Santos'])

    def test_xlsx_first_column(self):
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(['Full name'])
        ws.append(['Juan Cruz', 'ignored'])
        ws.append(['Maria Santos'])
        buf = io.BytesIO()
        wb.save(buf)
        upload = SimpleUploadedFile('n.xlsx', buf.getvalue())
        self.assertEqual(bulk.read_names(upload=upload), ['Juan Cruz', 'Maria Santos'])

    def test_rejects_wrong_type_fake_xlsx_empty_and_oversized(self):
        with self.assertRaises(bulk.BulkInputError):
            bulk.read_names(upload=SimpleUploadedFile('n.exe', b'x'))
        with self.assertRaises(bulk.BulkInputError):
            bulk.read_names(upload=SimpleUploadedFile('n.xlsx', b'not a zip'))
        with self.assertRaises(bulk.BulkInputError):
            bulk.read_names(text='  \n\n')
        with self.assertRaises(bulk.BulkInputError):
            bulk.read_names(text='\n'.join(f'Person {chr(65 + i % 26)}{i}' for i in range(bulk.MAX_NAMES + 1)))


class BuildPreviewTests(TestCase):
    def test_flags(self):
        ClergyRecord.objects.create(id='CLG-001', name='Juan Cruz')
        rows = bulk.build_preview(['Juan cruz', 'Maria Santos', 'maria santos', '12345', 'x' * 151])
        by_name = {r['name']: r for r in rows}
        self.assertIn('already exists', by_name['Juan cruz']['warnings'][0])
        self.assertEqual(by_name['Maria Santos']['warnings'], [])
        self.assertIn('more than once', rows[2]['warnings'][0])
        self.assertTrue(by_name['12345']['errors'])
        self.assertTrue(by_name['x' * 151]['errors'])
        self.assertEqual(bulk.valid_names(rows), ['Juan cruz', 'Maria Santos', 'maria santos'])


class SlipLayoutTests(TestCase):
    def test_pages_rows_and_padding(self):
        entries = [{'name': f'P{i}'} for i in range(9)]  # 9 slips -> 5 rows -> 2 pages (4 + 1)
        pages = slips.build_pages(entries)
        self.assertEqual([len(p) for p in pages], [slips.ROWS_PER_PAGE, 1])
        self.assertIsNone(pages[1][0][1])  # odd one out is padded, not dropped
        self.assertEqual(sum(1 for p in pages for r in p for s in r if s), 9)


class BulkViewTests(TestCase):
    def setUp(self):
        self.client.force_login(activate(User.objects.create_user('reg', password='pw12345!x', is_staff=True)))
        self.url = reverse('registrar:bulk_accounts')

    def _preview(self, text):
        return self.client.post(self.url, {'step': 'preview', 'names_text': text})

    def test_preview_creates_nothing(self):
        r = self._preview('Juan Cruz\nMaria Santos')
        self.assertContains(r, 'Review before creating')
        self.assertFalse(ClergyRecord.objects.exists())
        self.assertFalse(User.objects.filter(username__startswith='CLG').exists())

    def test_confirm_creates_selected_people_and_returns_pdf(self):
        token = self._preview('Juan Cruz\nMaria Santos').context['token']
        r = self.client.post(self.url, {'step': 'confirm', 'token': token, 'names': ['Juan Cruz', 'Maria Santos']})
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertTrue(r.content.startswith(b'%PDF'))
        self.assertEqual(list(ClergyRecord.objects.order_by('id').values_list('id', 'name')),
                         [('CLG-001', 'Juan Cruz'), ('CLG-002', 'Maria Santos')])
        self.assertEqual(UserSecurity.objects.filter(user__username__startswith='CLG').count(), 2)

    def test_only_ticked_names_are_created(self):
        token = self._preview('Juan Cruz\nMaria Santos').context['token']
        self.client.post(self.url, {'step': 'confirm', 'token': token, 'names': ['Maria Santos']})
        self.assertEqual(list(ClergyRecord.objects.values_list('name', flat=True)), ['Maria Santos'])

    def test_token_is_single_use_so_a_resubmit_cannot_duplicate_people(self):
        token = self._preview('Juan Cruz').context['token']
        data = {'step': 'confirm', 'token': token, 'names': ['Juan Cruz']}
        self.client.post(self.url, data)
        r = self.client.post(self.url, data, follow=True)
        self.assertContains(r, 'already submitted')
        self.assertEqual(ClergyRecord.objects.count(), 1)

    def test_confirm_without_a_preview_token_is_refused(self):
        self.client.post(self.url, {'step': 'confirm', 'token': 'forged', 'names': ['Juan Cruz']})
        self.assertFalse(ClergyRecord.objects.exists())

    def test_tampered_invalid_names_are_dropped_on_confirm(self):
        token = self._preview('Juan Cruz').context['token']
        self.client.post(self.url, {'step': 'confirm', 'token': token, 'names': ['Juan Cruz', '12345', 'x' * 200]})
        self.assertEqual(list(ClergyRecord.objects.values_list('name', flat=True)), ['Juan Cruz'])

    def test_clash_aborts_the_whole_batch(self):
        User.objects.create_user('CLG-002', password='pw12345!x')  # will clash with the 2nd person
        token = self._preview('Juan Cruz\nMaria Santos').context['token']
        r = self.client.post(self.url, {'step': 'confirm', 'token': token, 'names': ['Juan Cruz', 'Maria Santos']}, follow=True)
        self.assertContains(r, 'Nothing was created')
        self.assertFalse(ClergyRecord.objects.exists())

    def test_clergy_cannot_use_it(self):
        clergy = activate(User.objects.create_user('c', password='pw12345!x'))
        self.client.force_login(clergy)
        r = self.client.post(self.url, {'step': 'preview', 'names_text': 'Juan Cruz'})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(ClergyRecord.objects.exists())


class ResetAndLoginExpiryTests(TestCase):
    def setUp(self):
        self.record = ClergyRecord.objects.create(id='CLG-001', name='Fr. A')
        self.user, self.password = provision_account(self.record)
        self.registrar = activate(User.objects.create_user('reg', password='pw12345!x', is_staff=True))

    def _login(self, password):
        return self.client.post('/', {'username': 'CLG-001', 'password': password})

    def test_login_works_inside_the_window(self):
        self.assertEqual(self._login(self.password).status_code, 302)

    def test_login_refused_after_the_window_with_a_clear_message(self):
        UserSecurity.objects.filter(user=self.user).update(setup_expires_at=timezone.now() - timedelta(minutes=1))
        r = self._login(self.password)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'temporary password has expired')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_activated_account_never_expires(self):
        UserSecurity.objects.filter(user=self.user).update(
            setup_expires_at=timezone.now() - timedelta(days=90), activated_at=timezone.now())
        self.assertEqual(self._login(self.password).status_code, 302)

    def test_reset_view_reprints_and_reopens_an_expired_account(self):
        UserSecurity.objects.filter(user=self.user).update(setup_expires_at=timezone.now() - timedelta(days=1))
        self.client.force_login(self.registrar)
        r = self.client.post(reverse('registrar:reset_account', args=[self.record.id]))
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.user.security.refresh_from_db()
        self.assertGreater(self.user.security.setup_expires_at, timezone.now())
        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password(self.password))
