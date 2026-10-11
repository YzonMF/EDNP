"""Archiving. Generated reports: archive (one, or the ticked ones together), still downloadable
and printable while archived, restore, Delete now, Delete selected and Clear all (archived only),
and deleted for good 30 days after archiving. Clergy records: only retired ones can be archived, they leave the
working lists, and they are never deleted."""

import io
import os
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AccountAuditLog
from accounts.testing import activate
from clergy.models import ClergyProfile
from ednp.testing import MediaFolderMixin

from . import reports
from .archive import purge_expired_reports
from .models import REPORT_ARCHIVE_DAYS, ClergyRecord, GeneratedReport

User = get_user_model()
PDF = b'%PDF-1.4\n%a saved report\n'
REPORTS_PAGE = reverse('registrar:generate_report')
RECORDS_PAGE = reverse('registrar:clergy_records')
ARCHIVE_TICKED = reverse('registrar:archive_selected_reports')
DELETE_ARCHIVED = reverse('registrar:delete_archived_reports')


def url(name, obj):
    return reverse(f'registrar:{name}', args=[obj.pk])


def flashed(response):
    return [str(message) for message in get_messages(response.wsgi_request)]


class RegistrarTestCase(MediaFolderMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.registrar = activate(User.objects.create_user('reg', password='pw12345!x', is_staff=True))
        self.registrar.first_name = 'Rev. Ana Reyes'
        self.registrar.save()
        self.client.force_login(self.registrar)


class ReportArchiveTests(RegistrarTestCase):
    def make_report(self, title='Status Report', archived_days_ago=None):
        report = GeneratedReport(report_type='status_report', title=title, generated_by=self.registrar)
        report.file.save('status_report.pdf', ContentFile(PDF), save=True)
        if archived_days_ago is not None:
            GeneratedReport.objects.filter(pk=report.pk).update(
                archived_at=timezone.now() - timedelta(days=archived_days_ago), archived_by=self.registrar)
            report.refresh_from_db()
        return report

    def test_archiving_moves_the_report_to_the_archived_list(self):
        report = self.make_report()
        response = self.client.post(url('archive_generated_report', report))
        self.assertRedirects(response, REPORTS_PAGE, fetch_redirect_response=False)
        report.refresh_from_db()
        self.assertIsNotNone(report.archived_at)
        self.assertEqual(report.archived_by, self.registrar)

        page = self.client.get(REPORTS_PAGE)
        self.assertNotIn(report, page.context['generated_reports'])
        self.assertEqual(page.context['archived_reports'], [report])
        self.assertContains(page, f'{REPORT_ARCHIVE_DAYS} days left')
        self.assertContains(page, url('restore_generated_report', report))

        entry = AccountAuditLog.objects.get()
        self.assertEqual((entry.action, entry.actor_name, entry.target), ('report_archived', 'Rev. Ana Reyes', None))
        self.assertIn('Status Report', entry.note)

    def test_an_archived_report_can_still_be_downloaded_and_printed(self):
        report = self.make_report(archived_days_ago=5)
        download = self.client.get(url('download_generated_report', report))
        self.assertEqual(b''.join(download.streaming_content), PDF)
        self.assertIn('attachment', download['Content-Disposition'])
        printable = self.client.get(url('view_generated_report', report))   # what Print opens
        self.assertEqual(b''.join(printable.streaming_content), PDF)
        self.assertIn('inline', printable['Content-Disposition'])

    def test_days_left_count_down_from_the_day_it_was_archived(self):
        self.make_report(archived_days_ago=10)
        page = self.client.get(REPORTS_PAGE)
        self.assertEqual(page.context['archived_reports'][0].days_left, REPORT_ARCHIVE_DAYS - 10)
        self.assertContains(page, f'{REPORT_ARCHIVE_DAYS - 10} days left')

    def test_a_plain_link_changes_nothing(self):
        report = self.make_report()
        for name in ('archive_generated_report', 'delete_generated_report'):
            self.client.get(url(name, report))
        report.refresh_from_db()
        self.assertIsNone(report.archived_at)
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_restore_brings_it_back(self):
        report = self.make_report(archived_days_ago=29)
        self.client.post(url('restore_generated_report', report))
        report.refresh_from_db()
        self.assertIsNone(report.archived_at)
        self.assertIsNone(report.archived_by)
        self.assertIn(report, self.client.get(REPORTS_PAGE).context['generated_reports'])
        self.assertEqual(AccountAuditLog.objects.get().action, 'report_restored')

    def test_a_report_that_is_not_archived_cannot_be_deleted(self):
        report = self.make_report()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url('delete_generated_report', report))
        self.assertEqual(flashed(response), ['Archive a report before deleting it.'])
        self.assertTrue(GeneratedReport.objects.filter(pk=report.pk).exists())
        self.assertTrue(os.path.exists(report.file.path))
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_delete_now_removes_the_row_and_the_file(self):
        report = self.make_report(archived_days_ago=1)
        path = report.file.path
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url('delete_generated_report', report))
        self.assertFalse(GeneratedReport.objects.filter(pk=report.pk).exists())
        self.assertFalse(os.path.exists(path))
        entry = AccountAuditLog.objects.get()
        self.assertEqual((entry.action, entry.actor_name), ('report_deleted', 'Rev. Ana Reyes'))

    def test_reports_30_days_in_the_archive_are_deleted_when_the_page_opens(self):
        expired = self.make_report('Old Report', archived_days_ago=REPORT_ARCHIVE_DAYS)
        recent = self.make_report('Recent Report', archived_days_ago=REPORT_ARCHIVE_DAYS - 1)
        current = self.make_report('Current Report')
        path = expired.file.path

        with self.captureOnCommitCallbacks(execute=True):
            page = self.client.get(REPORTS_PAGE)
        self.assertFalse(GeneratedReport.objects.filter(pk=expired.pk).exists())
        self.assertFalse(os.path.exists(path))
        self.assertEqual(page.context['archived_reports'], [recent])
        self.assertIn(current, page.context['generated_reports'])

        entry = AccountAuditLog.objects.get()
        self.assertEqual((entry.action, entry.actor_name, entry.actor), ('report_expired', 'Automatic cleanup', None))
        self.assertIn('Old Report', entry.note)

        self.client.get(REPORTS_PAGE)   # nothing left to clean up, so nothing more is logged
        self.assertEqual(AccountAuditLog.objects.count(), 1)

    def test_an_expired_report_cannot_be_opened_from_an_old_link(self):
        report = self.make_report(archived_days_ago=REPORT_ARCHIVE_DAYS + 5)
        for name in ('download_generated_report', 'view_generated_report', 'preview_generated_report'):
            self.assertEqual(self.client.get(url(name, report)).status_code, 404, name)

    def test_the_command_cleans_up_too(self):
        self.make_report(archived_days_ago=REPORT_ARCHIVE_DAYS + 1)
        self.make_report(archived_days_ago=2)
        out = io.StringIO()
        call_command('purge_archived_reports', stdout=out)
        self.assertIn('Deleted 1 archived report.', out.getvalue())
        self.assertEqual(GeneratedReport.objects.count(), 1)

    def test_clergy_users_cannot_archive_or_delete_reports(self):
        report = self.make_report(archived_days_ago=1)
        record = ClergyRecord.objects.create(id='CLG-001', name='Fr. Juan')
        clergy = User.objects.create_user('CLG-001', password='pw12345!x')
        ClergyProfile.objects.create(user=clergy, record=record)
        other = Client()
        other.force_login(activate(clergy))
        other.post(url('restore_generated_report', report))
        other.post(url('delete_generated_report', report))
        report.refresh_from_db()
        self.assertIsNotNone(report.archived_at)


class BulkReportTests(RegistrarTestCase):
    make_report = ReportArchiveTests.make_report

    def archived(self):
        return set(GeneratedReport.objects.filter(archived_at__isnull=False))

    def test_the_buttons_only_show_when_there_is_something_to_act_on(self):
        page = self.client.get(REPORTS_PAGE)
        self.assertNotContains(page, ARCHIVE_TICKED)
        self.assertNotContains(page, DELETE_ARCHIVED)

        report = self.make_report()
        old = self.make_report('Old Report', archived_days_ago=3)
        page = self.client.get(REPORTS_PAGE)
        self.assertContains(page, ARCHIVE_TICKED)
        self.assertContains(page, f'name="report_ids" value="{report.pk}" form="bulkArchiveForm"')
        self.assertContains(page, 'id="pickAllReports"')
        self.assertContains(page, DELETE_ARCHIVED, count=2)   # Delete selected and Clear all
        self.assertContains(page, f'name="report_ids" value="{old.pk}" form="bulkDeleteForm"')
        self.assertContains(page, 'id="pickAllArchived"')
        self.assertContains(page, 'Delete all 1 archived report now?')

    def test_the_ticked_reports_are_archived_together_and_each_is_logged(self):
        first, second, third = self.make_report('First'), self.make_report('Second'), self.make_report('Third')
        response = self.client.post(ARCHIVE_TICKED, {'report_ids': [first.pk, second.pk]})
        self.assertRedirects(response, REPORTS_PAGE, fetch_redirect_response=False)
        self.assertTrue(flashed(response)[0].startswith('2 reports were archived. They will be deleted on '))
        self.assertEqual(self.archived(), {first, second})
        self.assertEqual(sorted(entry.note.split(',')[0] for entry in AccountAuditLog.objects.filter(action='report_archived')),
                         ['First', 'Second'])
        self.assertEqual(AccountAuditLog.objects.count(), 2)

    def test_one_ticked_report_reads_naturally(self):
        report = self.make_report()
        response = self.client.post(ARCHIVE_TICKED, {'report_ids': [report.pk]})
        self.assertTrue(flashed(response)[0].startswith('1 report was archived. It will be deleted on '))

    def test_nothing_ticked_archives_nothing(self):
        self.make_report()
        response = self.client.post(ARCHIVE_TICKED)
        self.assertEqual(flashed(response), ['Tick the reports to archive first.'])
        self.assertEqual(self.archived(), set())

    def test_a_report_already_archived_or_one_that_does_not_exist_is_passed_over(self):
        archived = self.make_report('Archived', archived_days_ago=2)
        response = self.client.post(ARCHIVE_TICKED, {'report_ids': [archived.pk, 'abc', '99999']})
        self.assertRedirects(response, REPORTS_PAGE, fetch_redirect_response=False)
        self.assertEqual(GeneratedReport.objects.get().archived_at, archived.archived_at)   # not archived again
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_delete_selected_deletes_only_the_ticked_archived_reports(self):
        first = self.make_report('First', archived_days_ago=1)
        second = self.make_report('Second', archived_days_ago=2)
        kept = self.make_report('Kept', archived_days_ago=3)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(DELETE_ARCHIVED, {'report_ids': [first.pk, second.pk]})
        self.assertEqual(flashed(response), ['2 archived reports were deleted.'])
        self.assertEqual(list(GeneratedReport.objects.all()), [kept])
        self.assertTrue(os.path.exists(kept.file.path))
        self.assertEqual(AccountAuditLog.objects.filter(action='report_deleted').count(), 2)

    def test_one_ticked_archived_report_reads_naturally(self):
        report = self.make_report(archived_days_ago=1)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(DELETE_ARCHIVED, {'report_ids': [report.pk]})
        self.assertEqual(flashed(response), ['1 archived report was deleted.'])

    def test_nothing_ticked_deletes_nothing(self):
        self.make_report(archived_days_ago=1)
        response = self.client.post(DELETE_ARCHIVED)
        self.assertEqual(flashed(response), ['Tick the archived reports to delete first.'])
        self.assertEqual(GeneratedReport.objects.count(), 1)

    def test_clear_all_deletes_every_archived_report_listed_with_its_file(self):
        first = self.make_report('First', archived_days_ago=1)
        second = self.make_report('Second', archived_days_ago=20)
        current = self.make_report('Current')
        paths = [first.file.path, second.file.path]
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(DELETE_ARCHIVED, {'report_ids': [first.pk, second.pk]})
        self.assertRedirects(response, REPORTS_PAGE, fetch_redirect_response=False)
        self.assertEqual(flashed(response), ['2 archived reports were deleted.'])
        self.assertEqual(list(GeneratedReport.objects.all()), [current])
        self.assertFalse(any(os.path.exists(path) for path in paths))
        self.assertTrue(os.path.exists(current.file.path))
        self.assertEqual(sorted(entry.note.split(',')[0] for entry in AccountAuditLog.objects.filter(action='report_deleted')),
                         ['First', 'Second'])

    def test_clear_all_leaves_a_report_archived_after_the_page_was_opened(self):
        shown = self.make_report('Shown', archived_days_ago=1)
        later = self.make_report('Archived meanwhile', archived_days_ago=0)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(DELETE_ARCHIVED, {'report_ids': [shown.pk]})
        self.assertEqual(list(GeneratedReport.objects.all()), [later])

    def test_clear_all_never_deletes_a_report_that_is_not_archived(self):
        current = self.make_report()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(DELETE_ARCHIVED, {'report_ids': [current.pk]})
        self.assertTrue(os.path.exists(current.file.path))
        self.assertEqual(list(GeneratedReport.objects.all()), [current])
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_a_plain_link_changes_nothing(self):
        current, archived = self.make_report('Current'), self.make_report('Archived', archived_days_ago=1)
        self.client.get(ARCHIVE_TICKED, {'report_ids': [current.pk]})
        self.client.get(DELETE_ARCHIVED, {'report_ids': [archived.pk]})
        self.assertEqual(self.archived(), {archived})
        self.assertEqual(GeneratedReport.objects.count(), 2)
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_clergy_users_can_do_neither(self):
        current, archived = self.make_report('Current'), self.make_report('Archived', archived_days_ago=1)
        record = ClergyRecord.objects.create(id='CLG-001', name='Fr. Juan')
        clergy = User.objects.create_user('CLG-001', password='pw12345!x')
        ClergyProfile.objects.create(user=clergy, record=record)
        other = Client()
        other.force_login(activate(clergy))
        other.post(ARCHIVE_TICKED, {'report_ids': [current.pk]})
        other.post(DELETE_ARCHIVED, {'report_ids': [archived.pk]})
        self.assertEqual(self.archived(), {archived})
        self.assertEqual(GeneratedReport.objects.count(), 2)


class RecordArchiveTests(RegistrarTestCase):
    def setUp(self):
        super().setUp()
        self.retired = ClergyRecord.objects.create(id='CLG-001', name='Fr. Luis Mendoza', status='Retired',
                                                   rank='priest', retirement_date=date(2019, 12, 31))
        self.active = ClergyRecord.objects.create(id='CLG-002', name='Fr. Pedro Santos', status='Active',
                                                  rank='priest', contract_end=date.today() + timedelta(days=60))

    def archive(self, record, **extra):
        return self.client.post(url('archive_record', record), extra)

    def test_only_retired_clergy_can_be_archived(self):
        response = self.archive(self.active)
        self.assertEqual(flashed(response),
                         ["Only retired clergy can be archived. Set Fr. Pedro Santos's status to Retired first."])
        self.active.refresh_from_db()
        self.assertFalse(self.active.is_archived)

        self.archive(self.retired)
        self.retired.refresh_from_db()
        self.assertTrue(self.retired.is_archived)
        self.assertEqual(self.retired.archived_by, self.registrar)

    def test_archiving_is_logged_against_the_clergy_members_account(self):
        clergy = User.objects.create_user('CLG-001', password='pw12345!x')
        ClergyProfile.objects.create(user=clergy, record=self.retired)
        self.archive(self.retired)
        entry = AccountAuditLog.objects.get()
        self.assertEqual((entry.action, entry.target, entry.target_name, entry.note),
                         ('record_archived', clergy, 'Fr. Luis Mendoza', 'CLG-001'))

    def test_archived_records_leave_the_working_lists(self):
        ClergyRecord.objects.filter(pk=self.active.pk).update(status='Retired')   # with a contract end still on file
        self.active.refresh_from_db()
        self.archive(self.active)

        records = self.client.get(RECORDS_PAGE).context['records']
        self.assertEqual([r.id for r in records], ['CLG-001'])
        archived = self.client.get(RECORDS_PAGE + '?view=archived').context['records']
        self.assertEqual([r.id for r in archived], ['CLG-002'])

        dashboard = self.client.get(reverse('registrar:dashboard'))
        self.assertEqual((dashboard.context['stats']['total'], dashboard.context['stats']['archived']), (1, 1))
        self.assertNotIn(self.active, list(dashboard.context['recent_records']))
        self.assertContains(dashboard, 'not counting 1 archived')

        documents = self.client.get(reverse('registrar:documents_overview')).context['rows']
        self.assertEqual([row['record'].id for row in documents], ['CLG-001'])
        self.assertEqual(self.client.get(reverse('registrar:contract_review')).context['rows'], [])

    def test_search_stays_within_the_list_that_is_showing(self):
        self.archive(self.retired)
        self.assertEqual(list(self.client.get(RECORDS_PAGE, {'q': 'Mendoza'}).context['records']), [])
        found = self.client.get(RECORDS_PAGE, {'q': 'Mendoza', 'view': 'archived'}).context['records']
        self.assertEqual([r.id for r in found], ['CLG-001'])

    def test_archived_records_still_appear_in_reports(self):
        self.archive(self.retired)
        rows = reports.build_report('status_report')['sections'][0]['rows']
        self.assertIn('Fr. Luis Mendoza', [row[0] for row in rows])

    def test_restore_puts_it_back_on_the_list(self):
        self.archive(self.retired)
        response = self.client.post(url('restore_record', self.retired))
        self.assertRedirects(response, RECORDS_PAGE + '?view=archived', fetch_redirect_response=False)
        self.retired.refresh_from_db()
        self.assertFalse(self.retired.is_archived)
        self.assertIn('CLG-001', [r.id for r in self.client.get(RECORDS_PAGE).context['records']])
        self.assertEqual(list(AccountAuditLog.objects.values_list('action', flat=True)),
                         ['record_restored', 'record_archived'])

    def test_buttons_on_the_record_page_return_to_it(self):
        response = self.archive(self.retired, next='record')
        self.assertRedirects(response, url('record_detail', self.retired), fetch_redirect_response=False)
        page = self.client.get(url('record_detail', self.retired))
        self.assertContains(page, 'Restore from Archive')
        self.assertContains(page, 'Back to Archived Records')

    def test_the_status_of_an_archived_record_cannot_change_until_it_is_restored(self):
        self.archive(self.retired)
        detail = url('record_detail', self.retired)
        response = self.client.post(detail, {'name': 'Fr. Luis Mendoza', 'status': 'Active', 'rank': 'priest'})
        self.assertEqual(flashed(response)[-1], 'This record is archived. Restore it before changing its status.')
        self.retired.refresh_from_db()
        self.assertEqual(self.retired.status, 'Retired')

        self.client.post(detail, {'name': 'Rev. Fr. Luis Mendoza', 'status': 'Retired', 'rank': 'priest'})
        self.retired.refresh_from_db()
        self.assertEqual(self.retired.name, 'Rev. Fr. Luis Mendoza')   # other edits still save
        self.assertTrue(self.retired.is_archived)

    def test_archived_records_are_never_deleted(self):
        self.archive(self.retired)
        ClergyRecord.objects.filter(pk=self.retired.pk).update(archived_at=timezone.now() - timedelta(days=3650))
        purge_expired_reports()
        self.client.get(REPORTS_PAGE)
        self.assertTrue(ClergyRecord.objects.filter(pk=self.retired.pk).exists())

    def test_the_clergy_member_can_still_sign_in_to_their_portal(self):
        clergy = User.objects.create_user('CLG-001', password='pw12345!x')
        ClergyProfile.objects.create(user=clergy, record=self.retired)
        self.archive(self.retired)
        portal = Client()
        portal.force_login(activate(clergy))
        self.assertEqual(portal.get(reverse('clergy:dashboard')).status_code, 200)
