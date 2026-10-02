import calendar
from datetime import date

from django.conf import settings
from django.db import models, transaction

RANK_CHOICES = [
    ('postulancy', 'Postulancy'),
    ('deacon', 'Deacon'),
    ('priest', 'Priest'),
    ('bishop', 'Bishop'),
]

RANK_ORDER = [choice[0] for choice in RANK_CHOICES]


class Deanery(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        db_table = 'deaneries'
        ordering = ['name']
        verbose_name_plural = 'Deaneries'

    def __str__(self):
        return self.name


class Parish(models.Model):
    deanery = models.ForeignKey(Deanery, on_delete=models.CASCADE, related_name='parishes')
    parish_name = models.CharField(max_length=150)
    place_name = models.CharField(max_length=150, blank=True, null=True)

    class Meta:
        db_table = 'parishes'
        ordering = ['deanery_id', 'id']

    def __str__(self):
        return f"{self.parish_name} - {self.place_name}" if self.place_name else self.parish_name


class IdCounter(models.Model):
    """Last number handed out for a named ID sequence (e.g. CLG-### clergy
    record IDs). Allocation locks this row, so concurrent creators are
    serialized for a few milliseconds instead of racing to the same ID, and
    it never has to scan the table the way max-of-existing-IDs does."""

    name = models.CharField(max_length=30, primary_key=True)
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'id_counters'


class ClergyRecord(models.Model):
    GENDER_CHOICES = [('Male', 'Male'), ('Female', 'Female')]
    STATUS_CHOICES = [
        ('Active', 'Active'),
        ('On Leave', 'On Leave'),
        ('Missionary to Other Diocese', 'Missionary to Other Diocese'),
        ('Retired', 'Retired'),
    ]

    id = models.CharField(max_length=20, primary_key=True)
    name = models.CharField(max_length=150)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, blank=True, null=True)
    date_of_birth = models.DateField(blank=True, null=True)
    baptism_date = models.DateField(blank=True, null=True)
    confirmation_date = models.DateField(blank=True, null=True)
    phone = models.CharField(max_length=30, blank=True, null=True)
    email = models.EmailField(max_length=150, blank=True, null=True)
    address = models.CharField(max_length=255, blank=True, null=True)
    deanery = models.ForeignKey(Deanery, on_delete=models.SET_NULL, blank=True, null=True, related_name='clergy_records')
    assignment = models.CharField(max_length=150, blank=True, null=True)
    status = models.CharField(max_length=40, choices=STATUS_CHOICES, default='Active')
    leave_reason = models.CharField(max_length=255, blank=True, null=True)
    rank = models.CharField(max_length=20, choices=RANK_CHOICES, default='postulancy')
    contract_end = models.DateField(blank=True, null=True)
    retirement_date = models.DateField(blank=True, null=True)

    # Ordination, Bishop & Family — `ordination_date` is treated as the date
    # of ordination to the Priesthood (the most commonly referenced one
    # throughout the app); diaconate/holy-orders are the earlier/parallel
    # steps in that same process.
    diaconate_date = models.DateField('Date of Ordination to Diaconate', blank=True, null=True)
    ordination_date = models.DateField('Date of Ordination to Priesthood', blank=True, null=True)
    holy_orders_date = models.DateField('Date for Holy Orders / Synod Entry', blank=True, null=True)
    ordination_place = models.CharField(max_length=150, blank=True, null=True)
    bishop_name = models.CharField('Bishop', max_length=150, blank=True, null=True)
    consecration_date = models.DateField('Date of Bishop Consecration', blank=True, null=True)
    previous_assignment = models.CharField(max_length=150, blank=True, null=True)
    service_history = models.TextField('Deployment & Service History', blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'clergy_records'
        ordering = ['id']

    def __str__(self):
        return f"{self.id} - {self.name}"

    def years_of_service(self):
        """Computed from ordination to today (or to retirement, if retired)
        — deliberately not stored, since a stored value would silently go
        stale every single day it isn't manually re-saved."""
        start = self.diaconate_date or self.ordination_date
        if not start:
            return None
        end = self.retirement_date if (self.status == 'Retired' and self.retirement_date) else date.today()
        years = end.year - start.year - ((end.month, end.day) < (start.month, start.day))
        return max(years, 0)

    COUNTER_NAME = 'clergy_record'

    @staticmethod
    def next_id():
        """Preview of the next CLG-### ID, for display only — it takes no
        lock and reserves nothing. Use allocate_id() when actually creating."""
        counter = IdCounter.objects.filter(name=ClergyRecord.COUNTER_NAME).first()
        return f"CLG-{(counter.last_value if counter else 0) + 1:03d}"

    @staticmethod
    def allocate_id():
        """Reserve and return the next CLG-### ID. Must be called inside the
        same transaction.atomic() block that saves the record: the counter row
        stays locked until that transaction ends, so two simultaneous creators
        can never get the same ID, and a rollback gives the number back. Also
        skips past any ID that already exists (e.g. one inserted by hand or by
        the demo seeder) rather than colliding with it."""
        with transaction.atomic():
            counter, _ = IdCounter.objects.select_for_update().get_or_create(name=ClergyRecord.COUNTER_NAME)
            while True:
                counter.last_value += 1
                candidate = f"CLG-{counter.last_value:03d}"
                if not ClergyRecord.objects.filter(id=candidate).exists():
                    break
            counter.save(update_fields=['last_value'])
            return candidate

    def has_account(self):
        return hasattr(self, 'profile') and self.profile is not None

    def document_checklist(self):
        """The full cumulative list of document types required for this
        clergy member's current rank (everything required at lower ranks,
        plus their own), each paired with its submission status."""
        try:
            rank_index = RANK_ORDER.index(self.rank)
        except ValueError:
            rank_index = 0
        applicable_ranks = RANK_ORDER[:rank_index + 1] + ['none']

        required_types = DocumentType.objects.filter(
            required_from_rank__in=applicable_ranks, is_required=True
        )
        existing = {d.document_type_id: d for d in self.documents.select_related('document_type')}

        checklist = []
        for doc_type in required_types:
            document = existing.get(doc_type.id)
            if document is None:
                status = 'Missing'
            elif document.is_expired():
                status = 'Expired'
            else:
                status = 'Submitted'
            checklist.append({'document_type': doc_type, 'document': document, 'status': status})
        return checklist

    def document_checklist_grouped(self):
        """The same checklist, split into the two groups the UI shows as
        separate card panels: documents the clergy member normally submits
        themselves, and documents the Registrar normally provides."""
        checklist = self.document_checklist()
        groups = {'clergy': [], 'registrar': []}
        for item in checklist:
            groups[item['document_type'].uploaded_by].append(item)
        return groups

    def document_summary(self):
        """Counts behind the document checklist, for badges/list views that
        just need "5/8 submitted" rather than the full per-type breakdown."""
        checklist = self.document_checklist()
        submitted = sum(1 for c in checklist if c['status'] == 'Submitted')
        expired = sum(1 for c in checklist if c['status'] == 'Expired')
        missing = sum(1 for c in checklist if c['status'] == 'Missing')
        return {'submitted': submitted, 'expired': expired, 'missing': missing, 'total': len(checklist)}


class DocumentType(models.Model):
    RANK_GATE_CHOICES = RANK_CHOICES + [('none', 'Not rank-gated')]
    UPLOADED_BY_CHOICES = [
        ('clergy', 'Clergy Submission'),
        ('registrar', 'Registrar Upload'),
    ]

    name = models.CharField(max_length=150, unique=True)
    required_from_rank = models.CharField(max_length=20, choices=RANK_GATE_CHOICES, default='none')
    is_required = models.BooleanField(default=True)
    uploaded_by = models.CharField(
        max_length=20, choices=UPLOADED_BY_CHOICES, default='clergy',
        help_text='Who normally provides this document — only affects which '
                   'checklist it appears under and who gets an upload control '
                   'for it; the Registrar can still manage either group.',
    )
    validity_period_months = models.PositiveIntegerField(
        blank=True, null=True,
        help_text='Leave blank if this document type does not expire.',
    )
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = 'document_types'
        ordering = ['name']

    def __str__(self):
        return self.name


def clergy_document_upload_path(instance, filename):
    return f'clergy_documents/{instance.record_id}/{instance.document_type_id}_{filename}'


class Document(models.Model):
    record = models.ForeignKey(ClergyRecord, on_delete=models.CASCADE, related_name='documents')
    document_type = models.ForeignKey(DocumentType, on_delete=models.PROTECT, related_name='documents')
    file = models.FileField(upload_to=clergy_document_upload_path)
    submitted_date = models.DateField()
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = 'documents'
        unique_together = [('record', 'document_type')]
        ordering = ['document_type__name']

    def __str__(self):
        return f"{self.record.name} - {self.document_type.name}"

    def expiry_date(self):
        months = self.document_type.validity_period_months
        if not months or not self.submitted_date:
            return None
        total_months = self.submitted_date.month - 1 + months
        year = self.submitted_date.year + total_months // 12
        month = total_months % 12 + 1
        day = min(self.submitted_date.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)

    def is_expired(self):
        expiry = self.expiry_date()
        return expiry is not None and expiry < date.today()


class GeneratedReport(models.Model):
    """A saved snapshot of a Generate Report run — the PDF is stored as-is
    so re-downloading it later returns exactly what was generated, even if
    the underlying clergy data has since changed."""

    report_type = models.CharField(max_length=40)
    title = models.CharField(max_length=200)
    file = models.FileField(upload_to='generated_reports/')
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True)
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'generated_reports'
        ordering = ['-generated_at']

    def __str__(self):
        return f"{self.title} ({self.generated_at:%Y-%m-%d %H:%M})"


class BackupArchive(models.Model):
    """A saved .zip of every uploaded clergy document, taken on demand from
    Backup Records. The database itself is backed up separately (a fresh
    dumpdata download each time) since that stays small at any record count
    — it's the uploaded files that grow large enough to need archiving
    instead of a live per-request download."""

    file = models.FileField(upload_to='backups/')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'backup_archives'
        ordering = ['-created_at']

    def __str__(self):
        return f"Documents Archive ({self.created_at:%Y-%m-%d %H:%M})"


def save_document(record, document_type, file, notes):
    """Create or replace the one Document for (record, document_type), then
    delete the file it replaced. The old file is only removed once the new row
    has committed, so a failed save never destroys the only good copy; the row
    lock makes two simultaneous re-uploads queue up instead of orphaning each
    other's files."""
    with transaction.atomic():
        previous = Document.objects.select_for_update().filter(record=record, document_type=document_type).first()
        old_name = previous.file.name if previous else None
        document, _ = Document.objects.update_or_create(
            record=record,
            document_type=document_type,
            defaults={'file': file, 'notes': notes, 'submitted_date': date.today()},
        )
        if old_name and old_name != document.file.name:
            storage = document.file.storage
            transaction.on_commit(lambda: storage.delete(old_name))
    return document
