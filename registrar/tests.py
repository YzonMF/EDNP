import shutil
import tempfile
import threading
from datetime import date, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.db import close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse

from accounts.testing import activate
from clergy.models import ClergyChild, ClergyEducation, ClergyProfile

from .forms import DocumentUploadForm
from . import reports
from .models import ClergyDeployment, ClergyRecord, Deanery, Document, DocumentType, GeneratedReport, IdCounter, Parish, save_document

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
        self.doc_type, _ = DocumentType.objects.get_or_create(name='Baptismal Certificate')

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

    def setUp(self):
        IdCounter.objects.get_or_create(name=ClergyRecord.COUNTER_NAME, defaults={'last_value': 0})

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
        self.doc_type, _ = DocumentType.objects.get_or_create(name='Baptismal Certificate')
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
        self.doc_type, _ = DocumentType.objects.get_or_create(name='Baptismal Certificate', defaults={'is_required': True})

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


class ClergyRecordsFilterTests(TestCase):
    def setUp(self):
        self.registrar = activate(User.objects.create_user('reg_filter', password='pw12345!x', is_staff=True))
        self.client.force_login(self.registrar)
        self.deanery_central = Deanery.objects.create(name='Central Deanery')
        self.deanery_bauko = Deanery.objects.create(name='Bauko Deanery')
        self.parish_central = Parish.objects.create(deanery=self.deanery_central, parish_name='St. Barnabas', place_name='Alab')
        self.parish_bauko = Parish.objects.create(deanery=self.deanery_bauko, parish_name='Holy Apostle', place_name='Abatan')

        self.r1 = ClergyRecord.objects.create(
            id='CLG-101', name='Fr. Juan', deanery=self.deanery_central,
            assignment=str(self.parish_central), rank='priest', status='Active'
        )
        self.r2 = ClergyRecord.objects.create(
            id='CLG-102', name='Fr. Pedro', deanery=None,
            assignment=str(self.parish_bauko), rank='deacon', status='Active'
        )
        self.r3 = ClergyRecord.objects.create(
            id='CLG-103', name='Bp. Marco', deanery=self.deanery_central,
            assignment='Cathedral', rank='bishop', status='On Leave'
        )

    def test_filter_by_deanery_fk(self):
        res = self.client.get(reverse('registrar:clergy_records'), {'deanery': self.deanery_central.id})
        ids = [r.id for r in res.context['records']]
        self.assertIn(self.r1.id, ids)
        self.assertIn(self.r3.id, ids)
        self.assertNotIn(self.r2.id, ids)
        self.assertContains(res, 'Central Deanery')

    def test_filter_by_deanery_fallback_parish(self):
        res = self.client.get(reverse('registrar:clergy_records'), {'deanery': self.deanery_bauko.id})
        ids = [r.id for r in res.context['records']]
        self.assertIn(self.r2.id, ids)
        self.assertNotIn(self.r1.id, ids)
        self.assertNotIn(self.r3.id, ids)

    def test_filter_by_rank(self):
        res = self.client.get(reverse('registrar:clergy_records'), {'rank': 'priest'})
        ids = [r.id for r in res.context['records']]
        self.assertEqual(ids, [self.r1.id])

        res_deacon = self.client.get(reverse('registrar:clergy_records'), {'rank': 'deacon'})
        self.assertEqual([r.id for r in res_deacon.context['records']], [self.r2.id])

    def test_filter_by_deanery_and_rank_combined(self):
        res = self.client.get(reverse('registrar:clergy_records'), {
            'deanery': self.deanery_central.id,
            'rank': 'bishop',
        })
        ids = [r.id for r in res.context['records']]
        self.assertEqual(ids, [self.r3.id])

    def test_filter_preserves_in_pagination_and_renders_selects(self):
        res = self.client.get(reverse('registrar:clergy_records'), {
            'deanery': self.deanery_central.id,
            'rank': 'priest',
            'status': 'Active',
        })
        self.assertContains(res, '<select name="deanery"')
        self.assertContains(res, '<select name="rank"')
        self.assertContains(res, '<select name="status"')
        self.assertEqual(res.context['deanery_filter'], str(self.deanery_central.id))
        self.assertEqual(res.context['rank_filter'], 'priest')


from clergy.models import ClergyChild, ClergyEducation
from .models import ClergyDeployment


class ClergyRecordEnhancementRegistrarTests(TestCase):
    def setUp(self):
        self.registrar = activate(User.objects.create_user('reg_enh', password='pw12345!x', is_staff=True))
        self.client.force_login(self.registrar)
        self.record = ClergyRecord.objects.create(
            id='CLG-301',
            name='Fr. Timothy',
            status='Active',
            rank='priest',
            diaconate_date=date(2010, 5, 1),
            diaconate_place='Bontoc Cathedral',
            ordination_date=date(2011, 5, 1),
            ordination_place='Baguio Cathedral',
        )
        self.clergy_user = activate(User.objects.create_user('CLG-301', password='pw12345!x'))
        self.profile = ClergyProfile.objects.create(
            user=self.clergy_user,
            record=self.record,
            place_of_birth='Bontoc',
            marriage_place='St. Paul Church',
            spouse_name='Sarah Timothy',
            spouse_birth_date=date(1985, 8, 20),
            marriage_date=date(2012, 6, 15),
        )
        ClergyChild.objects.create(
            profile=self.profile,
            name='Hannah Timothy',
            birth_date=date(2015, 3, 10),
            gender='Female',
        )
        ClergyEducation.objects.create(
            profile=self.profile,
            degree='Master of Divinity',
            institution="St. Andrew's Seminary",
            place='Quezon City',
            year_graduated=2009,
        )

    def test_registrar_can_save_diaconate_place(self):
        res = self.client.post(reverse('registrar:record_detail', args=[self.record.id]), {
            'name': 'Fr. Timothy',
            'status': 'Active',
            'rank': 'priest',
            'diaconate_date': '2010-05-01',
            'diaconate_place': 'Cathedral of Saint Mary & Saint John',
            'ordination_date': '2011-05-01',
            'ordination_place': 'Baguio Cathedral',
        }, follow=True)
        self.assertEqual(res.status_code, 200)
        self.record.refresh_from_db()
        self.assertEqual(self.record.diaconate_place, 'Cathedral of Saint Mary & Saint John')

    def test_registrar_can_add_and_delete_deployment(self):
        # Add deployment
        add_res = self.client.post(reverse('registrar:record_detail', args=[self.record.id]), {
            'action': 'add_deployment',
            'station_name': 'St. Peter Parish',
            'place_name': 'Sagada',
            'role': 'Rector',
            'start_date': '2018-01-01',
            'is_current': '1',
        }, follow=True)
        self.assertEqual(add_res.status_code, 200)

        dep = ClergyDeployment.objects.get(record=self.record, station_name='St. Peter Parish')
        self.assertEqual(dep.place_name, 'Sagada')
        self.assertTrue(dep.is_current)

        # Delete deployment
        del_res = self.client.post(reverse('registrar:record_detail', args=[self.record.id]), {
            'action': 'delete_deployment',
            'deployment_id': dep.id,
        }, follow=True)
        self.assertEqual(del_res.status_code, 200)
        self.assertFalse(ClergyDeployment.objects.filter(id=dep.id).exists())

    def test_registrar_view_renders_read_only_family_and_education(self):
        res = self.client.get(reverse('registrar:record_detail', args=[self.record.id]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Sarah Timothy')
        self.assertContains(res, 'St. Paul Church')
        self.assertContains(res, 'Hannah Timothy')
        self.assertContains(res, 'Master of Divinity')
        self.assertContains(res, 'Filed by Clergy')



class ContractExpiryDeploymentTests(TestCase):
    def setUp(self):
        self.registrar = activate(User.objects.create_user('reg_exp', password='pw12345!x', is_staff=True))
        self.client.force_login(self.registrar)
        self.record = ClergyRecord.objects.create(
            id='CLG-401',
            name='Fr. Barnaby',
            status='Active',
            rank='priest',
            assignment='St. Barnabas Parish - Alab',
            contract_end=date.today() - timedelta(days=10),
        )

    def test_expired_contract_automatically_recorded_in_deployments(self):
        # Visiting contract review automatically syncs expired contracts
        res = self.client.get(reverse('registrar:contract_review'))
        self.assertEqual(res.status_code, 200)

        dep = self.record.deployments.filter(end_date=self.record.contract_end).first()
        self.assertIsNotNone(dep)
        self.assertEqual(dep.station_name, 'St. Barnabas Parish')
        self.assertEqual(dep.place_name, 'Alab')
        self.assertEqual(dep.role, 'Priest-in-Charge')
        self.assertFalse(dep.is_current)

    def test_deployments_are_sorted_from_latest_to_oldest(self):
        # 1. Very old deployment (2012-2015)
        d_old = ClergyDeployment.objects.create(
            record=self.record, station_name='First Parish',
            start_date=date(2012, 1, 1), end_date=date(2015, 12, 31), is_current=False
        )
        # 2. Mid deployment (2016-2020)
        d_mid = ClergyDeployment.objects.create(
            record=self.record, station_name='Second Parish',
            start_date=date(2016, 1, 1), end_date=date(2020, 12, 31), is_current=False
        )
        # 3. Recently ended contract (2026)
        d_recent = ClergyDeployment.objects.create(
            record=self.record, station_name='Recent Station',
            end_date=date(2026, 1, 15), is_current=False
        )
        # 4. Current deployment
        d_current = ClergyDeployment.objects.create(
            record=self.record, station_name='Current Mission',
            start_date=date(2026, 2, 1), is_current=True
        )

        deps = list(self.record.deployments.all())
        self.assertEqual(len(deps), 4)
        # Ordered latest to oldest: Current first, then Recent (2026), then Mid (2020), then Old (2015)
        self.assertEqual(deps[0].id, d_current.id)
        self.assertEqual(deps[1].id, d_recent.id)
        self.assertEqual(deps[2].id, d_mid.id)
        self.assertEqual(deps[3].id, d_old.id)

    def test_no_duplicate_deployment_on_repeated_checks(self):
        self.record.record_expired_contract()
        self.record.record_expired_contract()
        self.assertEqual(self.record.deployments.filter(end_date=self.record.contract_end).count(), 1)

    def test_active_deployment_updated_when_contract_expires(self):
        active_dep = ClergyDeployment.objects.create(
            record=self.record, station_name='St. Barnabas Parish',
            role='Rector', is_current=True
        )
        self.assertTrue(active_dep.is_current)

        # Trigger expiration sync
        self.record.record_expired_contract()
        active_dep.refresh_from_db()
        self.assertFalse(active_dep.is_current)
        self.assertEqual(active_dep.end_date, self.record.contract_end)

    def test_future_contract_not_recorded_as_expired(self):
        future_record = ClergyRecord.objects.create(
            id='CLG-402',
            name='Fr. Future',
            status='Active',
            rank='priest',
            assignment='Epiphany Cathedral',
            contract_end=date.today() + timedelta(days=90),
        )
        result = future_record.record_expired_contract()
        self.assertFalse(result)
        self.assertFalse(future_record.deployments.exists())

    def test_management_command_sync_expired_contracts(self):
        from django.core.management import call_command
        import io
        out = io.StringIO()
        call_command('sync_expired_contracts', stdout=out)
        self.assertIn('Successfully synchronized', out.getvalue())


class ClericalDirectoryReportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('fr_john', 'john@example.com', 'Pass1234!')
        self.record = ClergyRecord.objects.create(
            id='CLG-501',
            name='The Rev. Fr. John Doe, M.Div.',
            rank='priest',
            status='Active',
            date_of_birth=date(1980, 5, 15),
            diaconate_date=date(2005, 6, 24),
            diaconate_place='Cathedral of All Saints, Bontoc',
            ordination_date=date(2006, 12, 8),
            ordination_place='Church of the Epiphany, La Trinidad',
            assignment='Cathedral of All Saints',
        )
        self.profile = ClergyProfile.objects.create(
            user=self.user,
            record=self.record,
            place_of_birth='Bontoc, Mountain Province',
            spouse_name='Jane Doe',
            spouse_birth_date=date(1982, 8, 20),
            marriage_date=date(2008, 1, 18),
            marriage_place='St. Paul Church, Sagada',
        )
        ClergyEducation.objects.create(
            profile=self.profile,
            degree='BA Philosophy',
            institution='University of the Philippines',
            place='Baguio',
            year_graduated=2001,
        )
        ClergyEducation.objects.create(
            profile=self.profile,
            degree='Master of Divinity',
            institution="St. Andrew's Theological Seminary",
            place='Quezon City',
            year_graduated=2005,
        )
        ClergyChild.objects.create(profile=self.profile, name='Mark Doe', birth_date=date(2010, 3, 10))
        ClergyChild.objects.create(profile=self.profile, name='Luke Doe', birth_date=date(2013, 7, 22))

        # Deployments: oldest to newest created, but manager / query must order latest to oldest
        ClergyDeployment.objects.create(
            record=self.record,
            station_name='St. Matthew Mission',
            place_name='Besao',
            role='Deacon',
            start_date=date(2007, 1, 1),
            end_date=date(2012, 12, 31),
            is_current=False,
        )
        ClergyDeployment.objects.create(
            record=self.record,
            station_name='St. Paul Church',
            place_name='Sagada',
            role='Priest-in-Charge',
            start_date=date(2013, 1, 1),
            end_date=date(2020, 12, 31),
            is_current=False,
        )
        ClergyDeployment.objects.create(
            record=self.record,
            station_name='Cathedral of All Saints',
            place_name='Bontoc',
            role='Dean',
            start_date=date(2021, 1, 1),
            is_current=True,
        )

    def test_clerical_directory_data_mapping_and_order(self):
        report = reports.build_report('clerical_directory')
        self.assertEqual(report['title'], 'Clerical Directory')
        self.assertEqual(len(report['entries']), 1)

        entry = report['entries'][0]
        self.assertEqual(entry['name'], 'The Rev. Fr. John Doe, M.Div.')
        self.assertEqual(entry['dob'], 'May 15, 1980 — Bontoc, Mountain Province')
        self.assertIn('2001 — University of the Philippines, Baguio (BA Philosophy)', entry['grad'])
        self.assertIn("2005 — St. Andrew's Theological Seminary, Quezon City (Master of Divinity)", entry['grad'])
        self.assertEqual(entry['od'], 'Jun 24, 2005 — Cathedral of All Saints, Bontoc')
        self.assertEqual(entry['op'], 'Dec 08, 2006 — Church of the Epiphany, La Trinidad')

        # Deployments: exactly 3, sorted latest to oldest
        self.assertEqual(len(entry['deployments']), 3)
        self.assertIn('Cathedral of All Saints', entry['deployments'][0])
        self.assertIn('Present', entry['deployments'][0])
        self.assertIn('St. Paul Church', entry['deployments'][1])
        self.assertIn('St. Matthew Mission', entry['deployments'][2])

        self.assertEqual(entry['spouse'], 'Jane Doe (b. Aug 20, 1982)')
        self.assertEqual(entry['marriage'], 'Jan 18, 2008 — St. Paul Church, Sagada')
        self.assertEqual(entry['children'], 'Mark Doe (b. Mar 10, 2010); Luke Doe (b. Jul 22, 2013)')

    def test_clerical_directory_html_and_pdf_rendering(self):
        from django.template.loader import render_to_string
        report = reports.build_report('clerical_directory')
        html = render_to_string('registrar/report_pdf.html', {'report': report})

        # Bolds name and abbreviations
        self.assertIn('<b>The Rev. Fr. John Doe, M.Div.</b>', html)
        self.assertIn('<b>DOB:</b>', html)
        self.assertIn('<b>Grad:</b>', html)
        self.assertIn('<b>OD:</b>', html)
        self.assertIn('<b>OP:</b>', html)
        self.assertIn('<b>Dep:</b>', html)
        self.assertIn('<b>S:</b>', html)
        self.assertIn('<b>M:</b>', html)
        self.assertIn('<b>C:</b>', html)

        # PDF generation produces valid binary
        pdf = reports.render_pdf(report)
        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_clerical_directory_fallbacks_for_empty_fields(self):
        minimal = ClergyRecord.objects.create(id='CLG-502', name='Fr. Minimal', rank='deacon')
        report = reports.build_report('clerical_directory')
        entry = [e for e in report['entries'] if e['id'] == 'CLG-502'][0]

        self.assertEqual(entry['dob'], '—')
        self.assertEqual(entry['grad'], '—')
        self.assertEqual(entry['od'], '—')
        self.assertEqual(entry['op'], '—')
        self.assertEqual(entry['deployments'], [])
        self.assertEqual(entry['spouse'], '—')
        self.assertEqual(entry['marriage'], '—')
        self.assertEqual(entry['children'], '—')

    def test_clerical_directory_query_count_flat(self):
        # Adding 10 more records should not increase queries linearly
        for i in range(10):
            r = ClergyRecord.objects.create(id=f'CLG-6{i:02d}', name=f'Fr. Batch {i}')
            u = User.objects.create_user(f'batch_{i}', f'b{i}@example.com', 'Pass1234!')
            p = ClergyProfile.objects.create(user=u, record=r, place_of_birth='Bontoc')
            ClergyDeployment.objects.create(record=r, station_name=f'Parish {i}', is_current=True)
            ClergyEducation.objects.create(profile=p, degree='B.Th.', institution='SATS', year_graduated=2010)
            ClergyChild.objects.create(profile=p, name=f'Child {i}', birth_date=date(2015, 1, 1))

        # Flat query count: 1 for ClergyRecord + Deanery + ClergyProfile (joined),
        # 1 for ClergyDeployment, 1 for ClergyChild, 1 for ClergyEducation
        with self.assertNumQueries(4):
            report = reports.build_report('clerical_directory')
        self.assertEqual(len(report['entries']), 11)

