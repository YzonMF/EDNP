import shutil
import tempfile
import threading
from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.db import close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse

from accounts.testing import activate
from clergy.models import ClergyProfile

from .forms import DocumentUploadForm
from . import reports
from .models import ClergyRecord, Deanery, Document, DocumentType, GeneratedReport, IdCounter, Parish, save_document

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
        self.registrar = activate(User.objects.create_user('reg', password='pw12345!x', is_staff=True))
        self.client.force_login(self.registrar)
        self.payload = {'name': 'Fr. Test', 'status': 'Active', 'rank': 'priest'}

    def test_creates_record_user_and_profile_together_and_returns_the_slip(self):
        r = self.client.post(reverse('registrar:create_record'), self.payload)
        record = ClergyRecord.objects.get(name='Fr. Test')
        self.assertEqual(record.id, 'CLG-001')
        # The username is the record ID, not something the registrar typed.
        self.assertTrue(ClergyProfile.objects.filter(user__username='CLG-001', record=record).exists())
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertTrue(r.content.startswith(b'%PDF'))
        self.assertEqual(r['Cache-Control'], 'no-store')

    def test_dashboard_includes_create_record_modal_directly(self):
        r = self.client.get(reverse('registrar:dashboard'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'id="newRecordButton"')
        self.assertContains(r, 'id="recordModal"')
        self.assertContains(r, 'id="deaneries-data"')

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
        self.assertContains(r, 'already in use')

    def test_slip_failure_rolls_everything_back(self):
        # If the PDF can't be produced, no account may survive whose password
        # was never printed.
        with mock.patch('registrar.views.slips.render_slips_pdf', side_effect=RuntimeError('pango missing')):
            with self.assertRaises(RuntimeError):
                self.client.post(reverse('registrar:create_record'), self.payload)
        self.assertFalse(ClergyRecord.objects.exists())
        self.assertFalse(User.objects.filter(username='CLG-001').exists())


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

    def test_a_file_gone_from_the_server_is_a_404_not_a_server_error(self):
        # What a redeploy leaves when uploads aren't on a persistent volume: the rows, no files.
        self.client.force_login(activate(User.objects.create_user('reg', password='pw12345!x', is_staff=True)))
        document = Document.objects.create(record=self.record, document_type=self.doc_type,
                                           file='clergy_documents/CLG-001/gone.pdf', submitted_date=date.today())
        report = GeneratedReport.objects.create(report_type='status_report', title='Status Report',
                                                file='generated_reports/gone.pdf')
        for url in (reverse('registrar:view_document', args=[document.id]),
                    reverse('registrar:download_document', args=[document.id]),
                    reverse('registrar:view_generated_report', args=[report.id]),
                    reverse('registrar:download_generated_report', args=[report.id])):
            self.assertEqual(self.client.get(url).status_code, 404, url)


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
    """A rejected upload must surface its reason in the page's flash messages
    (#flash-messages in base.html), which ui.js shows as an error toast."""

    def setUp(self):
        self.record = ClergyRecord.objects.create(id='CLG-001', name='Fr. A')
        self.doc_type = DocumentType.objects.create(name='Baptismal Certificate')
        self.bad = {'document_type': self.doc_type.pk, 'file': SimpleUploadedFile('x.html', b'<b>hi</b>')}

    def test_clergy_sees_rejection_toast(self):
        user = activate(User.objects.create_user('c', password='pw12345!x'))
        ClergyProfile.objects.create(user=user, record=self.record)
        self.client.force_login(user)
        r = self.client.post(reverse('clergy:my_requirements'), self.bad, follow=True)
        self.assertContains(r, '<span data-level="error">Only PDF, JPG and PNG files are allowed.</span>')

    def test_registrar_sees_rejection_toast(self):
        self.client.force_login(activate(User.objects.create_user('r', password='pw12345!x', is_staff=True)))
        r = self.client.post(reverse('registrar:record_detail', args=[self.record.id]), self.bad, follow=True)
        self.assertContains(r, '<span data-level="error">Only PDF, JPG and PNG files are allowed.</span>')


class BlankFieldTests(TestCase):
    """An empty field on the record page is shown empty, not as the word "None": the
    browser refused "None" as an email address, so a record without one couldn't be
    saved, and in the other fields "None" was saved as text."""

    def setUp(self):
        self.client.force_login(activate(User.objects.create_user('reg', password='pw12345!x', is_staff=True)))
        # Like a bulk-created record: only the name is filled in.
        self.record = ClergyRecord.objects.create(id='CLG-001', name='Fr. New')

    def test_empty_fields_are_shown_empty(self):
        page = self.client.get(reverse('registrar:record_detail', args=[self.record.id]))
        self.assertNotContains(page, 'value="None"')
        self.assertNotContains(page, '>None</textarea>')

    def test_saving_the_page_as_shown_stores_no_text_none(self):
        # What the browser now sends for the untouched empty fields: empty strings.
        blanks = dict.fromkeys(['email', 'address', 'ordination_place', 'bishop_name',
                                'previous_assignment', 'service_history', 'leave_reason'], '')
        self.client.post(reverse('registrar:record_detail', args=[self.record.id]),
                         {'name': 'Fr. New', 'status': 'Active', 'rank': 'postulancy', **blanks})
        self.record.refresh_from_db()
        for field in blanks:
            self.assertIn(getattr(self.record, field), (None, ''), field)


class PerDeaneryReportTests(TestCase):
    def test_uses_record_deanery_fk_and_falls_back_to_parish_mapping_when_unset(self):
        central = Deanery.objects.create(name='Central Deanery')
        southern = Deanery.objects.create(name='Southern Deanery')
        Parish.objects.create(deanery=southern, parish_name='St. Luke', place_name='Baguio')

        # Has real deanery FK set (even with a custom assignment text not matching a parish)
        ClergyRecord.objects.create(id='CLG-001', name='Fr. Central', deanery=central, assignment='Diocesan Office')
        # Legacy record with no deanery FK, matched via parish label
        ClergyRecord.objects.create(id='CLG-002', name='Fr. Southern', assignment='St. Luke - Baguio')
        # Neither
        ClergyRecord.objects.create(id='CLG-003', name='Fr. Unassigned')

        sections = reports.build_report('per_deanery')['sections']
        by_heading = {s['heading']: [r[0] for r in s['rows']] for s in sections}
        self.assertEqual(by_heading['Central Deanery'], ['Fr. Central'])
        self.assertEqual(by_heading['Southern Deanery'], ['Fr. Southern'])
        self.assertEqual(by_heading['Unassigned / Not Matched to a Parish'], ['Fr. Unassigned'])


class PaginationAndQueryCountTests(TestCase):
    def setUp(self):
        self.registrar = activate(User.objects.create_user('reg', password='pw12345!x', is_staff=True))
        self.client.force_login(self.registrar)
        self.doc_type = DocumentType.objects.create(name='Baptismal Certificate', is_required=True)

    def test_clergy_records_and_documents_overview_do_not_run_n_plus_one_queries(self):
        for i in range(1, 15):
            r = ClergyRecord.objects.create(id=f'CLG-{i:03d}', name=f'Fr. {i}')
            Document.objects.create(record=r, document_type=self.doc_type, file='a.pdf', submitted_date=date.today())
        # 14 records on the page, but the query count stays flat (no per-record checklist queries)
        with self.assertNumQueries(15):
            res = self.client.get(reverse('registrar:clergy_records'))
        self.assertEqual(len(res.context['records']), 14)
        self.assertEqual(res.context['records'][0].doc_summary['submitted'], 1)

        with self.assertNumQueries(10):
            res2 = self.client.get(reverse('registrar:documents_overview'))
        self.assertEqual(len(res2.context['rows']), 14)

    def test_clergy_records_paginates_at_100_per_page_preserving_filters(self):
        for i in range(1, 106):
            ClergyRecord.objects.create(id=f'CLG-{i:03d}', name=f'Fr. {i:03d}', status='Active')
        page1 = self.client.get(reverse('registrar:clergy_records'), {'status': 'Active'})
        self.assertEqual(len(page1.context['records']), 100)
        self.assertTrue(page1.context['page'].has_next())
        self.assertContains(page1, 'status=Active&amp;page=2')

        page2 = self.client.get(reverse('registrar:clergy_records'), {'status': 'Active', 'page': 2})
        self.assertEqual(len(page2.context['records']), 5)

    def test_generated_reports_paginates_at_10_per_page(self):
        for i in range(12):
            GeneratedReport.objects.create(report_type='status_report', title=f'Report {i}', file='r.pdf',
                                           generated_by=self.registrar)
        page1 = self.client.get(reverse('registrar:generate_report'))
        self.assertEqual(len(page1.context['generated_reports']), 10)
        self.assertTrue(page1.context['page'].has_next())

        page2 = self.client.get(reverse('registrar:generate_report'), {'page': 2})
        self.assertEqual(len(page2.context['generated_reports']), 2)
