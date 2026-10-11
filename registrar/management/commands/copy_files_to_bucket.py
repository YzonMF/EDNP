from collections import Counter

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError

from ednp.storage import EncryptedFileSystemStorage


class Command(BaseCommand):
    help = ("Copies every stored file from MEDIA_ROOT (the server's disk, or a volume mounted there) into the "
            'bucket, exactly as stored (still encrypted if it was), so a site moving to a bucket keeps the files it '
            'already has. Files already in the bucket are left alone, and nothing on disk is changed or deleted. '
            'Safe to run again. Use --dry-run first.')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Change nothing: report what would be copied.')

    def handle(self, *args, **options):
        if not settings.BUCKET_NAME:
            raise CommandError('BUCKET_NAME is not set, so there is no bucket to copy into. '
                               'See resources/storage-bucket.md.')
        dry_run = options['dry_run']
        disk = EncryptedFileSystemStorage(location=settings.MEDIA_ROOT)
        in_bucket = set(default_storage.stored_names())
        tally = Counter()
        for name in disk.stored_names():
            if name in in_bucket:
                tally['already in the bucket'] += 1
            elif dry_run:
                tally['would be copied'] += 1
            else:
                with disk.open_stored(name) as file:
                    default_storage.put_stored(name, file)
                tally['copied'] += 1
        parts = [f'{count} {state}' for state, count in sorted(tally.items())]
        self.stdout.write(f'Files in {settings.MEDIA_ROOT}: ' + (', '.join(parts) or 'nothing found'))
