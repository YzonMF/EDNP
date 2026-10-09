import secrets
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db import transaction
from django.db import models as db_models
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts import audit
from accounts.models import AccountAuditLog
from accounts.session_utils import keep_signed_in_after_password_change
from clergy.models import ClergyProfile
from ednp.password_helpers import validate_password_or_flag_errors

from . import backups, bulk, reports, slips
from .decorators import registrar_required
from .forms import ClergyRecordForm, DocumentUploadForm, PersonalInfoForm, first_error
from .models import RANK_CHOICES, BackupArchive, ClergyRecord, Deanery, Document, DocumentType, GeneratedReport, save_document
from .provisioning import (SETUP_WINDOW_DAYS, ProvisionError, account_status, provision_account, reset_credentials,
                           reset_lost_phone)

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

    # Portal-account state per record, for the Portal Account column: None if
    # there is no account yet, otherwise {'state': 'pending'|'expired'|'activated',
    # 'days_left': int|None}.
    profiles = ClergyProfile.objects.exclude(record=None).select_related('user__security')
    account_by_record = {}
    for profile in profiles:
        state, days_left = account_status(getattr(profile.user, 'security', None))
        account_by_record[profile.record_id] = {'state': state, 'days_left': days_left}
    for record in records:
        record.account = account_by_record.get(record.id)

    return render(request, 'registrar/clergy-records.html', {
        'active_page': 'clergy_records',
        'records': records,
        'search_query': query,
        'status_filter': status,
        'status_choices': ClergyRecord.STATUS_CHOICES,
        'next_id': ClergyRecord.next_id(),
        'deaneries_map': _build_deaneries_map(),
    })


def _portal_url(request):
    return request.build_absolute_uri('/')


def _slip_entry(name, user, password):
    return {
        'name': name,
        'username': user.username,
        'password': password,
        'expires_on': user.security.setup_expires_at.date(),
    }


def _slips_response(request, entries):
    """The slips as a PDF shown in the browser, ready to print. no-store keeps
    the plaintext passwords out of the browser's cache and back/forward
    history; they exist nowhere else once this response has been sent."""
    pdf = slips.render_slips_pdf(entries, _portal_url(request))
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = 'inline; filename="portal-login-slips.pdf"'
    response['Cache-Control'] = 'no-store'
    return response


@registrar_required
def create_record(request):
    if request.method != 'POST':
        return redirect('registrar:clergy_records')

    form = ClergyRecordForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Please fill in all required fields.')
        return redirect('registrar:clergy_records')

    # Record, login and slip are produced together or not at all — the PDF is
    # rendered inside the transaction, so if it fails nothing is saved and the
    # ID goes back, instead of leaving an account whose password was never
    # printed. The password is generated, not chosen, so there is nothing to
    # type here.
    try:
        with transaction.atomic():
            record = form.save(commit=False)
            record.id = ClergyRecord.allocate_id()
            record.save()
            user, password = provision_account(record)
            audit.record(request.user, user, 'account_created', target_name=record.name)
            return _slips_response(request, [_slip_entry(record.name, user, password)])
    except ProvisionError as exc:
        messages.error(request, str(exc))
        return redirect('registrar:clergy_records')


@registrar_required
def link_account(request, record_id):
    record = get_object_or_404(ClergyRecord, id=record_id)

    if request.method != 'POST':
        return redirect('registrar:clergy_records')

    if ClergyProfile.objects.filter(record=record).exists():
        messages.error(request, f'{record.name} already has a portal account.')
        return redirect('registrar:clergy_records')

    try:
        with transaction.atomic():
            user, password = provision_account(record)
            audit.record(request.user, user, 'account_created', target_name=record.name)
            return _slips_response(request, [_slip_entry(record.name, user, password)])
    except ProvisionError as exc:
        messages.error(request, str(exc))
        return redirect('registrar:clergy_records')


@registrar_required
def reset_account(request, record_id):
    """Two kinds of reset, both ending in a freshly printed slip:

    - 'reprint' (the default): a lost slip, or a temporary password that ran out
      before the account was activated. Refused for an activated account.
    - 'lost_phone': the owner lost their number or phone and has come in person.
      Forgets the number, revokes every trusted device and open session, and
      sends them back through first-login setup with the new password.

    The registrar's optional note is kept in the account activity log."""
    record = get_object_or_404(ClergyRecord, id=record_id)

    if request.method != 'POST':
        return redirect('registrar:clergy_records')

    profile = ClergyProfile.objects.filter(record=record).select_related('user').first()
    if profile is None:
        messages.error(request, f'{record.name} has no portal account to reset.')
        return redirect('registrar:clergy_records')

    try:
        with transaction.atomic():
            if request.POST.get('kind') == 'lost_phone':
                password, action = reset_lost_phone(profile.user), 'lost_phone_reset'
            else:
                password, action = reset_credentials(profile.user), 'password_reprinted'
            audit.record(request.user, profile.user, action, request.POST.get('note', ''), target_name=record.name)
            return _slips_response(request, [_slip_entry(record.name, profile.user, password)])
    except ProvisionError as exc:
        messages.error(request, str(exc))
        return redirect('registrar:clergy_records')


@registrar_required
def account_security(request):
    """Registrar accounts (so one registrar can recover another) and the recent
    account activity log."""
    User = get_user_model()
    staff = User.objects.filter(is_staff=True).select_related('security').order_by('first_name', 'username')
    rows = []
    for user in staff:
        state, days_left = account_status(getattr(user, 'security', None))
        rows.append({'user': user, 'name': audit.display_name(user), 'state': state, 'days_left': days_left,
                     'is_self': user.pk == request.user.pk})
    return render(request, 'registrar/account-security.html', {
        'active_page': 'account_security',
        'rows': rows,
        'activity': AccountAuditLog.objects.all()[:50],
    })


@registrar_required
def reset_staff_account(request, user_id):
    """One registrar recovering another who lost their phone or password.
    Never your own account: a hijacked session could otherwise reset itself,
    and a lone registrar is recovered from the server instead (manage.py
    reset_account)."""
    if request.method != 'POST':
        return redirect('registrar:account_security')
    target = get_object_or_404(get_user_model(), pk=user_id, is_staff=True)
    if target.pk == request.user.pk:
        messages.error(request, "You can't reset your own account. Ask another registrar, or the person who manages the server.")
        return redirect('registrar:account_security')
    with transaction.atomic():
        password = reset_lost_phone(target)
        audit.record(request.user, target, 'lost_phone_reset', request.POST.get('note', ''))
        return _slips_response(request, [_slip_entry(audit.display_name(target), target, password)])


BULK_TOKEN_SESSION_KEY = 'bulk_import_token'


@registrar_required
def bulk_accounts(request):
    """Create many clergy records + portal accounts from a list of names.

    Two steps so nothing is created blind: 'preview' reads and checks the
    names and shows what would happen; 'confirm' creates exactly the rows the
    registrar left ticked and returns the printable slips. The confirm step
    spends a one-time token, so a double-click or a browser refresh can't
    create the same people twice (and can't bring the passwords back)."""
    context = {'active_page': 'clergy_records', 'max_names': bulk.MAX_NAMES}

    if request.method != 'POST':
        return render(request, 'registrar/bulk-accounts.html', context)

    if request.POST.get('step') == 'confirm':
        token = request.session.pop(BULK_TOKEN_SESSION_KEY, None)
        if not token or token != request.POST.get('token'):
            messages.error(request, 'That import was already submitted or has expired. Start again.')
            return redirect('registrar:bulk_accounts')

        # Re-checked here, not trusted from the preview: the form fields are
        # editable by the browser.
        names = bulk.valid_names(bulk.build_preview(request.POST.getlist('names')))[:bulk.MAX_NAMES]
        if not names:
            messages.error(request, 'Nothing selected to create.')
            return redirect('registrar:bulk_accounts')

        try:
            with transaction.atomic():
                entries = []
                for name in names:
                    record = ClergyRecord(name=name)
                    record.id = ClergyRecord.allocate_id()
                    record.save()
                    user, password = provision_account(record)
                    audit.record(request.user, user, 'account_created', target_name=record.name)
                    entries.append(_slip_entry(record.name, user, password))
                return _slips_response(request, entries)
        except ProvisionError as exc:
            messages.error(request, f'Nothing was created. {exc}')
            return redirect('registrar:bulk_accounts')

    try:
        names = bulk.read_names(text=request.POST.get('names_text', ''), upload=request.FILES.get('names_file'))
    except bulk.BulkInputError as exc:
        messages.error(request, str(exc))
        return render(request, 'registrar/bulk-accounts.html', {**context, 'names_text': request.POST.get('names_text', '')})

    token = secrets.token_urlsafe(16)
    request.session[BULK_TOKEN_SESSION_KEY] = token
    rows = bulk.build_preview(names)
    return render(request, 'registrar/bulk-accounts.html', {
        **context,
        'preview_rows': rows,
        'token': token,
        'creatable': len(bulk.valid_names(rows)),
        'setup_window_days': SETUP_WINDOW_DAYS,
    })


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
            keep_signed_in_after_password_change(request, request.user)
            messages.success(request, 'Password changed successfully.')
            return redirect('registrar:change_password')
    return render(request, 'registrar/change-password.html', {'active_page': 'change_password'})
