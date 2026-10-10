"""Backup Records: the database backup takes a POST and the registrar's password,
and both kinds of backup leave a trace (activity log and security log)."""

import json
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts import audit
from accounts.models import AccountAuditLog
from accounts.testing import activate
from clergy.models import ClergyProfile

from .models import BackupArchive, ClergyRecord

User = get_user_model()
PASSWORD = 'pw12345!x'
PAGE = reverse('registrar:backup_records')
DUMP = reverse('registrar:download_database_backup')
ACTIVITY = reverse('registrar:account_security')


class BackupTestCase(TestCase):
    def setUp(self):
        self.registrar = activate(User.objects.create_user('reg', password=PASSWORD, is_staff=True))
        self.registrar.first_name = 'Rev. Ana Reyes'
        self.registrar.save()
        self.client.force_login(self.registrar)
        ClergyRecord.objects.create(id='CLG-001', name='Fr. Juan')


class DatabaseBackupTests(BackupTestCase):
    def test_the_page_asks_for_the_password(self):
        page = self.client.get(PAGE)
        self.assertContains(page, 'name="current_password"')
        self.assertContains(page, f'action="{DUMP}"')

    def test_a_plain_link_no_longer_downloads_anything(self):
        response = self.client.get(DUMP)
        self.assertRedirects(response, PAGE, fetch_redirect_response=False)
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_without_the_password_nothing_is_downloaded(self):
        response = self.client.post(DUMP)
        self.assertRedirects(response, PAGE, fetch_redirect_response=False)
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_a_wrong_password_is_refused_and_counts_toward_the_lockout(self):
        for _ in range(5):
            response = self.client.post(DUMP, {'current_password': 'nope'})
            self.assertRedirects(response, PAGE, fetch_redirect_response=False)
        self.assertFalse(AccountAuditLog.objects.exists())
        # Locked now: even the right password is refused.
        response = self.client.post(DUMP, {'current_password': PASSWORD}, follow=True)
        self.assertContains(response, 'Too many failed attempts')
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_the_right_password_downloads_the_file_and_it_is_recorded(self):
        response = self.client.post(DUMP, {'current_password': PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        self.assertTrue(response['Content-Disposition'].startswith('attachment; filename="database_backup_'))
        self.assertEqual(response['Cache-Control'], 'no-store')

        data = json.loads(response.content)
        self.assertTrue(any(item['model'] == 'registrar.clergyrecord' for item in data))
        users = [item for item in data if item['model'] == 'auth.user']
        self.assertTrue(users)
        self.assertTrue(all(user['fields']['password'] == '' for user in users))   # still no password hashes

        entry = AccountAuditLog.objects.get()
        self.assertEqual((entry.action, entry.actor_name, entry.target, entry.target_name),
                         ('database_backup', 'Rev. Ana Reyes', None, ''))

    def test_the_download_is_written_to_the_security_log(self):
        with self.assertLogs('ednp.security', level='INFO') as logs:
            self.client.post(DUMP, {'current_password': PASSWORD})
        self.assertIn('database backup downloaded by=reg', logs.output[0])
        self.assertNotIn(PASSWORD, logs.output[0])

    def test_a_wrong_password_is_written_to_the_security_log(self):
        with self.assertLogs('ednp.security', level='INFO') as logs:
            self.client.post(DUMP, {'current_password': 'nope'})
        self.assertIn('password check failed account=reg failures=1', logs.output[0])
        self.assertNotIn('nope', logs.output[0])

    def test_the_activity_page_shows_it_with_no_account_beside_it(self):
        self.client.post(DUMP, {'current_password': PASSWORD})
        page = self.client.get(ACTIVITY)
        self.assertContains(page, 'Database backup downloaded')
        self.assertContains(page, '<td>—</td>')   # no account to name, and it doesn't break the row

    def test_clergy_cannot_take_a_backup(self):
        record = ClergyRecord.objects.get(id='CLG-001')
        clergy = User.objects.create_user('CLG-001', password=PASSWORD)
        ClergyProfile.objects.create(user=clergy, record=record)
        activate(clergy)
        client = Client()
        client.force_login(clergy)
        response = client.post(DUMP, {'current_password': PASSWORD})
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(response['Content-Type'], 'application/json')
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_a_visitor_who_is_not_signed_in_cannot_take_one(self):
        response = Client().post(DUMP, {'current_password': PASSWORD})
        self.assertRedirects(response, f'/?next={DUMP}', fetch_redirect_response=False)
        self.assertFalse(AccountAuditLog.objects.exists())


class DocumentsArchiveTests(BackupTestCase):
    def setUp(self):
        super().setUp()
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)

    def test_creating_one_is_written_to_the_security_log(self):
        with self.assertLogs('ednp.security', level='INFO') as logs:
            self.client.post(PAGE)
        self.assertEqual(BackupArchive.objects.count(), 1)
        self.assertIn('documents archive created by=reg', logs.output[0])

    def test_downloading_one_is_recorded_in_both_logs(self):
        self.client.post(PAGE)
        archive = BackupArchive.objects.get()
        with self.assertLogs('ednp.security', level='INFO') as logs:
            response = self.client.get(reverse('registrar:download_backup_archive', args=[archive.id]))
            b''.join(response.streaming_content)   # reading it all is what closes the file
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('documents archive downloaded by=reg', logs.output[0])
        entry = AccountAuditLog.objects.get()
        self.assertEqual((entry.action, entry.actor_name, entry.target, entry.target_name),
                         ('archive_downloaded', 'Rev. Ana Reyes', None, ''))
        self.assertTrue(entry.note.startswith('documents_archive_'))


class AuditHelperTests(TestCase):
    def test_an_entry_can_have_no_target_account(self):
        user = User.objects.create_user('reg', password=PASSWORD, is_staff=True)
        entry = audit.record(user, None, 'database_backup')
        self.assertEqual((entry.target, entry.target_name), (None, ''))

    def test_an_entry_about_an_account_is_named_as_before(self):
        actor = User.objects.create_user('reg', password=PASSWORD, is_staff=True)
        target = User.objects.create_user('someone', password=PASSWORD)
        self.assertEqual(audit.record(actor, target, 'password_reset').target_name, 'someone')
        self.assertEqual(audit.record(actor, target, 'password_reset', target_name='Fr. Pedro').target_name, 'Fr. Pedro')
