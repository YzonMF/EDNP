import secrets
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db import transaction
from urllib.parse import urlencode
from django.db import models as db_models
from django.http import FileResponse, Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import dateformat, timezone

from accounts import audit
from accounts.models import AccountAuditLog
from accounts.views import password_confirmed, security_context
from clergy import notifications
from clergy.models import ClergyProfile
from ednp import encryption, security_log

from . import archive, backups, bulk, reports, slips
from .decorators import registrar_required
from .forms import ClergyRecordForm, DocumentUploadForm, EmberLetterUploadForm, PersonalInfoForm, first_error
from .models import (RANK_CHOICES, REPORT_ARCHIVE_DAYS, BackupArchive, ClergyRecord, ClergyDeployment, Deanery, Document, DocumentType,
                     EmberLetter, GeneratedReport, delete_ember_letter as remove_ember_letter,
                     note_document_submission, save_document, save_ember_letter, sync_expired_contracts)
from .provisioning import (SETUP_WINDOW_DAYS, ProvisionError, account_status, provision_account, reset_credentials,
                           reset_lost_phone)

NEAR_EXPIRY_WINDOW_DAYS = 180
CLERGY_RECORDS_PER_PAGE = 100
GENERATED_REPORTS_PER_PAGE = 10


def _can_access_document(user, document):
    """Registrar (staff) sees every document; a clergy user only sees
    documents on their own linked record — checked here, server-side, not
    just by which links happen to be rendered on their pages."""
    if user.is_staff:
        return True
    profile = getattr(user, 'clergy_profile', None)
    return profile is not None and profile.record_id == document.record_id


# A stored file can be gone while its database row remains: a redeploy replaces the
# server's disk, and keeps uploads only if MEDIA_ROOT is on a persistent volume. That is
# shown as missing (or a 404), not a server error.

def _open_stored(field_file):
    try:
        return field_file.open('rb')
    except FileNotFoundError:
        raise Http404('That file is no longer on the server.')


def _stored_size(field_file):
    """Size in bytes, or None if the file is no longer on the server."""
    try:
        return field_file.size
    except FileNotFoundError:
        return None


@login_required(login_url='index')
def view_document(request, document_id):
    document = get_object_or_404(Document, id=document_id)
    if not _can_access_document(request.user, document) or not document.file or not document.file.name:
        raise Http404
    filename = document.file.name.rsplit('/', 1)[-1]
    return FileResponse(_open_stored(document.file), as_attachment=False, filename=filename)


@login_required(login_url='index')
def download_document(request, document_id):
    document = get_object_or_404(Document, id=document_id)
    if not _can_access_document(request.user, document) or not document.file or not document.file.name:
        raise Http404
    filename = document.file.name.rsplit('/', 1)[-1]
    return FileResponse(_open_stored(document.file), as_attachment=True, filename=filename)


@login_required(login_url='index')
def view_profile_picture(request, record_id):
    record = get_object_or_404(ClergyRecord, id=record_id)
    record.record_expired_contract()
    profile = getattr(request.user, 'clergy_profile', None)
    if not (request.user.is_staff or (profile is not None and profile.record_id == record.id)):
        raise Http404
    if not record.profile_picture:
        raise Http404
    filename = record.profile_picture.name.rsplit('/', 1)[-1]
    response = FileResponse(_open_stored(record.profile_picture), as_attachment=False, filename=filename)
    response['Cache-Control'] = 'private, no-cache'
    return response


def _format_file_size(num_bytes):
    if not num_bytes:
        return '0 KB'
    if num_bytes < 1024 * 1024:
        return f'{max(1, round(num_bytes / 1024))} KB'
    return f'{num_bytes / (1024 * 1024):.1f} MB'


def serialize_ember_letter(ember):
    raw_name = ember.file.name.rsplit('/', 1)[-1]
    prefix = f'{ember.year}_Q{ember.quarter}_'
    display_name = raw_name[len(prefix):] if raw_name.startswith(prefix) else raw_name
    local_dt = timezone.localtime(ember.submitted_date) if timezone.is_aware(ember.submitted_date) else ember.submitted_date
    return {
        'id': ember.id,
        'year': ember.year,
        'quarter': ember.quarter,
        'file_name': display_name,
        'file_size': ember.file_size,
        'file_size_label': _format_file_size(ember.file_size),
        'submitted_date': dateformat.format(local_dt, 'M j, Y'),
        'view_url': reverse('registrar:view_ember_letter', args=[ember.id]),
        'download_url': reverse('registrar:download_ember_letter', args=[ember.id]),
        'delete_url': reverse('registrar:delete_ember_letter', args=[ember.id]),
    }


def build_ember_context(record, upload_url):
    letters = list(record.ember_letters.order_by('-year', 'quarter'))
    serialized = [serialize_ember_letter(el) for el in letters]
    current_year = date.today().year
    years_choices = list(range(current_year, 1959, -1))
    summary = record.ember_summary()
    return {
        'ember_summary': summary,
        'ember_config': {
            'upload_url': upload_url,
            'years_choices': years_choices,
            'min_year': 1950,
            'max_year': current_year,
            'max_bytes': 5 * 1024 * 1024,
            'letters': serialized,
            'summary': summary,
        },
    }


def handle_ember_upload(request, record):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required.'}, status=405)
    form = EmberLetterUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        return JsonResponse({'ok': False, 'error': first_error(form) or 'Invalid upload.'}, status=400)
    year = form.cleaned_data['year']
    quarter = form.cleaned_data['quarter']
    upload = form.cleaned_data['file']
    ember, replaced = save_ember_letter(record, year, quarter, upload, user=request.user)
    target_user = getattr(getattr(record, 'profile', None), 'user', None)
    action = 'ember_replaced' if replaced else 'ember_uploaded'
    audit.record(
        request.user,
        target_user,
        action,
        note=f'{record.id} · {year} Q{quarter}',
        target_name=record.name,
    )
    notifications.ember_letter_uploaded(ember, actor=request.user)
    return JsonResponse({
        'ok': True,
        'replaced': replaced,
        'letter': serialize_ember_letter(ember),
        'summary': record.ember_summary(),
    })


@registrar_required
def upload_ember_letter(request, record_id):
    record = get_object_or_404(ClergyRecord, id=record_id)
    return handle_ember_upload(request, record)


@login_required(login_url='index')
def view_ember_letter(request, ember_id):
    ember = get_object_or_404(EmberLetter, id=ember_id)
    if not _can_access_document(request.user, ember):
        raise Http404
    filename = ember.file.name.rsplit('/', 1)[-1]
    return FileResponse(_open_stored(ember.file), as_attachment=False, filename=filename)


@login_required(login_url='index')
def download_ember_letter(request, ember_id):
    ember = get_object_or_404(EmberLetter, id=ember_id)
    if not _can_access_document(request.user, ember):
        raise Http404
    filename = ember.file.name.rsplit('/', 1)[-1]
    return FileResponse(_open_stored(ember.file), as_attachment=True, filename=filename)


@login_required(login_url='index')
def delete_ember_letter(request, ember_id):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required.'}, status=405)
    ember = get_object_or_404(EmberLetter.objects.select_related('record'), id=ember_id)
    if not _can_access_document(request.user, ember):
        raise Http404
    record = ember.record
    year, quarter = ember.year, ember.quarter
    remove_ember_letter(ember)
    target_user = getattr(getattr(record, 'profile', None), 'user', None)
    audit.record(
        request.user,
        target_user,
        'ember_deleted',
        note=f'{record.id} · {year} Q{quarter}',
        target_name=record.name,
    )
    if request.user.is_staff:
        notifications.ember_letter_deleted(record, request.user, f'{year} Q{quarter}')
    return JsonResponse({
        'ok': True,
        'year': year,
        'quarter': quarter,
        'summary': record.ember_summary(),
    })


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
    sync_expired_contracts()
    records = ClergyRecord.objects.current()
    near_expiry_cutoff = date.today() + timedelta(days=NEAR_EXPIRY_WINDOW_DAYS)
    stats = {
        'total': records.count(),
        'active': records.filter(status='Active').count(),
        'expiry': records.filter(contract_end__isnull=False, contract_end__lt=near_expiry_cutoff).count(),
        'archived': ClergyRecord.objects.archived().count(),
    }
    recent_records = records.order_by('-created_at')[:5]
    return render(request, 'registrar/dashboard.html', {
        'active_page': 'dashboard',
        'stats': stats,
        'recent_records': recent_records,
        'next_id': ClergyRecord.next_id(),
        'deaneries_map': _build_deaneries_map(),
    })


@registrar_required
def clergy_records(request):
    """The working list, or with `?view=archived` the archived records (retired
    clergy filed away; see registrar/archive.py). Search and the status, deanery,
    and rank filters work within whichever list is showing."""
    query = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    deanery_filter = request.GET.get('deanery', '').strip()
    rank_filter = request.GET.get('rank', '').strip()
    showing_archived = request.GET.get('view') == 'archived'

    deaneries_map = _build_deaneries_map()

    records = ClergyRecord.objects.archived() if showing_archived else ClergyRecord.objects.current()
    if query:
        records = records.filter(
            db_models.Q(name__icontains=query)
            | db_models.Q(assignment__icontains=query)
            | db_models.Q(id__icontains=query)
        )
    if status:
        records = records.filter(status=status)
    if rank_filter:
        records = records.filter(rank__iexact=rank_filter)
    if deanery_filter:
        deanery_id_val = None
        try:
            deanery_id_val = int(deanery_filter)
        except (ValueError, TypeError):
            for d_id, d_data in deaneries_map.items():
                if d_data['name'].lower() == deanery_filter.lower():
                    deanery_id_val = int(d_id)
                    deanery_filter = d_id
                    break
        if deanery_id_val is not None:
            parishes = deaneries_map.get(str(deanery_id_val), {}).get('parishes', [])
            records = records.filter(
                db_models.Q(deanery_id=deanery_id_val)
                | (db_models.Q(deanery__isnull=True) & db_models.Q(assignment__in=parishes))
            )
        else:
            records = records.none()

    records = records.prefetch_related('documents__document_type')
    page = Paginator(records, CLERGY_RECORDS_PER_PAGE).get_page(request.GET.get('page'))
    records = list(page.object_list)
    required_types = list(DocumentType.objects.filter(is_required=True))
    for record in records:
        record.doc_summary = record.document_summary(required_types=required_types)

    # Portal-account state per record, for the Portal Account column: None if
    # there is no account yet, otherwise {'state': 'pending'|'expired'|'activated',
    # 'days_left': int|None}.
    profiles = ClergyProfile.objects.filter(record_id__in=[r.id for r in records]).select_related('user__security')
    account_by_record = {}
    for profile in profiles:
        state, days_left = account_status(getattr(profile.user, 'security', None))
        account_by_record[profile.record_id] = {'state': state, 'days_left': days_left}
    for record in records:
        record.account = account_by_record.get(record.id)

    page_params = {}
    if showing_archived:
        page_params['view'] = 'archived'
    if query:
        page_params['q'] = query
    if status:
        page_params['status'] = status
    if deanery_filter:
        page_params['deanery'] = deanery_filter
    if rank_filter:
        page_params['rank'] = rank_filter

    return render(request, 'registrar/clergy-records.html', {
        'active_page': 'clergy_records',
        'records': records,
        'page': page,
        'page_query': urlencode(page_params),
        'search_query': query,
        'status_filter': status,
        'status_choices': ClergyRecord.STATUS_CHOICES,
        'deanery_filter': deanery_filter,
        'deaneries': [{'id': d_id, 'name': d['name']} for d_id, d in deaneries_map.items()],
        'rank_filter': rank_filter,
        'rank_choices': RANK_CHOICES,
        'showing_archived': showing_archived,
        'archived_count': ClergyRecord.objects.archived().count(),
        'archivable_status': archive.ARCHIVABLE_STATUS,
        'next_id': ClergyRecord.next_id(),
        'deaneries_map': deaneries_map,
    })


ARCHIVED_RECORDS_URL_QUERY = '?view=archived'


def _back_from_archiving(request, record, list_url):
    """Archive and Restore are on both the list and the record's own page; go back to whichever."""
    if request.POST.get('next') == 'record':
        return redirect('registrar:record_detail', record.id)
    return redirect(list_url)


@registrar_required
def archive_record(request, record_id):
    record = get_object_or_404(ClergyRecord, id=record_id)
    if request.method == 'POST':
        try:
            if archive.archive_record(record, request.user):
                notifications.record_archived(record, request.user)
                messages.success(request, f'{record.name} was moved to the archive. The record is kept and can be restored any time.')
        except archive.ArchiveError as exc:
            messages.error(request, str(exc))
    return _back_from_archiving(request, record, reverse('registrar:clergy_records'))


@registrar_required
def restore_record(request, record_id):
    record = get_object_or_404(ClergyRecord, id=record_id)
    if request.method == 'POST' and archive.restore_record(record, request.user):
        notifications.record_restored(record, request.user)
        messages.success(request, f'{record.name} is back on the Clergy Records list.')
    return _back_from_archiving(request, record, reverse('registrar:clergy_records') + ARCHIVED_RECORDS_URL_QUERY)


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
            notifications.account_reset(profile.user, action, request.user)
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
        'show_registrars': len(rows) > 1,   # a lone registrar has nobody else to recover
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

    if request.method == 'POST' and request.POST.get('action') == 'toggle_noted_requirement':
        dt_id = request.POST.get('document_type_id')
        doc_type = get_object_or_404(DocumentType, id=dt_id)
        set_submitted = request.POST.get('set_submitted') == '1'
        if set_submitted:
            raw_date = request.POST.get('submitted_date')
            try:
                sub_date = date.fromisoformat(raw_date) if raw_date else date.today()
            except ValueError:
                sub_date = date.today()
            notes = request.POST.get('notes', '').strip()
            doc = note_document_submission(record, doc_type, submitted=True, submitted_date=sub_date, notes=notes)
            notifications.document_uploaded(doc, registrar=request.user)
            messages.success(request, f"{doc_type.name} marked as Submitted for {record.name}.")
        else:
            note_document_submission(record, doc_type, submitted=False)
            notifications.document_unmarked(record, request.user, doc_type)
            messages.success(request, f"{doc_type.name} marked as Not Submitted for {record.name}.")
        return redirect(f"{request.path}?tab=documents")

    if request.method == 'POST' and 'document_type' in request.POST:
        doc_form = DocumentUploadForm(request.POST, request.FILES)
        if doc_form.is_valid():
            document = save_document(
                record,
                doc_form.cleaned_data['document_type'],
                doc_form.cleaned_data['file'],
                doc_form.cleaned_data['notes'],
            )
            notifications.document_uploaded(document, registrar=request.user)
            messages.success(request, f"{doc_form.cleaned_data['document_type'].name} uploaded for {record.name}.")
        else:
            messages.error(request, first_error(doc_form) or 'Please choose a document type and a file.')
        tab = 'personal' if request.POST.get('action') == 'upload_attachment' else 'documents'
        return redirect(f"{request.path}?tab={tab}")

    if request.method == 'POST' and request.POST.get('action') == 'add_deployment':
        station_name = request.POST.get('station_name', '').strip()
        place_name = request.POST.get('place_name', '').strip()
        role = request.POST.get('role', '').strip() or 'Priest-in-Charge'
        start_date_raw = request.POST.get('start_date')
        end_date_raw = request.POST.get('end_date')
        is_current = request.POST.get('is_current') == '1'
        notes = request.POST.get('notes', '').strip()

        start_date = None
        if start_date_raw:
            try:
                start_date = date.fromisoformat(start_date_raw)
            except ValueError:
                pass
        end_date = None
        if end_date_raw:
            try:
                end_date = date.fromisoformat(end_date_raw)
            except ValueError:
                pass

        if not station_name:
            messages.error(request, 'Please provide a station or parish name.')
        else:
            if is_current:
                record.deployments.filter(is_current=True).update(is_current=False)
            dep = ClergyDeployment.objects.create(
                record=record,
                station_name=station_name,
                place_name=place_name,
                role=role,
                start_date=start_date,
                end_date=end_date,
                is_current=is_current,
                notes=notes
            )
            notifications.deployment_added(record, request.user, dep)
            messages.success(request, f'Deployment at {station_name} added successfully.')
        return redirect(f"{request.path}?tab=personal")

    if request.method == 'POST' and request.POST.get('action') == 'delete_deployment':
        dep_id = request.POST.get('deployment_id')
        dep = get_object_or_404(ClergyDeployment, id=dep_id, record=record)
        station_name = dep.station_name
        dep.delete()
        notifications.deployment_deleted(record, request.user, station_name)
        messages.success(request, 'Deployment removed.')
        return redirect(f"{request.path}?tab=personal")

    if request.method == 'POST':
        was_archived = record.is_archived
        form = PersonalInfoForm(request.POST, instance=record)
        if not form.is_valid():
            messages.error(request, 'Please check the form and try again.')
        elif was_archived and form.cleaned_data['status'] != archive.ARCHIVABLE_STATUS:
            # Only retired clergy are archived, so the status can't move while it is.
            messages.error(request, 'This record is archived. Restore it before changing its status.')
        else:
            form.save()
            record.record_expired_contract()
            notifications.record_updated(record, request.user, form)
            messages.success(request, 'Personal Information updated successfully.')
        return redirect(f"{request.path}?tab=personal")

    records_list_url = reverse('registrar:clergy_records')
    ember_upload_url = reverse('registrar:upload_ember_letter', args=[record.id])
    try:
        clergy_profile = record.profile
    except Exception:
        clergy_profile = None
    return render(request, 'registrar/record-detail.html', {
        'active_page': 'clergy_records',
        'record': record,
        'clergy_profile': clergy_profile,
        'checklist': record.document_checklist_grouped(),
        'active_tab': request.GET.get('tab', 'personal'),
        'deaneries_map': _build_deaneries_map(),
        'record_data': {'deanery_id': record.deanery_id, 'assignment': record.assignment or ''},
        'archivable_status': archive.ARCHIVABLE_STATUS,
        'records_list_url': records_list_url + ARCHIVED_RECORDS_URL_QUERY if record.is_archived else records_list_url,
        **build_ember_context(record, ember_upload_url),
    })


@registrar_required
def documents_overview(request):
    required_types = list(DocumentType.objects.filter(is_required=True))
    records = ClergyRecord.objects.current().prefetch_related('documents__document_type')
    rows = [{'record': r, 'summary': r.document_summary(required_types=required_types)} for r in records]
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
        if rank_value == 'postulancy':
            groups.append({
                'label': 'Holy Order',
                'custom_items': ['Date admitted as Holy Order'],
                'types': types,
            })
    general_types = DocumentType.objects.filter(required_from_rank='none')
    if general_types:
        groups.append({'label': 'Required for Every Clergy Member', 'types': general_types})

    return render(request, 'registrar/ordination-requirements.html', {
        'active_page': 'ordination_requirements',
        'groups': groups,
    })


@registrar_required
def contract_review(request):
    sync_expired_contracts()
    records = ClergyRecord.objects.current().filter(contract_end__isnull=False).order_by('contract_end')
    today = date.today()
    rows = [{'record': r, 'days_left': (r.contract_end - today).days} for r in records]
    return render(request, 'registrar/contract-review.html', {
        'active_page': 'contract_review',
        'rows': rows,
    })


@registrar_required
def generate_report(request):
    archive.purge_expired_reports()   # no scheduler: archived reports past their 30 days go when this page opens
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

    saved = GeneratedReport.objects.select_related('generated_by', 'archived_by')
    page = Paginator(saved.filter(archived_at__isnull=True), GENERATED_REPORTS_PER_PAGE).get_page(request.GET.get('page'))
    archived_reports = list(saved.filter(archived_at__isnull=False).order_by('archived_at'))   # next to go first
    now = timezone.now()
    for generated in archived_reports:
        generated.days_left = archive.days_left(generated, now)
    return render(request, 'registrar/generate-report.html', {
        'active_page': 'generate_report',
        'report_types': reports.REPORT_TYPES.items(),
        'generated_reports': page.object_list,
        'page': page,
        'archived_reports': archived_reports,
        'archive_days': REPORT_ARCHIVE_DAYS,
    })


def _report_or_404(report_id):
    """A saved report, after clearing out any whose 30 days in the archive are up, so an
    old bookmark can't open a report the archive has already let go of."""
    archive.purge_expired_reports()
    return get_object_or_404(GeneratedReport, id=report_id)


@registrar_required
def archive_generated_report(request, report_id):
    generated = _report_or_404(report_id)
    if request.method == 'POST' and archive.archive_report(generated, request.user):
        deletes_on = timezone.now() + timedelta(days=REPORT_ARCHIVE_DAYS)
        messages.success(request, f'{generated.title} was archived. It will be deleted on {deletes_on:%b %d, %Y} unless you restore it.')
    return redirect('registrar:generate_report')


@registrar_required
def restore_generated_report(request, report_id):
    generated = _report_or_404(report_id)
    if request.method == 'POST' and archive.restore_report(generated, request.user):
        messages.success(request, f'{generated.title} is back under Generated Reports.')
    return redirect('registrar:generate_report')


@registrar_required
def delete_generated_report(request, report_id):
    generated = _report_or_404(report_id)
    if request.method == 'POST':
        try:
            if archive.delete_report(generated, request.user):
                messages.success(request, f'{generated.title} was deleted.')
        except archive.ArchiveError as exc:
            messages.error(request, str(exc))
    return redirect('registrar:generate_report')


def _posted_report_ids(request):
    return [value for value in request.POST.getlist('report_ids') if value.isdigit()]


@registrar_required
def archive_selected_reports(request):
    """Archive, for every report ticked under Generated Reports."""
    if request.method == 'POST':
        archive.purge_expired_reports()
        ticked = GeneratedReport.objects.filter(id__in=_posted_report_ids(request), archived_at__isnull=True)
        count = archive.archive_reports(ticked, request.user)
        if count:
            deletes_on = timezone.now() + timedelta(days=REPORT_ARCHIVE_DAYS)
            messages.success(request, f'{count} report{"s were" if count != 1 else " was"} archived. '
                                      f'{"They" if count != 1 else "It"} will be deleted on {deletes_on:%b %d, %Y} unless you restore '
                                      f'{"them" if count != 1 else "it"}.')
        elif not _posted_report_ids(request):
            messages.error(request, 'Tick the reports to archive first.')
    return redirect('registrar:generate_report')


@registrar_required
def delete_archived_reports(request):
    """Delete now, for several archived reports at once: the ticked ones (Delete selected), or
    every one the page listed (Clear all). Only those: one archived after the page was opened
    is left for the registrar to see first."""
    if request.method == 'POST':
        archive.purge_expired_reports()
        posted = GeneratedReport.objects.filter(id__in=_posted_report_ids(request), archived_at__isnull=False)
        count = archive.delete_reports(posted, request.user)
        if count:
            messages.success(request, f'{count} archived report{"s were" if count != 1 else " was"} deleted.')
        elif not _posted_report_ids(request):
            messages.error(request, 'Tick the archived reports to delete first.')
    return redirect('registrar:generate_report')


@registrar_required
def view_generated_report(request, report_id):
    generated = _report_or_404(report_id)
    filename = generated.file.name.rsplit('/', 1)[-1]
    return FileResponse(_open_stored(generated.file), as_attachment=False, filename=filename, content_type='application/pdf')


@registrar_required
def preview_generated_report(request, report_id):
    """Shows the report as an ordinary HTML page (the same template used to
    build the stored PDF), with its own Print button — rather than embedding
    the PDF file itself, which ran into browsers that force a download on a
    direct PDF navigation regardless of what headers say. Renders the data
    fresh as of right now rather than replaying the historical snapshot, so
    "View" is a live look and "Download" remains the exact file generated
    on the original date."""
    generated = _report_or_404(report_id)
    report = reports.build_report(generated.report_type, generated_by=generated.generated_by)
    return render(request, 'registrar/report_pdf.html', {
        'report': report,
        'download_url': reverse('registrar:download_generated_report', args=[generated.id]),
    })


@registrar_required
def download_generated_report(request, report_id):
    generated = _report_or_404(report_id)
    filename = generated.file.name.rsplit('/', 1)[-1]
    return FileResponse(_open_stored(generated.file), as_attachment=True, filename=filename, content_type='application/pdf')


@registrar_required
def backup_records(request):
    # Archives saved on the server before the archive became a straight download, listed until
    # they are deleted so their storage can be freed.
    saved_archives = list(BackupArchive.objects.select_related('created_by'))
    for saved in saved_archives:
        saved.stored_size = _stored_size(saved.file)
    return render(request, 'registrar/backup-records.html', {
        'active_page': 'backup_records',
        'saved_archives': saved_archives,
        'encryption_on': encryption.configured(),
    })


@registrar_required
def download_database_backup(request):
    """The whole database as one file: every clergy member's personal details and
    government ID numbers in one go. So it takes a POST with the registrar's password
    re-entered (a session left open or stolen isn't enough, and wrong guesses count
    toward the sign-in lockout), and every download is written to the activity log."""
    if request.method != 'POST' or not password_confirmed(request):
        return redirect('registrar:backup_records')

    dump_bytes = backups.build_database_dump()
    audit.record(request.user, None, 'database_backup')
    security_log.export(request, request.user, 'database backup downloaded')
    filename = f"database_backup_{timezone.now().strftime('%Y%m%d_%H%M%S')}.json"
    response = HttpResponse(dump_bytes, content_type='application/json')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Cache-Control'] = 'no-store'
    return response


@registrar_required
def download_documents_archive(request):
    """Every uploaded document in one .zip, built as it downloads (backups.documents_zip) and
    never kept on the server. Like the database backup it is everything at once, so it takes
    the registrar's password re-entered, and every download is written to the activity log."""
    if request.method != 'POST' or not password_confirmed(request):
        return redirect('registrar:backup_records')

    filename = f"documents_archive_{timezone.now().strftime('%Y%m%d_%H%M%S')}.zip"
    audit.record(request.user, None, 'archive_downloaded', note=filename)
    security_log.export(request, request.user, 'documents archive downloaded')
    response = StreamingHttpResponse(backups.documents_zip(), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Cache-Control'] = 'no-store'
    return response


@registrar_required
def download_backup_archive(request, archive_id):
    if request.method != 'POST' or not password_confirmed(request):
        return redirect('registrar:backup_records')

    saved = get_object_or_404(BackupArchive, id=archive_id)
    filename = saved.file.name.rsplit('/', 1)[-1]
    try:
        handle = saved.file.open('rb')
    except FileNotFoundError:
        messages.error(request, "That archive's file is no longer on the server. Use Download Documents Archive for a new one.")
        return redirect('registrar:backup_records')
    audit.record(request.user, None, 'archive_downloaded', note=filename)
    security_log.export(request, request.user, 'documents archive downloaded')
    return FileResponse(handle, as_attachment=True, filename=filename, content_type='application/zip')


@registrar_required
def delete_backup_archive(request, archive_id):
    """Deletes an archive saved on the server (a full copy of every document, as it was then)
    to free its storage. The file goes once the row's deletion has committed."""
    saved = get_object_or_404(BackupArchive, id=archive_id)
    if request.method == 'POST':
        name = saved.file.name
        with transaction.atomic():
            deleted, _ = BackupArchive.objects.filter(pk=saved.pk).delete()
            if deleted:
                audit.record(request.user, None, 'archive_deleted', note=name.rsplit('/', 1)[-1])
                storage = saved.file.storage
                transaction.on_commit(lambda: storage.delete(name))   # a file already gone is fine
        if deleted:
            messages.success(request, f"The archive saved {timezone.localtime(saved.created_at):%b %d, %Y} was deleted.")
    return redirect('registrar:backup_records')


@registrar_required
def my_profile(request):
    """The registrar's own name, username, number, password and trusted devices,
    in one place. The actions themselves live in accounts.views.my_security."""
    return render(request, 'registrar/my-profile.html', {'active_page': 'my_profile', **security_context(request)})


@registrar_required
def change_password(request):
    """Old address of the Change Password page, now part of My Profile."""
    return redirect('registrar:my_profile')
