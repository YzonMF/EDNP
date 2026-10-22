import io
import os
import shutil
import tempfile
import zipfile
from datetime import date

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

import importlib

from accounts.models import AccountAuditLog
from accounts.testing import activate
from clergy.models import ClergyProfile, Notification
from registrar import backups
from registrar.forms import EmberLetterUploadForm

ember_cleanup_migration = importlib.import_module(
    'registrar.migrations.0013_remove_seminary_ember_document_type'
)
from registrar.models import (
    ClergyRecord,
    Document,
    DocumentType,
    EmberLetter,
    delete_ember_letter,
    save_document,
    save_ember_letter,
)

User = get_user_model()

PDF = b'%PDF-1.4\n%fake but correctly-headed pdf\n'


def _make_png_bytes(width=120, height=120):
    buf = io.BytesIO()
    img = Image.new('RGB', (width, height), color=(40, 90, 160))
    img.save(buf, format='PNG')
    return buf.getvalue()


class EmberLetterTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)

        self.registrar = activate(User.objects.create_user('reg', password='pw12345!x', is_staff=True))
        self.record_a = ClergyRecord.objects.create(id='CLG-001', name='Fr. Alpha')
        self.user_a = activate(User.objects.create_user('clg_a', password='pw12345!x'))
        self.profile_a = ClergyProfile.objects.create(user=self.user_a, record=self.record_a)

        self.record_b = ClergyRecord.objects.create(id='CLG-002', name='Fr. Beta')
        self.user_b = activate(User.objects.create_user('clg_b', password='pw12345!x'))
        self.profile_b = ClergyProfile.objects.create(user=self.user_b, record=self.record_b)

    def test_rejects_6mb_pdf_and_6mb_image(self):
        big_pdf = SimpleUploadedFile('big.pdf', PDF + b'0' * (6 * 1024 * 1024))
        form_pdf = EmberLetterUploadForm({'year': 2025, 'quarter': 1}, {'file': big_pdf})
        self.assertFalse(form_pdf.is_valid())
        self.assertIn('5 MB', str(form_pdf.errors))

        big_png = SimpleUploadedFile('big.png', _make_png_bytes() + b'0' * (6 * 1024 * 1024))
        form_png = EmberLetterUploadForm({'year': 2025, 'quarter': 1}, {'file': big_png})
        self.assertFalse(form_png.is_valid())
        self.assertIn('5 MB', str(form_png.errors))

    def test_valid_photo_is_shrunk_and_accepted_and_older_years_supported(self):
        png_bytes = _make_png_bytes(2200, 2200)
        upload = SimpleUploadedFile('scan.png', png_bytes, content_type='image/png')
        form = EmberLetterUploadForm({'year': 1978, 'quarter': 3}, {'file': upload})
        self.assertTrue(form.is_valid(), form.errors)

        form_too_old = EmberLetterUploadForm(
            {'year': 1940, 'quarter': 1},
            {'file': SimpleUploadedFile('a.pdf', PDF)},
        )
        self.assertFalse(form_too_old.is_valid())

        form_future = EmberLetterUploadForm(
            {'year': date.today().year + 1, 'quarter': 1},
            {'file': SimpleUploadedFile('a.pdf', PDF)},
        )
        self.assertFalse(form_future.is_valid())

    def test_uploading_twice_to_same_slot_replaces_row_and_deletes_old_file(self):
        with self.captureOnCommitCallbacks(execute=True):
            first, replaced1 = save_ember_letter(
                self.record_a, 2024, 2, SimpleUploadedFile('first.pdf', PDF), user=self.user_a
            )
        self.assertFalse(replaced1)
        old_path = first.file.path
        self.assertTrue(os.path.exists(old_path))

        with self.captureOnCommitCallbacks(execute=True):
            second, replaced2 = save_ember_letter(
                self.record_a, 2024, 2, SimpleUploadedFile('second.pdf', PDF + b'extra'), user=self.user_a
            )
        self.assertTrue(replaced2)
        self.assertEqual(EmberLetter.objects.filter(record=self.record_a, year=2024, quarter=2).count(), 1)
        self.assertEqual(first.pk, second.pk)
        self.assertFalse(os.path.exists(old_path))
        self.assertTrue(os.path.exists(second.file.path))
        self.assertEqual(second.file_size, len(PDF + b'extra'))

    def test_clergy_can_manage_own_ember_letters_but_not_another_record(self):
        self.client.force_login(self.user_a)
        upload_url = reverse('clergy:upload_ember_letter')
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(upload_url, {
                'year': 2025,
                'quarter': 1,
                'file': SimpleUploadedFile('q1.pdf', PDF),
            })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data['ok'])
        ember_id = data['letter']['id']

        # Own record: view & download succeed
        self.assertEqual(self.client.get(reverse('registrar:view_ember_letter', args=[ember_id])).status_code, 200)
        self.assertEqual(self.client.get(reverse('registrar:download_ember_letter', args=[ember_id])).status_code, 200)

        # Clergy user cannot hit registrar upload endpoint for another record
        reg_upload_b = reverse('registrar:upload_ember_letter', args=[self.record_b.id])
        self.assertNotEqual(
            self.client.post(reg_upload_b, {'year': 2025, 'quarter': 1, 'file': SimpleUploadedFile('x.pdf', PDF)}).status_code,
            200,
        )

        # User B cannot view, download, or delete User A's Ember letter
        self.client.force_login(self.user_b)
        self.assertEqual(self.client.get(reverse('registrar:view_ember_letter', args=[ember_id])).status_code, 404)
        self.assertEqual(self.client.get(reverse('registrar:download_ember_letter', args=[ember_id])).status_code, 404)
        self.assertEqual(self.client.post(reverse('registrar:delete_ember_letter', args=[ember_id])).status_code, 404)

    def test_registrar_can_upload_view_download_and_delete_for_any_record(self):
        self.client.force_login(self.registrar)
        reg_upload_b = reverse('registrar:upload_ember_letter', args=[self.record_b.id])
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(reg_upload_b, {
                'year': 2023,
                'quarter': 4,
                'file': SimpleUploadedFile('q4.pdf', PDF),
            })
        self.assertEqual(r.status_code, 200)
        ember_id = r.json()['letter']['id']
        ember = EmberLetter.objects.get(pk=ember_id)
        stored_path = ember.file.path
        self.assertTrue(os.path.exists(stored_path))

        self.assertEqual(self.client.get(reverse('registrar:view_ember_letter', args=[ember_id])).status_code, 200)
        self.assertEqual(self.client.get(reverse('registrar:download_ember_letter', args=[ember_id])).status_code, 200)

        with self.captureOnCommitCallbacks(execute=True):
            del_resp = self.client.post(reverse('registrar:delete_ember_letter', args=[ember_id]))
        self.assertEqual(del_resp.status_code, 200)
        self.assertTrue(del_resp.json()['ok'])
        self.assertFalse(EmberLetter.objects.filter(pk=ember_id).exists())
        self.assertFalse(os.path.exists(stored_path))

        actions = list(AccountAuditLog.objects.filter(target=self.user_b).values_list('action', flat=True))
        self.assertIn('ember_uploaded', actions)
        self.assertIn('ember_deleted', actions)

    def test_twelve_uploads_in_one_session_collapse_into_single_notification(self):
        self.client.force_login(self.user_a)
        upload_url = reverse('clergy:upload_ember_letter')
        for year in (2023, 2024, 2025):
            for quarter in (1, 2, 3, 4):
                r = self.client.post(upload_url, {
                    'year': year,
                    'quarter': quarter,
                    'file': SimpleUploadedFile(f'{year}_q{quarter}.pdf', PDF),
                })
                self.assertEqual(r.status_code, 200)

        self.assertEqual(EmberLetter.objects.filter(record=self.record_a).count(), 12)
        notices = Notification.objects.filter(user=self.user_a, kind='ember_uploaded')
        self.assertEqual(notices.count(), 1)
        notice = notices.first()
        self.assertIn('12 Ember letters uploaded', notice.title)

    def test_documents_zip_includes_ember_letters(self):
        doc_type = DocumentType.objects.create(name='Birth Certificate')
        save_document(self.record_a, doc_type, SimpleUploadedFile('birth.pdf', PDF), '')
        ember, _ = save_ember_letter(
            self.record_a, 2025, 2, SimpleUploadedFile('ember_q2.pdf', PDF), user=self.registrar
        )

        zip_bytes = b''.join(backups.documents_zip())
        with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zf:
            names = zf.namelist()
        self.assertIn(ember.file.name, names)

    def test_seed_command_and_cleanup_migration_remove_legacy_ember_type(self):
        # Create legacy DocumentType and a Document attached to it
        legacy_type = DocumentType.objects.create(name='Seminary Evaluation / Grades / Ember Letter')
        doc = save_document(self.record_a, legacy_type, SimpleUploadedFile('old_sem.pdf', PDF), '')
        doc_path = doc.file.path
        self.assertTrue(os.path.exists(doc_path))

        from django.apps import apps
        ember_cleanup_migration.remove_seminary_ember_document_types(apps, None)

        self.assertFalse(DocumentType.objects.filter(name='Seminary Evaluation / Grades / Ember Letter').exists())
        self.assertFalse(Document.objects.filter(pk=doc.pk).exists())
        self.assertFalse(os.path.exists(doc_path))

        # Running seed_document_types does not re-create the combined Ember type,
        # while Seminary Evaluation and Supervisor's Evaluation are seeded for Deacon
        call_command('seed_document_types', stdout=io.StringIO())
        self.assertFalse(DocumentType.objects.filter(name__icontains='Ember').exists())
        self.assertTrue(DocumentType.objects.filter(name='Seminary Evaluation', required_from_rank='deacon').exists())
        self.assertTrue(DocumentType.objects.filter(name="Supervisor's Evaluation", required_from_rank='deacon').exists())
