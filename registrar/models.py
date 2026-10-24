import calendar
from datetime import date, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction, OperationalError
from django.db.models import CheckConstraint, Q, UniqueConstraint
from django.db.models.functions import Coalesce
from django.utils import timezone

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


def parse_station_and_place(assignment):
    """Split an assignment string (e.g. 'St. Barnabas Parish - Alab' or
    'St. Jude Church, Baguio') into station_name and place_name."""
    if not assignment:
        return '', ''
    assignment = assignment.strip()
    if ' - ' in assignment:
        parts = assignment.split(' - ', 1)
        return parts[0].strip(), parts[1].strip()
    elif ', ' in assignment:
        parts = assignment.split(', ', 1)
        return parts[0].strip(), parts[1].strip()
    return assignment, ''


class ClergyDeploymentQuerySet(models.QuerySet):
    def latest_first(self):
        return self.order_by(
            '-is_current',
            Coalesce('end_date', 'start_date').desc(),
            '-end_date',
            '-start_date',
            '-id'
        )


class ClergyDeploymentManager(models.Manager):
    def get_queryset(self):
        return ClergyDeploymentQuerySet(self.model, using=self._db).latest_first()


class ClergyDeployment(models.Model):
    record = models.ForeignKey('ClergyRecord', on_delete=models.CASCADE, related_name='deployments')
    station_name = models.CharField('Parish / Station / Mission', max_length=150)
    place_name = models.CharField('Place / Location', max_length=150, blank=True, null=True)
    role = models.CharField('Role / Position', max_length=100, default='Priest-in-Charge')
    start_date = models.DateField('Start Date', blank=True, null=True)
    end_date = models.DateField('End Date', blank=True, null=True)
    is_current = models.BooleanField('Current Assignment', default=False)
    notes = models.CharField(max_length=255, blank=True, null=True)

    objects = ClergyDeploymentManager()

    class Meta:
        db_table = 'clergy_deployments'
        ordering = ['-is_current', '-end_date', '-start_date', '-id']

    def __str__(self):
        return f"{self.role} - {self.station_name}"

    @property
    def date_range_display(self):
        if self.is_current:
            if self.start_date:
                return f"{self.start_date.strftime('%b %d, %Y')} – Present"
            return "Current"
        if self.start_date and self.end_date:
            return f"{self.start_date.strftime('%b %d, %Y')} – {self.end_date.strftime('%b %d, %Y')}"
        if self.end_date:
            return f"Ended {self.end_date.strftime('%b %d, %Y')}"
        if self.start_date:
            return f"From {self.start_date.strftime('%b %d, %Y')}"
        return ""

    @property
    def location_display(self):
        if self.place_name and self.place_name.lower() not in self.station_name.lower():
            return f"{self.station_name}, {self.place_name}"
        return self.station_name

    @property
    def summary_display(self):
        date_str = self.date_range_display
        loc_str = self.location_display
        role_str = f"({self.role})" if self.role else ""
        if date_str and loc_str:
            base = f"{date_str}: {loc_str}"
        else:
            base = loc_str or date_str or ""
        if role_str and base:
            return f"{base} {role_str}"
        return base or role_str


class IdCounter(models.Model):
    """Last number handed out for a named ID sequence (e.g. CLG-### clergy
    record IDs). Allocation locks this row, so concurrent creators are
    serialized for a few milliseconds instead of racing to the same ID, and
    it never has to scan the table the way max-of-existing-IDs does."""

    name = models.CharField(max_length=30, primary_key=True)
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'id_counters'


class ClergyRecordQuerySet(models.QuerySet):
    def current(self):
        """Records on the registrar's working lists: everything not archived."""
        return self.filter(archived_at__isnull=True)

    def archived(self):
        return self.filter(archived_at__isnull=False)


def clergy_profile_picture_upload_path(instance, filename):
    return f'clergy_photos/{instance.id}/{filename}'


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
    profile_picture = models.FileField(upload_to=clergy_profile_picture_upload_path, blank=True, null=True)
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
    rank = models.CharField(max_length=20, choices=RANK_CHOICES, default='deacon')
    contract_end = models.DateField(blank=True, null=True)
    retirement_date = models.DateField(blank=True, null=True)

    # Ordination, Bishop & Family — `ordination_date` is treated as the date
    # of ordination to the Priesthood (the most commonly referenced one
    # throughout the app); diaconate/holy-orders are the earlier/parallel
    # steps in that same process.
    diaconate_date = models.DateField('Date of Ordination to Diaconate', blank=True, null=True)
    diaconate_place = models.CharField('Place of Diaconate Ordination', max_length=150, blank=True, null=True)
    ordination_date = models.DateField('Date of Ordination to Priesthood', blank=True, null=True)
    holy_orders_date = models.DateField('Date Admitted as Holy Order', blank=True, null=True)
    ordination_place = models.CharField('Place of Priesthood Ordination', max_length=150, blank=True, null=True)
    bishop_name = models.CharField('Bishop', max_length=150, blank=True, null=True)
    consecration_date = models.DateField('Date of Bishop Consecration', blank=True, null=True)
    previous_assignment = models.CharField(max_length=150, blank=True, null=True)
    service_history = models.TextField('Deployment & Service History', blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    # A retired clergy member's record can be archived: it leaves the registrar's working
    # lists but is never deleted (see registrar/archive.py). Set together, cleared together.
    archived_at = models.DateTimeField(blank=True, null=True)
    archived_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    blank=True, null=True, related_name='+')

    objects = ClergyRecordQuerySet.as_manager()

    class Meta:
        db_table = 'clergy_records'
        ordering = ['id']

    def __str__(self):
        return f"{self.id} - {self.name}"

    @property
    def initials(self):
        words = [w for w in (self.name or '').split() if w]
        return "".join(w[0] for w in words[:2]).upper() or "CU"

    @property
    def is_archived(self):
        return self.archived_at is not None

    @property
    def can_have_diaconate(self):
        """Deacons, Priests, and Bishops have undergone the 1st Ordination (Diaconate)."""
        return self.rank in ('deacon', 'priest', 'bishop')

    @property
    def can_have_priesthood(self):
        """Priests and Bishops have undergone the 2nd Ordination (Priesthood)."""
        return self.rank in ('priest', 'bishop')

    @property
    def can_have_consecration(self):
        """Bishops have undergone the 3rd Ordination (Consecration to Episcopate)."""
        return self.rank == 'bishop'

    def clean(self):
        super().clean()
        # Enforce cumulative holy orders progression:
        # Higher ranks build strictly on lower ranks. Postulancy has no ordinations.
        if self.rank == 'postulancy':
            self.diaconate_date = None
            self.diaconate_place = ''
            self.ordination_date = None
            self.ordination_place = ''
            self.consecration_date = None
            self.holy_orders_date = None
        elif self.rank == 'deacon':
            self.ordination_date = None
            self.ordination_place = ''
            self.consecration_date = None
        elif self.rank == 'priest':
            self.consecration_date = None

        # Chronological validation across sequential ordinations
        errors = {}
        if self.diaconate_date and self.ordination_date and self.diaconate_date > self.ordination_date:
            errors['ordination_date'] = 'Priesthood ordination date cannot be earlier than Diaconate ordination date.'
        if self.ordination_date and self.consecration_date and self.ordination_date > self.consecration_date:
            errors['consecration_date'] = 'Bishop consecration date cannot be earlier than Priesthood ordination date.'
        if self.diaconate_date and self.consecration_date and self.diaconate_date > self.consecration_date:
            errors['consecration_date'] = 'Bishop consecration date cannot be earlier than Diaconate ordination date.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    @property
    def years_of_service(self):
        """Computed from deployment history if available (summing non-overlapping
        service periods across all assignments), or falling back to diaconate / ordination
        date to today (or to retirement date, if retired). Deliberately not stored
        since a stored value would silently go stale every single day."""
        today = self.retirement_date if (self.status == 'Retired' and self.retirement_date) else date.today()

        # 1. Compute from structured deployments if any have start_date
        intervals = []
        for dep in self.deployments.all():
            start = dep.start_date
            if not start:
                continue
            if dep.is_current:
                end = today
            elif dep.end_date:
                end = dep.end_date
            else:
                end = start
            if end >= start:
                intervals.append((start, end))

        if intervals:
            intervals.sort(key=lambda x: x[0])
            merged = [intervals[0]]
            for c_start, c_end in intervals[1:]:
                prev_start, prev_end = merged[-1]
                if c_start <= prev_end:
                    merged[-1] = (prev_start, max(prev_end, c_end))
                else:
                    merged.append((c_start, c_end))

            total_days = sum((end - start).days for start, end in merged)
            years = int(total_days / 365.25)
            return max(years, 0)

        # 2. Fallback: Diaconate or Ordination date to today / retirement
        start = self.diaconate_date or self.ordination_date
        if not start:
            return None
        years = today.year - start.year - ((today.month, today.day) < (start.month, start.day))
        return max(years, 0)

    @property
    def years_of_service_display(self):
        y = self.years_of_service
        if y is None:
            return "—"
        if y == 0:
            return "< 1 year"
        return f"{y} year{'s' if y != 1 else ''}"

    def record_expired_contract(self, today=None):
        """Automatically records an expired contract assignment into deployments
        history, and ensures current deployments are unmarked when their contract ends.
        Prevents duplicate entries and ensures deployments are ordered latest to oldest."""
        if today is None:
            today = date.today()

        if not self.contract_end or self.contract_end > today:
            return False

        assignment_text = (self.assignment or '').strip()
        if not assignment_text:
            return False

        station, place = parse_station_and_place(assignment_text)
        if not station:
            station = assignment_text

        if not place and self.deanery_id:
            p = self.deanery.parishes.filter(parish_name__iexact=station).first()
            if p and p.place_name:
                place = p.place_name

        # Check and update any currently active deployment for this record that has now expired
        current_deps = list(self.deployments.filter(is_current=True))
        updated_current = False
        for dep in current_deps:
            if (dep.end_date and dep.end_date <= today) or dep.station_name.lower() == station.lower():
                dep.is_current = False
                if not dep.end_date:
                    dep.end_date = self.contract_end
                if not dep.notes:
                    dep.notes = f"Contract expired on {self.contract_end.isoformat()}"
                dep.save(update_fields=['is_current', 'end_date', 'notes'])
                updated_current = True

        # Check if a deployment already exists for this station and contract_end
        already_recorded = self.deployments.filter(
            station_name__iexact=station,
            end_date=self.contract_end
        ).exists()

        if not already_recorded:
            role = 'Priest-in-Charge'
            if self.rank == 'deacon':
                role = 'Deacon'
            elif self.rank == 'bishop':
                role = 'Bishop'
            elif self.rank == 'priest':
                role = 'Priest-in-Charge'

            self.deployments.create(
                station_name=station,
                place_name=place or None,
                role=role,
                end_date=self.contract_end,
                is_current=False,
                notes=f"Contract expired on {self.contract_end.isoformat()}"
            )
            return True

        return updated_current


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

    def document_checklist(self, required_types=None):
        """The full cumulative list of document types required for this
        clergy member's current rank (everything required at lower ranks,
        plus their own), each paired with its submission status.

        When called in a loop over many records, pass the already-loaded
        `required_types` list and `prefetch_related('documents__document_type')`
        on the records queryset so this does no extra queries per record."""
        try:
            rank_index = RANK_ORDER.index(self.rank)
        except ValueError:
            rank_index = 0
        applicable_ranks = set(RANK_ORDER[:rank_index + 1] + ['none'])

        if required_types is None:
            types = DocumentType.objects.filter(
                required_from_rank__in=applicable_ranks, is_required=True
            )
        else:
            types = [dt for dt in required_types if dt.required_from_rank in applicable_ranks]

        if 'documents' in getattr(self, '_prefetched_objects_cache', {}):
            docs = self.documents.all()
        else:
            docs = self.documents.select_related('document_type')
        existing = {d.document_type_id: d for d in docs}

        checklist = []
        for doc_type in types:
            document = existing.get(doc_type.id)
            if document is None:
                status = 'Missing'
            elif doc_type.is_noted_only:
                status = 'Submitted'
            elif document.is_expired():
                status = 'Expired'
            else:
                status = 'Submitted'
            checklist.append({'document_type': doc_type, 'document': document, 'status': status})
        return checklist

    def document_checklist_grouped(self, required_types=None):
        """The same checklist, split into the two groups the UI shows as
        separate card panels: documents the clergy member normally submits
        themselves, and documents the Registrar normally provides."""
        checklist = self.document_checklist(required_types=required_types)
        groups = {'clergy': [], 'registrar': []}
        for item in checklist:
            groups[item['document_type'].uploaded_by].append(item)
        return groups

    def document_summary(self, required_types=None):
        """Counts behind the document checklist, for badges/list views that
        just need "5/8 submitted" rather than the full per-type breakdown."""
        if not hasattr(self, '_document_summary_cache') or required_types is not None:
            checklist = self.document_checklist(required_types=required_types)
            submitted = sum(1 for c in checklist if c['status'] == 'Submitted')
            expired = sum(1 for c in checklist if c['status'] == 'Expired')
            missing = sum(1 for c in checklist if c['status'] == 'Missing')
            self._document_summary_cache = {
                'submitted': submitted, 'expired': expired, 'missing': missing, 'total': len(checklist),
            }
        return self._document_summary_cache

    @property
    def date_attachments_map(self):
        """Map of date field names to their corresponding document requirement status,
        attachment object, and upload metadata, strictly filtered by the clergy member's rank."""
        if not hasattr(self, '_date_attachments_cache'):
            fields_map = {
                'date_of_birth': 'Birth Certificate',
                'baptism_date': 'Baptismal Certificate',
                'confirmation_date': 'Declaration of Conformity',
            }
            if self.rank in ('deacon', 'priest', 'bishop'):
                fields_map['diaconate_date'] = 'Ordination Entry / Holy Orders Document'
                if self.holy_orders_date:
                    fields_map['holy_orders_date'] = 'Ordination Entry / Holy Orders Document'
            if self.rank in ('priest', 'bishop'):
                fields_map['ordination_date'] = 'Ordination Entry / Holy Orders Document'
            if self.rank == 'bishop':
                fields_map['consecration_date'] = 'Ordination Entry / Holy Orders Document'

            needed_names = set(fields_map.values())
            doc_types = {dt.name: dt for dt in DocumentType.objects.filter(name__in=needed_names)}
            existing_docs = {
                doc.document_type.name: doc
                for doc in self.documents.select_related('document_type').filter(document_type__name__in=needed_names)
                if doc.file
            }
            res = {}
            for field_name, doc_name in fields_map.items():
                dt = doc_types.get(doc_name)
                doc = existing_docs.get(doc_name)
                res[field_name] = {
                    'name': doc_name,
                    'doc_type': dt,
                    'doc': doc,
                    'is_uploaded': doc is not None and bool(doc.file),
                    'doc_id': doc.id if doc else None,
                    'can_clergy_upload': dt.uploaded_by == 'clergy' if dt else False,
                }
            self._date_attachments_cache = res
        return self._date_attachments_cache

    def ember_summary(self):
        """Informational counts of uploaded Ember letters across years."""
        if 'ember_letters' in getattr(self, '_prefetched_objects_cache', {}):
            letters = list(self.ember_letters.all())
        else:
            letters = list(self.ember_letters.only('id', 'year'))
        years = sorted({el.year for el in letters}, reverse=True)
        year_count = len(years)
        target = max(12, year_count * 4)
        return {
            'uploaded': len(letters),
            'years': year_count,
            'target': target,
        }


def sync_expired_contracts(today=None, queryset=None):
    """Scan and synchronize all expired contracts into deployment records."""
    if today is None:
        today = date.today()
    if queryset is None:
        queryset = ClergyRecord.objects.filter(
            contract_end__isnull=False,
            contract_end__lte=today
        ).exclude(assignment__isnull=True).exclude(assignment='')
    else:
        queryset = queryset.filter(
            contract_end__isnull=False,
            contract_end__lte=today
        ).exclude(assignment__isnull=True).exclude(assignment='')

    synced = 0
    for record in queryset:
        if record.record_expired_contract(today=today):
            synced += 1
    return synced


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
    requires_upload = models.BooleanField(
        default=True,
        help_text='If false, this item is noted only (Submitted/Not Submitted) without document upload.',
    )

    class Meta:
        db_table = 'document_types'
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def is_noted_only(self):
        return not self.requires_upload


def clergy_document_upload_path(instance, filename):
    return f'clergy_documents/{instance.record_id}/{instance.document_type_id}_{filename}'


class Document(models.Model):
    record = models.ForeignKey(ClergyRecord, on_delete=models.CASCADE, related_name='documents')
    document_type = models.ForeignKey(DocumentType, on_delete=models.PROTECT, related_name='documents')
    file = models.FileField(upload_to=clergy_document_upload_path, blank=True, null=True)
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


REPORT_ARCHIVE_DAYS = 30


class GeneratedReport(models.Model):
    """A saved snapshot of a Generate Report run — the PDF is stored as-is
    so re-downloading it later returns exactly what was generated, even if
    the underlying clergy data has since changed.

    An archived report can still be downloaded and printed, and is deleted for
    good REPORT_ARCHIVE_DAYS after it was archived unless it is restored first
    (see registrar/archive.py)."""

    report_type = models.CharField(max_length=40)
    title = models.CharField(max_length=200)
    file = models.FileField(upload_to='generated_reports/')
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True)
    generated_at = models.DateTimeField(auto_now_add=True)
    archived_at = models.DateTimeField(blank=True, null=True)
    archived_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    blank=True, null=True, related_name='+')

    class Meta:
        db_table = 'generated_reports'
        ordering = ['-generated_at']

    def __str__(self):
        return f"{self.title} ({self.generated_at:%Y-%m-%d %H:%M})"

    def deletes_at(self):
        """When an archived report is deleted for good; None while it isn't archived."""
        if self.archived_at is None:
            return None
        return self.archived_at + timedelta(days=REPORT_ARCHIVE_DAYS)


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
        if old_name and document.file and old_name != document.file.name:
            storage = document.file.storage
            transaction.on_commit(lambda: storage.delete(old_name))
    return document


def note_document_submission(record, document_type, submitted=True, submitted_date=None, notes=''):
    """Record or remove a noted-only requirement (e.g. Psychiatric Evaluation Result)
    without any file upload."""
    with transaction.atomic():
        previous = Document.objects.select_for_update().filter(record=record, document_type=document_type).first()
        old_name = previous.file.name if previous and previous.file else None
        if submitted:
            document, _ = Document.objects.update_or_create(
                record=record,
                document_type=document_type,
                defaults={'file': None, 'notes': notes, 'submitted_date': submitted_date or date.today()},
            )
            if old_name and previous.file:
                storage = previous.file.storage
                transaction.on_commit(lambda: storage.delete(old_name))
            return document
        else:
            if previous:
                if old_name and previous.file:
                    storage = previous.file.storage
                    transaction.on_commit(lambda: storage.delete(old_name))
                previous.delete()
            return None


def ember_letter_upload_path(instance, filename):
    return f'ember_letters/{instance.record_id}/{instance.year}_Q{instance.quarter}_{filename}'


class EmberLetter(models.Model):
    record = models.ForeignKey(
        ClergyRecord,
        on_delete=models.CASCADE,
        related_name='ember_letters',
    )
    year = models.PositiveSmallIntegerField()
    quarter = models.PositiveSmallIntegerField()
    file = models.FileField(upload_to=ember_letter_upload_path)
    file_size = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    submitted_date = models.DateTimeField()

    class Meta:
        db_table = 'ember_letters'
        ordering = ['-year', 'quarter']
        constraints = [
            UniqueConstraint(
                fields=['record', 'year', 'quarter'],
                name='ember_letter_unique_slot',
            ),
            CheckConstraint(
                check=Q(quarter__gte=1, quarter__lte=4),
                name='ember_letter_quarter_1_to_4',
            ),
        ]

    def __str__(self):
        return f"{self.record_id} - {self.year} Q{self.quarter}"


def save_ember_letter(record, year, quarter, file, user=None):
    """Create or replace the one EmberLetter for (record, year, quarter), then
    delete the file it replaced once the transaction commits. Returns
    (ember_letter, replaced_bool)."""
    file_size = getattr(file, 'size', 0) or 0
    with transaction.atomic():
        previous = (
            EmberLetter.objects.select_for_update()
            .filter(record=record, year=year, quarter=quarter)
            .first()
        )
        old_name = previous.file.name if previous else None
        ember_letter, created = EmberLetter.objects.update_or_create(
            record=record,
            year=year,
            quarter=quarter,
            defaults={
                'file': file,
                'file_size': file_size,
                'uploaded_by': user,
                'submitted_date': timezone.now(),
            },
        )
        if old_name and old_name != ember_letter.file.name:
            storage = ember_letter.file.storage
            transaction.on_commit(lambda: storage.delete(old_name))
    return ember_letter, not created


def delete_ember_letter(ember_letter):
    """Delete an EmberLetter row and remove its stored file after commit."""
    with transaction.atomic():
        old_name = ember_letter.file.name
        storage = ember_letter.file.storage
        ember_letter.delete()
        if old_name:
            transaction.on_commit(lambda: storage.delete(old_name))


def save_profile_picture(record, file):
    """Save or replace `record`'s profile picture, deleting the previous file
    from storage only after the transaction commits."""
    with transaction.atomic():
        locked = ClergyRecord.objects.select_for_update().get(pk=record.pk)
        old_name = locked.profile_picture.name if locked.profile_picture else None
        locked.profile_picture = file
        locked.save(update_fields=['profile_picture'])
        record.profile_picture = locked.profile_picture
        if old_name and old_name != locked.profile_picture.name:
            storage = locked.profile_picture.storage
            transaction.on_commit(lambda: storage.delete(old_name))
    return record


def delete_profile_picture(record):
    """Clear `record`'s profile picture and delete the stored file after commit."""
    with transaction.atomic():
        locked = ClergyRecord.objects.select_for_update().get(pk=record.pk)
        old_name = locked.profile_picture.name if locked.profile_picture else None
        storage = locked.profile_picture.storage if old_name else None
        locked.profile_picture = ''
        locked.save(update_fields=['profile_picture'])
        record.profile_picture = locked.profile_picture
        if old_name and storage is not None:
            transaction.on_commit(lambda: storage.delete(old_name))
