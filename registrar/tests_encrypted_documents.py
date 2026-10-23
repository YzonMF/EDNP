"""Encryption at rest through the real pages: documents uploaded, viewed and downloaded, reports,
and the documents archive (which holds the files as stored, so they stay encrypted inside it)."""

import io
import os
import zipfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from accounts.testing import activate
from clergy.models import ClergyProfile
from ednp import encryption
from ednp.storage import EncryptedFileSystemStorage
from ednp.testing import MediaFolderMixin, encrypted, needs_crypto, new_key, plaintext_mode

from .models import BackupArchive, ClergyRecord, Document, DocumentType, GeneratedReport

User = get_user_model()
PASSWORD = 'pw12345!x'
MAGIC = encryption.FILE_MAGIC
PDF = b'%PDF-1.4\n' + b'PSYCHIATRIC-RESULT ' * 2000


class EncryptedPagesCase(MediaFolderMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.key = new_key()
        self.registrar = activate(User.objects.create_user('reg', password=PASSWORD, is_staff=True))
        self.registrar.first_name = 'Rev. Ana Reyes'
        self.registrar.save()
        self.record = ClergyRecord.objects.create(id='CLG-001', name='Fr. Juan')
        self.clergy = activate(User.objects.create_user('CLG-001', password=PASSWORD))
        ClergyProfile.objects.create(user=self.clergy, record=self.record)
        self.doc_type, _ = DocumentType.objects.get_or_create(name='Baptismal Certificate')

    def as_registrar(self):
        client = Client()
        client.force_login(self.registrar)
        return client

    def as_clergy(self):
        client = Client()
        client.force_login(self.clergy)
        return client

    def upload_as_registrar(self, content=PDF):
        return self.as_registrar().post(reverse('registrar:record_detail', args=[self.record.id]), {
            'document_type': self.doc_type.pk, 'file': SimpleUploadedFile('result.pdf', content), 'notes': ''})

    def on_disk(self, field_file):
        with open(field_file.storage.path(field_file.name), 'rb') as file:
            return file.read()

    def whole(self, response):
        return b''.join(response.streaming_content)


@needs_crypto
class DocumentTests(EncryptedPagesCase):
    def test_an_upload_is_stored_encrypted_and_is_viewed_and_downloaded_whole(self):
        with encrypted(self.key):
            self.upload_as_registrar()
            document = Document.objects.get()
            raw = self.on_disk(document.file)
            self.assertTrue(raw.startswith(MAGIC))
            self.assertNotIn(b'PSYCHIATRIC-RESULT', raw)

            viewed = self.as_registrar().get(reverse('registrar:view_document', args=[document.id]))
            self.assertEqual(viewed['Content-Type'], 'application/pdf')
            self.assertEqual(viewed['Content-Length'], str(len(PDF)))
            self.assertEqual(self.whole(viewed), PDF)

            downloaded = self.as_registrar().get(reverse('registrar:download_document', args=[document.id]))
            self.assertIn('attachment', downloaded['Content-Disposition'])
            self.assertEqual(self.whole(downloaded), PDF)

    def test_a_clergy_member_uploads_and_views_their_own_document(self):
        with encrypted(self.key):
            client = self.as_clergy()
            client.post(reverse('clergy:my_requirements'), {
                'document_type': self.doc_type.pk, 'file': SimpleUploadedFile('mine.pdf', PDF), 'notes': ''})
            document = Document.objects.get()
            self.assertTrue(self.on_disk(document.file).startswith(MAGIC))
            self.assertEqual(self.whole(client.get(reverse('registrar:view_document', args=[document.id]))), PDF)

    def test_who_may_see_a_document_is_unchanged(self):
        with encrypted(self.key):
            self.upload_as_registrar()
            document = Document.objects.get()
            other = activate(User.objects.create_user('CLG-002', password=PASSWORD))
            ClergyProfile.objects.create(user=other, record=ClergyRecord.objects.create(id='CLG-002', name='Fr. Pedro'))
            client = Client()
            client.force_login(other)
            self.assertEqual(client.get(reverse('registrar:view_document', args=[document.id])).status_code, 404)
            self.assertEqual(Client().get(reverse('registrar:view_document', args=[document.id])).status_code, 302)

    def test_replacing_a_document_removes_the_old_encrypted_file(self):
        with encrypted(self.key):
            self.upload_as_registrar(b'%PDF-1.4 first')
            first = Document.objects.get().file
            first_path = first.storage.path(first.name)
            with self.captureOnCommitCallbacks(execute=True):   # the old file is removed once the new one has committed
                self.upload_as_registrar(b'%PDF-1.4 second version')
            document = Document.objects.get()
            new_path = document.file.storage.path(document.file.name)
            self.assertNotEqual(first_path, new_path)
            self.assertFalse(os.path.exists(first_path))
            self.assertTrue(os.path.exists(new_path))
            self.assertEqual(self.whole(self.as_registrar().get(reverse('registrar:view_document', args=[document.id]))),
                             b'%PDF-1.4 second version')

    def test_a_document_uploaded_before_encryption_is_still_viewable_until_it_is_required(self):
        with plaintext_mode():
            self.upload_as_registrar()
        document = Document.objects.get()
        self.assertEqual(self.on_disk(document.file), PDF)   # stored plain, as before
        with encrypted(self.key):
            self.assertEqual(self.whole(self.as_registrar().get(reverse('registrar:view_document', args=[document.id]))), PDF)

    def test_with_encryption_required_everything_works_and_a_plaintext_leftover_fails_loudly(self):
        with plaintext_mode():
            self.upload_as_registrar(b'%PDF-1.4 old plaintext')
        leftover = Document.objects.get()
        with encrypted(self.key, required=True):
            self.doc_type = DocumentType.objects.create(name='Confirmation Certificate')
            self.upload_as_registrar()
            fresh = Document.objects.get(document_type=self.doc_type)
            self.assertEqual(self.whole(self.as_registrar().get(reverse('registrar:view_document', args=[fresh.id]))), PDF)
            with self.assertRaisesMessage(encryption.EncryptionError, 'DATA_ENCRYPTION_REQUIRED'):
                self.as_registrar().get(reverse('registrar:view_document', args=[leftover.id]))

    def test_the_wrong_key_is_an_error_and_not_a_corrupt_download(self):
        with encrypted(self.key):
            self.upload_as_registrar()
        document = Document.objects.get()
        with encrypted(new_key()):
            with self.assertRaises(encryption.EncryptionError):
                self.as_registrar().get(reverse('registrar:view_document', args=[document.id]))


@needs_crypto
class ReportAndArchiveTests(EncryptedPagesCase):
    def test_a_generated_report_is_stored_encrypted_and_downloads_as_a_real_pdf(self):
        with encrypted(self.key):
            self.as_registrar().post(reverse('registrar:generate_report'), {'type': 'status_report'})
            report = GeneratedReport.objects.get()
            self.assertTrue(self.on_disk(report.file).startswith(MAGIC))
            response = self.as_registrar().get(reverse('registrar:download_generated_report', args=[report.id]))
            body = self.whole(response)
            self.assertTrue(body.startswith(b'%PDF'))
            self.assertEqual(response['Content-Length'], str(len(body)))
            viewed = self.as_registrar().get(reverse('registrar:view_generated_report', args=[report.id]))
            self.assertTrue(self.whole(viewed).startswith(b'%PDF'))

    def test_the_documents_archive_holds_the_files_encrypted_and_they_can_be_restored(self):
        with encrypted(self.key):
            self.upload_as_registrar()
            document = Document.objects.get()
            name = document.file.name

            response = self.as_registrar().post(reverse('registrar:download_documents_archive'),
                                                {'current_password': PASSWORD})
            downloaded = self.whole(response)
            self.assertFalse(BackupArchive.objects.exists())                # nothing is kept on the server
            self.assertTrue(zipfile.is_zipfile(io.BytesIO(downloaded)))     # what the registrar gets is an ordinary zip
            with zipfile.ZipFile(io.BytesIO(downloaded)) as zipped:
                self.assertEqual(zipped.namelist(), [name])
                member = zipped.read(name)
            self.assertTrue(member.startswith(MAGIC))                       # whose documents are still encrypted
            self.assertNotIn(b'PSYCHIATRIC-RESULT', downloaded)

            # Restoring: put the file back where it was, and it reads again with the same key.
            os.remove(document.file.storage.path(name))
            with open(document.file.storage.path(name), 'wb') as restored:
                restored.write(member)
            self.assertEqual(EncryptedFileSystemStorage().open(name).read(), PDF)

            # ...and not with any other key.
            with encrypted(new_key()):
                with self.assertRaises(encryption.EncryptionError):
                    EncryptedFileSystemStorage().open(name)

    def test_the_backup_page_tells_the_registrar_about_the_key_only_when_encryption_is_on(self):
        page = reverse('registrar:backup_records')
        with plaintext_mode():
            self.assertNotContains(self.as_registrar().get(page), 'DATA_ENCRYPTION_KEY')
        with encrypted(self.key):
            self.assertContains(self.as_registrar().get(page), 'DATA_ENCRYPTION_KEY')
