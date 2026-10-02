"""Seeds only the deaneries and parishes (no demo clergy records or demo logins).

Run with: python manage.py seed_deaneries
Safe to re-run — uses get_or_create throughout.
"""

from django.core.management.base import BaseCommand

from registrar.management.commands.seed_demo_data import DEANERIES
from registrar.models import Deanery, Parish


class Command(BaseCommand):
    help = "Seed deaneries and parishes only."

    def handle(self, *args, **options):
        for deanery_name, entries in DEANERIES.items():
            deanery, _ = Deanery.objects.get_or_create(name=deanery_name)
            for entry in entries:
                if ' - ' in entry:
                    parish_name, place_name = entry.split(' - ', 1)
                else:
                    parish_name, place_name = entry, None
                Parish.objects.get_or_create(deanery=deanery, parish_name=parish_name, place_name=place_name)
        self.stdout.write(self.style.SUCCESS(f"Deaneries/parishes seeded ({Deanery.objects.count()} deaneries, {Parish.objects.count()} parishes)."))
