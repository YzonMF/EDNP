"""Seeds the DocumentType checklist described in
resources/ednp-document-types-fix.txt (cross-checked against the panel's
handwritten notes in resources/docs required/).

Run with: python manage.py seed_document_types
Safe to re-run — uses update_or_create throughout.
"""

from django.core.management.base import BaseCommand

from registrar.models import DocumentType

DOCUMENT_TYPES = [
    # Postulancy — baseline, required for every clergy member regardless of final rank
    {
        'name': 'Endorsement from Parish Priest and Vestry',
        'required_from_rank': 'postulancy',
    },
    {
        'name': 'Medical Exam Result',
        'required_from_rank': 'postulancy',
        'validity_period_months': 6,
    },
    {
        'name': 'Psychiatric Evaluation Result',
        'required_from_rank': 'postulancy',
        'validity_period_months': 12,
        'notes': 'Record the date it was received by the bishop.',
    },
    {
        'name': 'Letter of Intention (Postulancy)',
        'required_from_rank': 'postulancy',
    },
    {
        'name': 'Interview Record / Endorsement from Commission on Ministry',
        'required_from_rank': 'postulancy',
    },

    # Deacon — Postulancy requirements plus these
    {
        'name': 'Endorsement from Another Priest',
        'required_from_rank': 'deacon',
    },
    {
        'name': 'Declaration of Conformity',
        'required_from_rank': 'deacon',
    },
    {
        'name': 'Letter of Intention (Deacon)',
        'required_from_rank': 'deacon',
    },

    # Priest — Postulancy + Deacon requirements plus these
    {
        'name': 'Endorsement from Commission on Ministry and Standing Committee',
        'required_from_rank': 'priest',
    },
    {
        'name': 'Oath of Conformity',
        'required_from_rank': 'priest',
        'notes': 'Record the place it was taken.',
    },
    {
        'name': 'Letter of Intention (Priest)',
        'required_from_rank': 'priest',
    },

    # Bishop — Postulancy + Deacon + Priest requirements plus this
    {
        'name': 'Endorsement from Another Bishop',
        'required_from_rank': 'bishop',
    },

    # Supporting document types — not rank-gated, but every clergy member may need them
    {
        'name': 'Deployment Notice',
        'required_from_rank': 'none',
        'notes': 'Issued by the bishop.',
    },
    {
        'name': 'Letter of Institution',
        'required_from_rank': 'none',
        'notes': 'Issued per deployment/assignment.',
    },
    {
        'name': 'Biodata Form',
        'required_from_rank': 'none',
        'notes': 'Signed physical biodata sheet.',
    },
    {
        'name': 'Birth Certificate',
        'required_from_rank': 'none',
    },
    {
        'name': 'Seminary Evaluation / Grades / Ember Letter',
        'required_from_rank': 'none',
        'notes': 'From seminary — the registrar typically uploads this on the clergy member\'s behalf.',
    },
]


class Command(BaseCommand):
    help = "Seed the full cumulative-by-rank document type checklist."

    def handle(self, *args, **options):
        for entry in DOCUMENT_TYPES:
            DocumentType.objects.update_or_create(
                name=entry['name'],
                defaults={
                    'required_from_rank': entry['required_from_rank'],
                    'validity_period_months': entry.get('validity_period_months'),
                    'notes': entry.get('notes', ''),
                    'is_required': True,
                },
            )
        self.stdout.write(self.style.SUCCESS(f"Document types seeded ({DocumentType.objects.count()})."))
