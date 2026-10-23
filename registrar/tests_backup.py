"""Backup Records: the database backup and the documents archive each take a POST and the
registrar's password, are built as they download and never kept on the server, and leave a
trace (activity log and security log). Archives saved on the server before that can still be
downloaded, and deleted to free their storage."""

import io
import json
import os
import zipfile

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.files.base import ContentFile
from django.test import Client, TestCase
from django.urls import reverse

from accounts import audit
from accounts.models import AccountAuditLog
from accounts.testing import activate
from clergy.models import ClergyProfile
from ednp.testing import MediaFolderMixin, plaintext_mode

from .models import BackupArchive, ClergyRecord, Document, DocumentType, save_document

User = get_user_model()
PASSWORD = 'pw12345!x'
PAGE = reverse('registrar:backup_records')
DUMP = reverse('registrar:download_database_backup')
ARCHIVE = reverse('registrar:download_documents_archive')
ACTIVITY = reverse('registrar:account_security')


def flashed(response):
    return [str(message) for message in get_messages(response.wsgi_request)]


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


class DocumentsArchiveTests(MediaFolderMixin, BackupTestCase):
    def setUp(self):
        super().setUp()
        override = plaintext_mode()   # with encryption on, see tests_encrypted_documents
        override.enable()
        self.addCleanup(override.disable)
        self.record = ClergyRecord.objects.get(id='CLG-001')
        self.baptism = self.add_document('Baptismal Certificate', 'baptism.pdf', b'%PDF-1.4 BAPTISM')
        self.medical = self.add_document('Medical Exam Result', 'medical.jpg', b'\xff\xd8\xff MEDICAL')

    def add_document(self, type_name, filename, content):
        document_type = DocumentType.objects.create(name=type_name)
        return save_document(self.record, document_type, ContentFile(content, name=filename), '')

    def download(self, password=PASSWORD):
        return self.client.post(ARCHIVE, {'current_password': password})

    def unzipped(self, response):
        return zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content)))

    def test_the_page_asks_for_the_password(self):
        page = self.client.get(PAGE)
        self.assertContains(page, f'action="{ARCHIVE}"')
        self.assertContains(page, 'name="current_password"', count=2)   # this and the database backup

    def test_a_plain_link_downloads_nothing(self):
        self.assertRedirects(self.client.get(ARCHIVE), PAGE, fetch_redirect_response=False)
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_without_the_right_password_nothing_is_downloaded(self):
        for response in (self.client.post(ARCHIVE), self.download('nope')):
            self.assertRedirects(response, PAGE, fetch_redirect_response=False)
            self.assertIn('Your current password is incorrect.', flashed(response))
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_it_holds_every_document_exactly_as_stored(self):
        response = self.download()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')
        self.assertTrue(response['Content-Disposition'].startswith('attachment; filename="documents_archive_'))
        self.assertEqual(response['Cache-Control'], 'no-store')
        with self.unzipped(response) as archive:
            self.assertEqual(sorted(archive.namelist()), sorted([self.baptism.file.name, self.medical.file.name]))
            self.assertEqual(archive.read(self.baptism.file.name), b'%PDF-1.4 BAPTISM')
            self.assertEqual(archive.read(self.medical.file.name), b'\xff\xd8\xff MEDICAL')

    def test_downloading_it_is_recorded_in_both_logs(self):
        with self.assertLogs('ednp.security', level='INFO') as logs:
            response = self.download()
        self.assertIn('documents archive downloaded by=reg', logs.output[0])
        entry = AccountAuditLog.objects.get()
        self.assertEqual((entry.action, entry.actor_name, entry.target, entry.target_name),
                         ('archive_downloaded', 'Rev. Ana Reyes', None, ''))
        self.assertIn(entry.note, response['Content-Disposition'])

    def test_nothing_is_kept_on_the_server(self):
        b''.join(self.download().streaming_content)
        self.assertFalse(BackupArchive.objects.exists())
        self.assertEqual(os.listdir(self.media), ['clergy_documents'])

    def test_a_document_whose_file_is_gone_is_named_in_the_archive_instead(self):
        self.medical.file.storage.delete(self.medical.file.name)
        with self.unzipped(self.download()) as archive:
            self.assertEqual(archive.namelist(), [self.baptism.file.name, 'MISSING-FILES.txt'])
            missing = archive.read('MISSING-FILES.txt').decode()
        self.assertIn(self.medical.file.name, missing)
        self.assertIn('Fr. Juan: Medical Exam Result', missing)

    def test_with_no_documents_it_is_an_empty_archive(self):
        Document.objects.all().delete()
        with self.unzipped(self.download()) as archive:
            self.assertEqual(archive.namelist(), [])

    def test_clergy_cannot_download_it(self):
        clergy = User.objects.create_user('CLG-001', password=PASSWORD)
        ClergyProfile.objects.create(user=clergy, record=self.record)
        client = Client()
        client.force_login(activate(clergy))
        response = client.post(ARCHIVE, {'current_password': PASSWORD})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AccountAuditLog.objects.exists())


class SavedArchiveTests(MediaFolderMixin, BackupTestCase):
    """Archives saved on the server before the archive became a straight download."""

    def setUp(self):
        super().setUp()
        self.saved = BackupArchive(created_by=self.registrar)
        self.saved.file.save('documents_archive_20261001_120000.zip', ContentFile(b'PK\x05\x06' + bytes(18)), save=True)

    def test_they_are_listed_with_download_and_delete(self):
        page = self.client.get(PAGE)
        self.assertContains(page, 'Archives Saved on the Server')
        self.assertContains(page, reverse('registrar:download_backup_archive', args=[self.saved.id]))
        self.assertContains(page, reverse('registrar:delete_backup_archive', args=[self.saved.id]))

    def test_with_none_saved_the_section_is_not_shown(self):
        BackupArchive.objects.all().delete()
        self.assertNotContains(self.client.get(PAGE), 'Archives Saved on the Server')

    def test_a_plain_get_request_is_refused(self):
        response = self.client.get(reverse('registrar:download_backup_archive', args=[self.saved.id]))
        self.assertRedirects(response, PAGE)
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_wrong_password_is_refused(self):
        response = self.client.post(reverse('registrar:download_backup_archive', args=[self.saved.id]), {'current_password': 'wrong'})
        self.assertRedirects(response, PAGE)
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_one_can_still_be_downloaded_and_it_is_recorded_in_both_logs(self):
        with self.assertLogs('ednp.security', level='INFO') as logs:
            response = self.client.post(reverse('registrar:download_backup_archive', args=[self.saved.id]), {'current_password': PASSWORD})
            b''.join(response.streaming_content)   # reading it all is what closes the file
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')
        self.assertIn('documents archive downloaded by=reg', logs.output[0])
        entry = AccountAuditLog.objects.get()
        self.assertEqual((entry.action, entry.note), ('archive_downloaded', 'documents_archive_20261001_120000.zip'))

    def test_deleting_one_removes_the_row_and_the_file_and_is_logged(self):
        path = self.saved.file.path
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('registrar:delete_backup_archive', args=[self.saved.id]))
        self.assertRedirects(response, PAGE, fetch_redirect_response=False)
        self.assertFalse(BackupArchive.objects.exists())
        self.assertFalse(os.path.exists(path))
        entry = AccountAuditLog.objects.get()
        self.assertEqual((entry.action, entry.actor_name, entry.target, entry.note),
                         ('archive_deleted', 'Rev. Ana Reyes', None, 'documents_archive_20261001_120000.zip'))
        self.assertContains(self.client.get(ACTIVITY), 'Saved documents archive deleted')

    def test_a_plain_link_deletes_nothing(self):
        self.client.get(reverse('registrar:delete_backup_archive', args=[self.saved.id]))
        self.assertTrue(BackupArchive.objects.exists())
        self.assertTrue(os.path.exists(self.saved.file.path))
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_one_whose_file_is_gone_is_listed_as_missing_and_can_still_be_deleted(self):
        # What a redeploy leaves when uploads aren't on a persistent volume: the row, no file.
        self.saved.file.storage.delete(self.saved.file.name)
        page = self.client.get(PAGE)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'File missing')
        self.assertNotContains(page, reverse('registrar:download_backup_archive', args=[self.saved.id]))

        response = self.client.post(reverse('registrar:download_backup_archive', args=[self.saved.id]), {'current_password': PASSWORD}, follow=True)
        self.assertContains(response, 'no longer on the server')
        self.assertFalse(AccountAuditLog.objects.exists())   # nothing was downloaded, so nothing is recorded

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('registrar:delete_backup_archive', args=[self.saved.id]))
        self.assertFalse(BackupArchive.objects.exists())

    def test_clergy_cannot_delete_one(self):
        record = ClergyRecord.objects.get(id='CLG-001')
        clergy = User.objects.create_user('CLG-001', password=PASSWORD)
        ClergyProfile.objects.create(user=clergy, record=record)
        client = Client()
        client.force_login(activate(clergy))
        client.post(reverse('registrar:delete_backup_archive', args=[self.saved.id]))
        self.assertTrue(BackupArchive.objects.exists())


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
