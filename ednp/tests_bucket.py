"""Stored files in a bucket (ednp/bucket_storage.py), tested against moto's stand-in for S3, and
the startup checks for the bucket settings. The bucket tests are skipped when moto isn't
installed (pip install "moto[s3]"); the site itself doesn't need it."""

import io
import os
import unittest
import zipfile

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage, storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from accounts.testing import activate
from registrar.models import ClergyRecord, Document, DocumentType

from . import checks, encryption
from .testing import MediaFolderMixin, encrypted, needs_crypto, new_key, plaintext_mode

try:
    import boto3
    from moto import mock_aws
except ImportError:
    mock_aws = None

needs_moto = unittest.skipIf(mock_aws is None, 'moto is not installed (pip install "moto[s3]")')

User = get_user_model()
PASSWORD = 'pw12345!x'
BUCKET = 'ednp-documents'
OPTIONS = {
    'bucket_name': BUCKET, 'region_name': 'us-east-1', 'endpoint_url': None,
    'access_key': 'test-key', 'secret_key': 'test-secret', 'addressing_style': 'virtual',
}
PDF = b'%PDF-1.4\n' + b'PSYCHIATRIC-RESULT ' * 2000


def bucket_settings():
    return override_settings(
        BUCKET_NAME=BUCKET, BUCKET_OPTIONS=OPTIONS,
        STORAGES={'default': {'BACKEND': 'ednp.bucket_storage.EncryptedS3Storage'},
                  'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})


@needs_moto
@needs_crypto
class BucketTests(MediaFolderMixin, TestCase):
    def setUp(self):
        super().setUp()
        aws = mock_aws()
        aws.start()
        self.addCleanup(aws.stop)
        self.s3 = boto3.client('s3', region_name='us-east-1')
        self.s3.create_bucket(Bucket=BUCKET)
        for override in (bucket_settings(), plaintext_mode()):
            override.enable()
            self.addCleanup(override.disable)

        self.registrar = activate(User.objects.create_user('reg', password=PASSWORD, is_staff=True))
        self.client.force_login(self.registrar)
        self.record = ClergyRecord.objects.create(id='CLG-001', name='Fr. Juan')
        self.doc_type = DocumentType.objects.create(name='Medical Exam Result')

    def keys(self):
        return sorted(entry['Key'] for entry in self.s3.list_objects_v2(Bucket=BUCKET).get('Contents', ()))

    def raw(self, key):
        return self.s3.get_object(Bucket=BUCKET, Key=key)['Body'].read()

    def upload(self, content=PDF, name='result.pdf'):
        return self.client.post(reverse('registrar:record_detail', args=[self.record.id]), {
            'document_type': self.doc_type.pk, 'file': SimpleUploadedFile(name, content), 'notes': ''})

    def whole(self, response):
        return b''.join(response.streaming_content)

    def test_the_site_uses_the_bucket_when_one_is_set(self):
        from .bucket_storage import EncryptedS3Storage
        self.assertIsInstance(storages['default'], EncryptedS3Storage)
        self.assertEqual(storages['default'].bucket_name, BUCKET)
        self.assertFalse(storages['default'].file_overwrite)

    def test_an_upload_lands_in_the_bucket_not_on_disk_and_reads_back(self):
        self.upload()
        document = Document.objects.get()
        self.assertEqual(self.keys(), [document.file.name])
        self.assertEqual(self.raw(document.file.name), PDF)
        self.assertEqual(os.listdir(self.media), [])
        response = self.client.get(reverse('registrar:view_document', args=[document.id]))
        self.assertEqual(self.whole(response), PDF)
        self.assertEqual(response['Content-Length'], str(len(PDF)))

    def test_with_a_key_it_is_stored_encrypted_and_reads_back(self):
        with encrypted(new_key()):
            self.upload()
            document = Document.objects.get()
            raw = self.raw(document.file.name)
            self.assertTrue(raw.startswith(encryption.FILE_MAGIC))
            self.assertNotIn(b'PSYCHIATRIC-RESULT', raw)
            self.assertLess(len(raw), len(PDF) * 1.01)
            download = self.client.get(reverse('registrar:download_document', args=[document.id]))
            self.assertEqual(self.whole(download), PDF)

    def test_replacing_a_document_with_one_of_the_same_file_name_keeps_the_new_one(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.upload(b'%PDF-1.4 first')
        with self.captureOnCommitCallbacks(execute=True):
            self.upload(b'%PDF-1.4 second')
        document = Document.objects.get()
        self.assertEqual(self.keys(), [document.file.name])                  # the old one is gone...
        self.assertEqual(self.raw(document.file.name), b'%PDF-1.4 second')   # ...and the new one is not

    def test_a_file_gone_from_the_bucket_is_a_404_not_a_server_error(self):
        self.upload()
        document = Document.objects.get()
        self.s3.delete_object(Bucket=BUCKET, Key=document.file.name)
        self.assertEqual(self.client.get(reverse('registrar:view_document', args=[document.id])).status_code, 404)
        with self.assertRaises(FileNotFoundError):
            default_storage.size(document.file.name)

    def test_the_documents_archive_is_read_from_the_bucket(self):
        self.upload()
        name = Document.objects.get().file.name
        response = self.client.post(reverse('registrar:download_documents_archive'), {'current_password': PASSWORD})
        with zipfile.ZipFile(io.BytesIO(self.whole(response))) as archive:
            self.assertEqual(archive.namelist(), [name])
            self.assertEqual(archive.read(name), PDF)
        self.assertEqual(self.keys(), [name])   # and nothing was added to the bucket

    def test_stored_names_and_put_stored(self):
        default_storage.put_stored('generated_reports/a.pdf', io.BytesIO(b'as given'))
        default_storage.put_stored('generated_reports/a.pdf', io.BytesIO(b'replaced'))
        self.assertEqual(list(default_storage.stored_names()), ['generated_reports/a.pdf'])
        self.assertEqual(self.raw('generated_reports/a.pdf'), b'replaced')

    def test_encrypt_existing_data_encrypts_what_is_in_the_bucket(self):
        self.s3.put_object(Bucket=BUCKET, Key='clergy_documents/CLG-001/1_old.pdf', Body=PDF)
        with encrypted(new_key()):
            out = io.StringIO()
            call_command('encrypt_existing_data', stdout=out)
            self.assertIn('Files: 1 encrypted now', out.getvalue())
            self.assertTrue(self.raw('clergy_documents/CLG-001/1_old.pdf').startswith(encryption.FILE_MAGIC))
            with default_storage.open('clergy_documents/CLG-001/1_old.pdf') as file:
                self.assertEqual(file.read(), PDF)

    def test_copy_files_to_bucket_copies_what_is_on_disk_exactly_and_only_once(self):
        files = {'clergy_documents/CLG-001/1_a.pdf': b'EDNPENC2 still encrypted', 'generated_reports/r.pdf': PDF}
        for name, content in files.items():
            path = os.path.join(self.media, *name.split('/'))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as file:
                file.write(content)

        def run(*args):
            out = io.StringIO()
            call_command('copy_files_to_bucket', *args, stdout=out)
            return out.getvalue()

        self.assertIn('2 would be copied', run('--dry-run'))
        self.assertEqual(self.keys(), [])
        self.assertIn('2 copied', run())
        self.assertEqual({key: self.raw(key) for key in self.keys()}, files)
        self.assertIn('2 already in the bucket', run())
        self.assertEqual(sorted(os.listdir(self.media)), ['clergy_documents', 'generated_reports'])   # disk untouched


class CopyWithoutBucketTests(SimpleTestCase):
    def test_copy_files_to_bucket_needs_a_bucket(self):
        with override_settings(BUCKET_NAME=''):
            with self.assertRaisesMessage(CommandError, 'BUCKET_NAME is not set'):
                call_command('copy_files_to_bucket', stdout=io.StringIO())


class BucketCheckTests(SimpleTestCase):
    OPTIONS = {**OPTIONS, 'endpoint_url': 'https://storage.example.com'}

    def test_no_bucket_is_fine_but_a_deploy_check_warns_that_files_are_on_local_disk(self):
        with override_settings(BUCKET_NAME=''):
            self.assertEqual(checks.bucket_settings(None), [])
            self.assertEqual([w.id for w in checks.files_on_local_disk(None)], ['ednp.W002'])

    def test_a_complete_bucket_setting_passes(self):
        with override_settings(BUCKET_NAME=BUCKET, BUCKET_OPTIONS=self.OPTIONS):
            self.assertEqual(checks.bucket_settings(None), [])
            self.assertEqual(checks.files_on_local_disk(None), [])

    def test_a_bucket_without_its_keys_stops_the_site_starting_and_says_which_are_missing(self):
        with override_settings(BUCKET_NAME=BUCKET, BUCKET_OPTIONS={**self.OPTIONS, 'endpoint_url': None, 'secret_key': None}):
            errors = checks.bucket_settings(None)
        self.assertEqual([e.id for e in errors], ['ednp.E003'])
        self.assertIn('BUCKET_ENDPOINT, BUCKET_SECRET_ACCESS_KEY are not', errors[0].msg)

    def test_an_unknown_url_style_stops_the_site_starting(self):
        with override_settings(BUCKET_NAME=BUCKET, BUCKET_OPTIONS={**self.OPTIONS, 'addressing_style': 'sideways'}):
            self.assertEqual([e.id for e in checks.bucket_settings(None)], ['ednp.E004'])

    def test_the_test_suite_never_uses_a_real_bucket(self):
        from django.conf import settings
        self.assertEqual(settings.BUCKET_NAME, '')
