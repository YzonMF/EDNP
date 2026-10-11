from django.core.management.base import BaseCommand

from registrar.archive import purge_expired_reports
from registrar.models import REPORT_ARCHIVE_DAYS


class Command(BaseCommand):
    help = (
        f'Delete generated reports that have been archived for {REPORT_ARCHIVE_DAYS} days, with their '
        'files. The Generate Report page does this whenever it opens; this is for a cron job.'
    )

    def handle(self, *args, **options):
        purged = purge_expired_reports()
        self.stdout.write(self.style.SUCCESS(f'Deleted {purged} archived report{"" if purged == 1 else "s"}.'))
