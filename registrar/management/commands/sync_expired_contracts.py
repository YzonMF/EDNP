from django.core.management.base import BaseCommand
from registrar.models import sync_expired_contracts


class Command(BaseCommand):
    help = 'Automatically scans and records expired contracts into deployments history'

    def handle(self, *args, **options):
        self.stdout.write('Scanning for expired contracts...')
        count = sync_expired_contracts()
        self.stdout.write(self.style.SUCCESS(f'Successfully synchronized {count} expired contract(s) to deployments.'))

