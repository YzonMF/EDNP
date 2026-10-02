from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db import models as db_models
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from clergy.models import ClergyProfile
from ednp.password_helpers import validate_password_or_flag_errors

from . import backups, reports
from .decorators import registrar_required
from .forms import ClergyRecordForm, DocumentUploadForm, PersonalInfoForm, first_error
from .models import RANK_CHOICES, BackupArchive, ClergyRecord, Deanery, Document, DocumentType, GeneratedReport, save_document

NEAR_EXPIRY_WINDOW_DAYS = 180


def _can_access_document(user, document):
    """Registrar (staff) sees every document; a clergy user only sees
    documents on their own linked record — checked here, server-side, not
    just by which links happen to be rendered on their pages."""
    if user.is_staff:
        return True
    profile = getattr(user, 'clergy_profile', None)
    return profile is not None and profile.record_id == document.record_id


@login_required(login_url='index')
def view_document(request, document_id):
    document = get_object_or_404(Document, id=document_id)
    if not _can_access_document(request.user, document):
        raise Http404
    filename = document.file.name.rsplit('/', 1)[-1]
    return FileResponse(document.file.open('rb'), as_attachment=False, filename=filename)


@login_required(login_url='index')
def download_document(request, document_id):
    document = get_object_or_404(Document, id=document_id)
    if not _can_access_document(request.user, document):
        raise Http404
    filename = document.file.name.rsplit('/', 1)[-1]
    return FileResponse(document.file.open('rb'), as_attachment=True, filename=filename)


def _build_deaneries_map():
    """Deanery -> its parishes, keyed by Deanery id (as a string, since JSON
    object keys are always strings) so a <select> can submit the real FK
    value directly instead of a display name that has to be looked back up."""
    deaneries_map = {}
    for deanery in Deanery.objects.prefetch_related('parishes'):
        options = []
        for parish in deanery.parishes.all():
            label = f"{parish.parish_name} - {parish.place_name}" if parish.place_name else parish.parish_name
            options.append(label)
        deaneries_map[str(deanery.id)] = {'name': deanery.name, 'parishes': options}
    return deaneries_map


def _build_account_from_record(record):
    """Only login details + the record link are set here — every profile
    field starts blank so the clergy member fills it in themselves on first
    login via My Profile, including their own name and assignment."""
    return {
        'record': record,
        'verification': 'For Verification',
    }


@registrar_required
def dashboard(request):
    records = ClergyRecord.objects.all()
    near_expiry_cutoff = date.today() + timedelta(days=NEAR_EXPIRY_WINDOW_DAYS)
    stats = {
        'total': records.count(),
        'active': records.filter(status='Active').count(),
        'expiry': records.filter(contract_end__isnull=False, contract_end__lt=near_expiry_cutoff).count(),
    }
    recent_records = records.order_by('-created_at')[:5]
    return render(request, 'registrar/dashboard.html', {
        'active_page': 'dashboard',
        'stats': stats,
        'recent_records': recent_records,
    })


@registrar_required
def clergy_records(request):
    query = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()

    records = ClergyRecord.objects.all()
    if query:
        records = records.filter(
            db_models.Q(name__icontains=query)
            | db_models.Q(assignment__icontains=query)
            | db_models.Q(id__icontains=query)
        )
    if status:
        records = records.filter(status=status)

    records = list(records)
    for record in records:
        record.doc_summary = record.document_summary()

    linked_ids = set(ClergyProfile.objects.exclude(record=None).values_list('record_id', flat=True))

    return render(request, 'registrar/clergy-records.html', {
        'active_page': 'clergy_records',
        'records': records,
        'linked_ids': linked_ids,
        'search_query': query,
        'status_filter': status,
        'status_choices': ClergyRecord.STATUS_CHOICES,
        'next_id': ClergyRecord.next_id(),
        'deaneries_map': _build_deaneries_map(),
    })


@registrar_required
def create_record(request):
    if request.method != 'POST':
        return redirect('registrar:clergy_records')

    form = ClergyRecordForm(request.POST)
    username = request.POST.get('new_username', '').strip()
    password = request.POST.get('new_password', '')

    if User.objects.filter(username__iexact=username).exists():
        messages.error(request, 'That username is already taken.')
    elif form.is_valid() and username and password:
        if not validate_password_or_flag_errors(request, password, user=User(username=username)):
            return redirect('registrar:clergy_records')

        # Record, login and profile are created together or not at all, and the
        # ID is allocated inside the same transaction so a failure hands the
        # number back instead of leaving a gap or a half-built account.
        try:
            with transaction.atomic():
                record = form.save(commit=False)
                record.id = ClergyRecord.allocate_id()
                record.save()

                user = User.objects.create_user(username=username, password=password)
                ClergyProfile.objects.create(user=user, **_build_account_from_record(record))
        except IntegrityError:
            # Someone else took the username between the check above and now.
            messages.error(request, 'That username is already taken.')
        else:
            messages.success(request, 'Clergy record created and portal account set up successfully.')
    else:
        messages.error(request, 'Please fill in all required fields.')

    return redirect('registrar:clergy_records')


@registrar_required
def link_account(request, record_id):
    record = get_object_or_404(ClergyRecord, id=record_id)

    if request.method != 'POST':
        return redirect('registrar:clergy_records')

    username = request.POST.get('username', '').strip()
    password = request.POST.get('password', '')

    if User.objects.filter(username__iexact=username).exists():
        messages.error(request, 'That username is already taken.')
    elif username and password:
        if not validate_password_or_flag_errors(request, password, user=User(username=username)):
            return redirect('registrar:clergy_records')

        try:
            with transaction.atomic():
                user = User.objects.create_user(username=username, password=password)
                ClergyProfile.objects.create(user=user, **_build_account_from_record(record))
        except IntegrityError:
            messages.error(request, 'That username is already taken.')
        else:
            messages.success(request, f'Portal account created for {record.name}.')

    return redirect('registrar:clergy_records')


@registrar_required
def record_detail(request, record_id):
    """One combined per-record page — Personal Information and Documents as
    two tabs of the same record, instead of a JS-populated modal plus a
    separate documents page. `?tab=documents` (or `?tab=personal`, the
    default) picks which tab is active on load, so the Documents overview
    page can link straight into a specific record's Documents tab."""
    record = get_object_or_404(ClergyRecord, id=record_id)

    if request.method == 'POST' and 'document_type' in request.POST:
        doc_form = DocumentUploadForm(request.POST, request.FILES)
        if doc_form.is_valid():
            save_document(
                record,
                doc_form.cleaned_data['document_type'],
                doc_form.cleaned_data['file'],
                doc_form.cleaned_data['notes'],
            )
            messages.success(request, f"{doc_form.cleaned_data['document_type'].name} uploaded for {record.name}.")
        else:
            messages.error(request, first_error(doc_form) or 'Please choose a document type and a file.')
        return redirect(f"{request.path}?tab=documents")

    if request.method == 'POST':
        form = PersonalInfoForm(request.POST, instance=record)
        if form.is_valid():
            form.save()
            messages.success(request, 'Personal Information updated successfully.')
        else:
            messages.error(request, 'Please check the form and try again.')
        return redirect(f"{request.path}?tab=personal")

    return render(request, 'registrar/record-detail.html', {
        'active_page': 'clergy_records',
        'record': record,
        'checklist': record.document_checklist_grouped(),
        'active_tab': request.GET.get('tab', 'personal'),
        'deaneries_map': _build_deaneries_map(),
        'record_data': {'deanery_id': record.deanery_id, 'assignment': record.assignment or ''},
    })


@registrar_required
def documents_overview(request):
    records = ClergyRecord.objects.all()
    rows = [{'record': r, 'summary': r.document_summary()} for r in records]
    return render(request, 'registrar/documents.html', {
        'active_page': 'documents',
        'rows': rows,
    })


@registrar_required
def ordination_requirements(request):
    """A read-only reference view of the document catalog, grouped by the
    rank each requirement is introduced at — generated live from
    DocumentType instead of a hardcoded page, so it can't drift out of sync
    with whatever the catalog actually requires."""
    groups = []
    for rank_value, rank_label in RANK_CHOICES:
        types = DocumentType.objects.filter(required_from_rank=rank_value)
        if types:
            groups.append({'label': rank_label, 'types': types})
    general_types = DocumentType.objects.filter(required_from_rank='none')
    if general_types:
        groups.append({'label': 'Required for Every Clergy Member', 'types': general_types})

    return render(request, 'registrar/ordination-requirements.html', {
        'active_page': 'ordination_requirements',
        'groups': groups,
    })


@registrar_required
def contract_review(request):
    records = ClergyRecord.objects.filter(contract_end__isnull=False).order_by('contract_end')
    today = date.today()
    rows = [{'record': r, 'days_left': (r.contract_end - today).days} for r in records]
    return render(request, 'registrar/contract-review.html', {
        'active_page': 'contract_review',
        'rows': rows,
    })


@registrar_required
def generate_report(request):
    if request.method == 'POST':
        report_type = request.POST.get('type')
        if report_type not in reports.REPORT_TYPES:
            messages.error(request, 'Please choose a report type first.')
            return redirect('registrar:generate_report')

        report = reports.build_report(report_type, generated_by=request.user)
        pdf_bytes = reports.render_pdf(report)
        filename = f"{report_type}_{report['generated_at'].strftime('%Y%m%d_%H%M%S')}.pdf"

        generated = GeneratedReport(report_type=report_type, title=report['title'], generated_by=request.user)
        generated.file.save(filename, ContentFile(pdf_bytes), save=True)

        messages.success(request, f"{report['title']} generated.")
        return redirect('registrar:generate_report')

    return render(request, 'registrar/generate-report.html', {
        'active_page': 'generate_report',
        'report_types': reports.REPORT_TYPES.items(),
        'generated_reports': GeneratedReport.objects.select_related('generated_by').all(),
    })


@registrar_required
def view_generated_report(request, report_id):
    generated = get_object_or_404(GeneratedReport, id=report_id)
    filename = generated.file.name.rsplit('/', 1)[-1]
    return FileResponse(generated.file.open('rb'), as_attachment=False, filename=filename, content_type='application/pdf')


@registrar_required
def preview_generated_report(request, report_id):
    """Shows the report as an ordinary HTML page (the same template used to
    build the stored PDF), with its own Print button — rather than embedding
    the PDF file itself, which ran into browsers that force a download on a
    direct PDF navigation regardless of what headers say. Renders the data
    fresh as of right now rather than replaying the historical snapshot, so
    "View" is a live look and "Download" remains the exact file generated
    on the original date."""
    generated = get_object_or_404(GeneratedReport, id=report_id)
    report = reports.build_report(generated.report_type, generated_by=generated.generated_by)
    return render(request, 'registrar/report_pdf.html', {
        'report': report,
        'download_url': reverse('registrar:download_generated_report', args=[generated.id]),
    })


@registrar_required
def download_generated_report(request, report_id):
    generated = get_object_or_404(GeneratedReport, id=report_id)
    filename = generated.file.name.rsplit('/', 1)[-1]
    return FileResponse(generated.file.open('rb'), as_attachment=True, filename=filename, content_type='application/pdf')


@registrar_required
def backup_records(request):
    if request.method == 'POST':
        zip_bytes = backups.build_documents_zip()
        filename = f"documents_archive_{timezone.now().strftime('%Y%m%d_%H%M%S')}.zip"
        archive = BackupArchive(created_by=request.user)
        archive.file.save(filename, ContentFile(zip_bytes), save=True)
        messages.success(request, 'Documents archive created.')
        return redirect('registrar:backup_records')

    return render(request, 'registrar/backup-records.html', {
        'active_page': 'backup_records',
        'archives': BackupArchive.objects.select_related('created_by').all(),
    })


@registrar_required
def download_database_backup(request):
    dump_bytes = backups.build_database_dump()
    filename = f"database_backup_{timezone.now().strftime('%Y%m%d_%H%M%S')}.json"
    response = HttpResponse(dump_bytes, content_type='application/json')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@registrar_required
def download_backup_archive(request, archive_id):
    archive = get_object_or_404(BackupArchive, id=archive_id)
    filename = archive.file.name.rsplit('/', 1)[-1]
    return FileResponse(archive.file.open('rb'), as_attachment=True, filename=filename, content_type='application/zip')


@registrar_required
def change_password(request):
    if request.method == 'POST':
        current = request.POST.get('current_password', '')
        new = request.POST.get('new_password', '')
        confirm = request.POST.get('confirm_password', '')
        if not request.user.check_password(current):
            messages.error(request, 'Current password is incorrect.')
        elif new != confirm:
            messages.error(request, 'New passwords do not match.')
        elif new == current:
            messages.error(request, 'New password must be different from the current one.')
        elif validate_password_or_flag_errors(request, new, user=request.user):
            request.user.set_password(new)
            request.user.save()
            update_session_auth_hash(request, request.user)
            messages.success(request, 'Password changed successfully.')
            return redirect('registrar:change_password')
    return render(request, 'registrar/change-password.html', {'active_page': 'change_password'})
