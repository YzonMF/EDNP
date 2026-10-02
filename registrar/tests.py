import shutil
import tempfile
import threading
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.db import close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse

from clergy.models import ClergyProfile

from .forms import DocumentUploadForm
from .models import ClergyRecord, Document, DocumentType, IdCounter, save_document

User = get_user_model()

PDF = b'%PDF-1.4\n%fake but correctly-headed pdf\n'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 20


class RecordIdTests(TestCase):
    def test_ids_are_sequential_and_start_after_existing(self):
        ClergyRecord.objects.create(id='CLG-009', name='Existing')
        IdCounter.objects.update_or_create(name='clergy_record', defaults={'last_value': 9})
        self.assertEqual(ClergyRecord.next_id(), 'CLG-010')
        self.assertEqual(ClergyRecord.allocate_id(), 'CLG-010')
        self.assertEqual(ClergyRecord.allocate_id(), 'CLG-011')

    def test_allocate_skips_ids_that_already_exist(self):
        # e.g. inserted by hand / the seeder without going through the counter
        for n in (1, 2, 3):
            ClergyRecord.objects.create(id=f'CLG-{n:03d}', name=f'R{n}')
        self.assertEqual(ClergyRecord.allocate_id(), 'CLG-004')

    def test_preview_does_not_reserve(self):
        self.assertEqual(ClergyRecord.next_id(), 'CLG-001')
        self.assertEqual(ClergyRecord.next_id(), 'CLG-001')

    def test_ids_past_999_still_work(self):
        IdCounter.objects.update_or_create(name='clergy_record', defaults={'last_value': 999})
        self.assertEqual(ClergyRecord.allocate_id(), 'CLG-1000')


class CreateRecordTests(TestCase):
    def setUp(self):
        self.registrar = User.objects.create_user('reg', password='pw12345!x', is_staff=True)
        self.client.force_login(self.registrar)
        self.payload = {'name': 'Fr. Test', 'status': 'Active', 'rank': 'priest',
                        'new_username': 'frtest', 'new_password': 'Str0ng!Passw0rd#1'}

    def test_creates_record_user_and_profile_together(self):
        self.client.post(reverse('registrar:create_record'), self.payload)
        record = ClergyRecord.objects.get(name='Fr. Test')
        self.assertEqual(record.id, 'CLG-001')
        self.assertTrue(ClergyProfile.objects.filter(user__username='frtest', record=record).exists())

    def test_failure_midway_leaves_nothing_behind_and_returns_the_id(self):
        with mock.patch.object(User.objects, 'create_user', side_effect=IntegrityError):
            self.client.post(reverse('registrar:create_record'), self.payload)
        self.assertFalse(ClergyRecord.objects.exists())
        self.assertFalse(IdCounter.objects.filter(last_value__gt=0).exists())  # rolled back with it
        self.client.post(reverse('registrar:create_record'), self.payload)
        self.assertEqual(ClergyRecord.objects.get().id, 'CLG-001')

    def test_username_race_shows_friendly_error_not_a_crash(self):
        with mock.patch.object(User.objects, 'create_user', side_effect=IntegrityError):
            r = self.client.post(reverse('registrar:create_record'), self.payload, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'already taken')


class DocumentTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.record = ClergyRecord.objects.create(id='CLG-001', name='Fr. A')
        self.doc_type = DocumentType.objects.create(name='Baptismal Certificate')

    def _form(self, name, content):
        return DocumentUploadForm({'document_type': self.doc_type.pk, 'notes': ''},
                                  {'file': SimpleUploadedFile(name, content)})

    def test_replacing_a_document_deletes_the_old_file(self):
        with self.captureOnCommitCallbacks(execute=True):
            first = save_document(self.record, self.doc_type, SimpleUploadedFile('a.pdf', PDF), '')
        old_path = first.file.path
        with self.captureOnCommitCallbacks(execute=True):
            second = save_document(self.record, self.doc_type, SimpleUploadedFile('b.pdf', PDF), '')
        self.assertEqual(Document.objects.count(), 1)
        self.assertNotEqual(old_path, second.file.path)
        import os
        self.assertFalse(os.path.exists(old_path))
        self.assertTrue(os.path.exists(second.file.path))

    def test_accepts_real_pdf_and_png(self):
        self.assertTrue(self._form('scan.pdf', PDF).is_valid())
        self.assertTrue(self._form('photo.PNG', PNG).is_valid())

    def test_rejects_disguised_or_disallowed_files(self):
        self.assertFalse(self._form('evil.pdf', b'<html><script>alert(1)</script></html>').is_valid())
        self.assertFalse(self._form('evil.html', b'<html></html>').is_valid())
        self.assertFalse(self._form('logo.svg', b'<svg onload="x()"/>').is_valid())
        self.assertFalse(self._form('macro.docx', b'PK\x03\x04').is_valid())

    def test_rejects_files_over_10mb(self):
        self.assertFalse(self._form('big.pdf', PDF + b'0' * (10 * 1024 * 1024)).is_valid())


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentIdAllocationTests(TransactionTestCase):
    """Real simultaneous creators (separate DB connections). Skipped on SQLite,
    which has no row locking — run against MySQL to exercise this."""

    def test_simultaneous_creates_never_share_an_id(self):
        n = 12
        ids, errors = [], []
        barrier = threading.Barrier(n)

        def create():
            try:
                barrier.wait()
                with transaction.atomic():
                    new_id = ClergyRecord.allocate_id()
                    ClergyRecord.objects.create(id=new_id, name='Concurrent')
                ids.append(new_id)
            except Exception as exc:  # noqa: BLE001 - surface any failure in the assert
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=create) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(set(ids)), n)
        self.assertEqual(ClergyRecord.objects.count(), n)


class UploadFeedbackTests(TestCase):
    """A rejected upload must surface its reason as a *valid* showToast call —
    an unquoted message is a JS syntax error and the toast silently never shows."""

    def setUp(self):
        self.record = ClergyRecord.objects.create(id='CLG-001', name='Fr. A')
        self.doc_type = DocumentType.objects.create(name='Baptismal Certificate')
        self.bad = {'document_type': self.doc_type.pk, 'file': SimpleUploadedFile('x.html', b'<b>hi</b>')}

    def test_clergy_sees_rejection_toast(self):
        user = User.objects.create_user('c', password='pw12345!x')
        ClergyProfile.objects.create(user=user, record=self.record)
        self.client.force_login(user)
        r = self.client.post(reverse('clergy:my_requirements'), self.bad, follow=True)
        self.assertContains(r, "showToast('Only PDF, JPG and PNG files are allowed.', 'error')")

    def test_registrar_sees_rejection_toast(self):
        self.client.force_login(User.objects.create_user('r', password='pw12345!x', is_staff=True))
        r = self.client.post(reverse('registrar:record_detail', args=[self.record.id]), self.bad, follow=True)
        self.assertContains(r, "showToast('Only PDF, JPG and PNG files are allowed.', 'error')")
