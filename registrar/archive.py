"""Archiving: taking things off the registrar's working lists without losing them by accident.

- Generated reports. Archive moves a report to Archived Reports, where it can still be
  downloaded and printed. REPORT_ARCHIVE_DAYS after that it is deleted for good, row and
  stored PDF, unless it was restored first; Delete now does it straight away. Only an
  archived report can be deleted, so no report disappears in one click.
- Clergy records. Only a retired clergy member's record can be archived. It leaves Clergy
  Records, the Dashboard, Documents and Contract Review, but is never deleted: it keeps its
  documents and portal account, still appears in reports (they are the diocese's official
  record), and can be restored at any time.

Every archive, restore and delete is written to the account activity log. Each change is a
single conditional UPDATE or DELETE, so two registrars pressing the same button at once
change, and log, it only once.
"""

import math
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from accounts import audit

from .models import REPORT_ARCHIVE_DAYS, ClergyRecord, GeneratedReport

ARCHIVABLE_STATUS = 'Retired'
AUTOMATIC_CLEANUP = 'Automatic cleanup'


class ArchiveError(Exception):
    """The change isn't allowed; the message is safe to show the registrar."""


# --- Generated reports ----------------------------------------------------

def _report_note(report):
    return f'{report.title}, generated {report.generated_at:%b %d, %Y %H:%M}'


def archive_report(report, by):
    """True if this call archived it; False if it already was."""
    with transaction.atomic():
        changed = GeneratedReport.objects.filter(pk=report.pk, archived_at__isnull=True).update(
            archived_at=timezone.now(), archived_by=by)
        if changed:
            audit.record(by, None, 'report_archived', _report_note(report))
    return bool(changed)


def restore_report(report, by):
    """True if this call restored it; False if it wasn't archived (or is already gone)."""
    with transaction.atomic():
        changed = GeneratedReport.objects.filter(pk=report.pk, archived_at__isnull=False).update(
            archived_at=None, archived_by=None)
        if changed:
            audit.record(by, None, 'report_restored', _report_note(report))
    return bool(changed)


def _delete_report(report, **still):
    """Deletes the row if it still matches `still`, then its stored PDF once that has
    committed, so a delete that is rolled back never loses the file."""
    deleted, _ = GeneratedReport.objects.filter(pk=report.pk, **still).delete()
    if deleted and report.file.name:
        name, storage = report.file.name, report.file.storage
        transaction.on_commit(lambda: storage.delete(name))   # a file already gone is fine
    return bool(deleted)


def delete_report(report, by):
    """Delete now, for an archived report only. True if this call deleted it."""
    if report.archived_at is None:
        raise ArchiveError('Archive a report before deleting it.')
    with transaction.atomic():
        deleted = _delete_report(report, archived_at__isnull=False)
        if deleted:
            audit.record(by, None, 'report_deleted', _report_note(report))
    return deleted


def archive_reports(reports, by):
    """Archive, for several reports at once (the ticked ones). Returns how many this call archived."""
    return sum(archive_report(report, by) for report in reports)


def delete_reports(reports, by):
    """Delete now, for several archived reports at once (Delete selected, Clear all), each
    logged as its own deletion. Returns how many this call deleted."""
    return sum(delete_report(report, by) for report in reports)


def purge_expired_reports(now=None):
    """Deletes every report archived REPORT_ARCHIVE_DAYS or more ago, with its file, and
    logs each one. There is no scheduler, so this runs whenever the Generate Report page or
    one of its reports is opened, and from `manage.py purge_archived_reports` for anyone who
    wants a cron job. Returns how many were deleted."""
    cutoff = (now or timezone.now()) - timedelta(days=REPORT_ARCHIVE_DAYS)
    purged = 0
    for report in GeneratedReport.objects.filter(archived_at__lte=cutoff):
        with transaction.atomic():
            if _delete_report(report, archived_at__lte=cutoff):
                audit.record(None, None, 'report_expired', _report_note(report), actor_name=AUTOMATIC_CLEANUP)
                purged += 1
    return purged


def days_left(report, now=None):
    """Whole days, rounded up, until an archived report is deleted (a report archived just
    now has REPORT_ARCHIVE_DAYS left)."""
    seconds = (report.deletes_at() - (now or timezone.now())).total_seconds()
    return max(math.ceil(seconds / 86400), 0)


# --- Clergy records -------------------------------------------------------

def _owner(record):
    """The portal account linked to the record, or None if it has none."""
    profile = getattr(record, 'profile', None)
    return profile.user if profile is not None else None


def archive_record(record, by):
    """True if this call archived it; False if it already was. Refuses a record whose
    status isn't Retired."""
    if record.status != ARCHIVABLE_STATUS:
        raise ArchiveError(f"Only retired clergy can be archived. Set {record.name}'s status to Retired first.")
    with transaction.atomic():
        changed = ClergyRecord.objects.filter(pk=record.pk, archived_at__isnull=True, status=ARCHIVABLE_STATUS).update(
            archived_at=timezone.now(), archived_by=by)
        if changed:
            audit.record(by, _owner(record), 'record_archived', record.id, target_name=record.name)
    return bool(changed)


def restore_record(record, by):
    """True if this call restored it; False if it wasn't archived."""
    with transaction.atomic():
        changed = ClergyRecord.objects.filter(pk=record.pk, archived_at__isnull=False).update(
            archived_at=None, archived_by=None)
        if changed:
            audit.record(by, _owner(record), 'record_restored', record.id, target_name=record.name)
    return bool(changed)
