from datetime import date

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from accounts.views import security_context
from registrar.forms import DocumentUploadForm, ProfilePictureForm, first_error
from registrar.models import ClergyDeployment, delete_profile_picture, save_document, save_profile_picture
from registrar.views import build_ember_context, handle_ember_upload

from .decorators import clergy_required
from .forms import ClergyProfileForm, ClergyRecordSelfServiceForm
from .models import ClergyProfile, Notification, ClergyChild, ClergyEducation
from .notifications import (ago, attention, document_uploaded, mark_all_read, mark_read, profile_updated,
                            sync_reminders)


def _get_or_create_profile(user):
    profile, _ = ClergyProfile.objects.get_or_create(user=user)
    return profile


@clergy_required
def dashboard(request):
    profile = _get_or_create_profile(request.user)
    if profile.record:
        profile.record.record_expired_contract()
    sync_reminders(request.user, profile.record)   # every sign-in lands here
    days_left = None
    if profile.record and profile.record.contract_end:
        days_left = (profile.record.contract_end - date.today()).days
    return render(request, 'clergy/dashboard.html', {
        'active_page': 'dashboard',
        'profile': profile,
        'days_left': days_left,
    })


MEDICAL_PSYCH_DOCUMENT_NAMES = (
    'Medical Exam Result',
    'Psychiatric Evaluation Result',
    'Medical Exam Result (Priest)',
    'Psychiatric Evaluation Result (Priest)',
)


@clergy_required
def my_profile(request):
    """My Record & Profile — one merged page: the Registrar-controlled
    Clergy Record + Ordination history (view-only here, edited from the
    Registrar's record-detail page), alongside the clergy-editable Personal
    Information, Marriage/Family, Government ID and Account sections."""
    profile = _get_or_create_profile(request.user)

    if not profile.record:
        messages.error(request, 'Your account is not yet linked to an official clergy record — ask the Registrar to link one before editing your profile.')
        return redirect('clergy:dashboard')

    profile.record.record_expired_contract()
    days_left = None
    if profile.record.contract_end:
        days_left = (profile.record.contract_end - date.today()).days

    medical_psych = [
        c for c in profile.record.document_checklist()
        if c['document_type'].name in MEDICAL_PSYCH_DOCUMENT_NAMES
    ]

    # Username, password, mobile number and trusted devices are on the Security
    # tab; their actions live in accounts.views.my_security.
    active_tab = 'security' if request.GET.get('tab') == 'security' else 'personal'

    # Backfill legacy children string into structured ClergyChild records if needed
    if profile.children_names and profile.children.count() == 0:
        for raw_name in profile.children_names.split(','):
            clean_name = raw_name.strip()
            if clean_name:
                ClergyChild.objects.create(profile=profile, name=clean_name)

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
                profile.record.deployments.filter(is_current=True).update(is_current=False)
            ClergyDeployment.objects.create(
                record=profile.record,
                station_name=station_name,
                place_name=place_name,
                role=role,
                start_date=start_date,
                end_date=end_date,
                is_current=is_current,
                notes=notes
            )
            messages.success(request, f'Deployment at {station_name} added successfully.')
        return redirect('clergy:my_profile')

    if request.method == 'POST' and request.POST.get('action') == 'delete_deployment':
        dep_id = request.POST.get('deployment_id')
        get_object_or_404(ClergyDeployment, id=dep_id, record=profile.record).delete()
        messages.success(request, 'Deployment removed.')
        return redirect('clergy:my_profile')

    if request.method == 'POST' and request.POST.get('action') == 'upload_attachment':
        form = DocumentUploadForm(request.POST, request.FILES)
        if form.is_valid() and form.cleaned_data['document_type'].is_noted_only:
            messages.error(request, 'That requirement does not accept document uploads — it is noted directly by the Registrar.')
        elif form.is_valid() and form.cleaned_data['document_type'].uploaded_by == 'registrar':
            messages.error(request, 'That document is provided by the Registrar — you cannot upload it yourself.')
        elif form.is_valid():
            document = save_document(
                profile.record,
                form.cleaned_data['document_type'],
                form.cleaned_data['file'],
                form.cleaned_data.get('notes', ''),
            )
            document_uploaded(document)
            messages.success(request, f"{form.cleaned_data['document_type'].name} uploaded successfully.")
        else:
            messages.error(request, first_error(form) or 'Please choose a valid document file (PDF, PNG, JPG).')
        return redirect('clergy:my_profile')

    if request.method == 'POST' and 'remove_profile_picture' in request.POST:
        if profile.record.profile_picture:
            delete_profile_picture(profile.record)
            messages.success(request, 'Profile picture removed.')
        return redirect('clergy:my_profile')

    if request.method == 'POST' and 'upload_profile_picture' in request.POST:
        pic_form = ProfilePictureForm(request.POST, request.FILES)
        if pic_form.is_valid():
            save_profile_picture(profile.record, pic_form.cleaned_data['profile_picture'])
            messages.success(request, 'Profile picture updated successfully.')
        else:
            messages.error(request, first_error(pic_form) or 'Please choose a JPG or PNG image.')
        return redirect('clergy:my_profile')

    if request.method == 'POST':
        form = ClergyProfileForm(request.POST, instance=profile)
        record_form = ClergyRecordSelfServiceForm(request.POST, instance=profile.record)
        pic_form = ProfilePictureForm(request.POST, request.FILES) if 'profile_picture' in request.FILES else None
        if pic_form is not None and not pic_form.is_valid():
            messages.error(request, first_error(pic_form) or 'Please choose a valid JPG or PNG image.')
        elif form.is_valid() and record_form.is_valid():
            with transaction.atomic():
                saved_profile = form.save()
                record_form.save()

                # Process Children list (only applicable if Married or Widowed)
                if saved_profile.civil_status == 'Single':
                    saved_profile.children.all().delete()
                    if saved_profile.children_names:
                        saved_profile.children_names = ''
                        saved_profile.save(update_fields=['children_names'])
                elif 'child_name' in request.POST:
                    profile.children.all().delete()
                    child_names = request.POST.getlist('child_name')
                    child_dobs = request.POST.getlist('child_dob')
                    child_genders = request.POST.getlist('child_gender')
                    new_children = []
                    for c_name, c_dob, c_gender in zip(child_names, child_dobs, child_genders):
                        c_name = c_name.strip()
                        if c_name:
                            b_date = None
                            if c_dob:
                                try:
                                    b_date = date.fromisoformat(c_dob)
                                except ValueError:
                                    pass
                            new_children.append(ClergyChild(
                                profile=profile,
                                name=c_name,
                                birth_date=b_date,
                                gender=c_gender or ''
                            ))
                    if new_children:
                        ClergyChild.objects.bulk_create(new_children)
                    profile.children_names = ", ".join(c.name for c in new_children)
                    profile.save(update_fields=['children_names'])

                # Process Education list
                if 'edu_degree' in request.POST or 'edu_institution' in request.POST:
                    profile.educations.all().delete()
                    edu_degrees = request.POST.getlist('edu_degree')
                    edu_institutions = request.POST.getlist('edu_institution')
                    edu_places = request.POST.getlist('edu_place')
                    edu_years = request.POST.getlist('edu_year')
                    new_edus = []
                    for deg, inst, plc, yr in zip(edu_degrees, edu_institutions, edu_places, edu_years):
                        deg = deg.strip()
                        inst = inst.strip()
                        plc = plc.strip()
                        grad_yr = int(yr.strip()) if yr and yr.strip().isdigit() else None
                        if deg or inst:
                            new_edus.append(ClergyEducation(
                                profile=profile,
                                degree=deg or 'Degree',
                                institution=inst,
                                place=plc,
                                year_graduated=grad_yr
                            ))
                    if new_edus:
                        ClergyEducation.objects.bulk_create(new_edus)

                if pic_form is not None:
                    save_profile_picture(profile.record, pic_form.cleaned_data['profile_picture'])
                profile_updated(request.user, (record_form, form))
            messages.success(request, 'Profile updated successfully.')
            return redirect('clergy:my_profile')
        else:
            messages.error(request, 'Please check the form and try again.')
    else:
        form = ClergyProfileForm(instance=profile)
        record_form = ClergyRecordSelfServiceForm(instance=profile.record)

    context = {
        'active_page': 'my_profile',
        'active_tab': active_tab,
        'profile': profile,
        'form': form,
        'record_form': record_form,
        'days_left': days_left,
        'medical_psych': medical_psych,
    }
    if active_tab == 'security':
        context.update(security_context(request))
    return render(request, 'clergy/my-profile.html', context)


@clergy_required
def print_own_record(request):
    """A letterhead-style printable copy of the clergy member's own record —
    opened in a new tab, with its own Print button and a Download PDF link.
    Deliberately not persisted anywhere (unlike the Registrar's Generate
    Report history); it's a point-in-time personal copy, not an official
    archived document."""
    profile = _get_or_create_profile(request.user)
    if not profile.record:
        messages.error(request, 'Your account is not yet linked to an official clergy record — ask the Registrar to link one first.')
        return redirect('clergy:dashboard')
    profile.record.record_expired_contract()

    return render(request, 'clergy/print-record.html', {
        'profile': profile,
        'checklist': profile.record.document_checklist(),
        'download_url': reverse('clergy:download_own_record'),
    })


@clergy_required
def download_own_record(request):
    """Same template as print_own_record, rendered to a real PDF via
    WeasyPrint instead of the browser's print dialog — generated fresh on
    every request, not persisted, same point-in-time-copy philosophy."""
    profile = _get_or_create_profile(request.user)
    if not profile.record:
        messages.error(request, 'Your account is not yet linked to an official clergy record — ask the Registrar to link one first.')
        return redirect('clergy:dashboard')

    from ednp.pdf import html_to_pdf

    html = render_to_string('clergy/print-record.html', {
        'profile': profile,
        'checklist': profile.record.document_checklist(),
    })
    pdf_bytes = html_to_pdf(html)
    filename = f"clergy_record_{profile.record.id or request.user.username}.pdf"
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


NOTIFICATION_TABS = [('all', 'All'), ('security', 'Security'), ('record', 'Record'), ('documents', 'Documents')]
NOTIFICATIONS_PER_PAGE = 20


@clergy_required
def notifications(request):
    """What needs attention right now (worked out fresh each time) above everything that
    has happened to the account and record (stored as it happened), newest first. Opening
    a page of notifications marks those as read; the ones that were new say so this once."""
    if request.method == 'POST':
        if request.POST.get('action') == 'mark_all_read':
            mark_all_read(request.user)
        return redirect('clergy:notifications')

    profile = _get_or_create_profile(request.user)
    sync_reminders(request.user, profile.record)

    tab = request.GET.get('tab')
    if tab not in dict(NOTIFICATION_TABS):
        tab = 'all'
    entries = Notification.objects.filter(user=request.user)
    if tab != 'all':
        entries = entries.filter(kind__in=Notification.kinds_under(tab))
    page = Paginator(entries, NOTIFICATIONS_PER_PAGE).get_page(request.GET.get('page'))

    links = {
        'security': (reverse('clergy:my_profile') + '?tab=security', 'Security settings'),
        'record': (reverse('clergy:my_profile'), 'My record'),
        'documents': (reverse('clergy:my_requirements'), 'My requirements'),
    }
    now = timezone.now()
    items = list(page.object_list)
    for item in items:
        item.is_new = item.read_at is None
        item.ago = ago(item.created_at, now)
        item.link, item.link_text = links[item.tab]
    mark_read(items)

    return render(request, 'clergy/notifications.html', {
        'active_page': 'notifications',
        'profile': profile,
        'attention': attention(profile.record),
        'tabs': NOTIFICATION_TABS,
        'tab': tab,
        'items': items,
        'page': page,
    })


@clergy_required
def my_requirements(request):
    profile = _get_or_create_profile(request.user)

    if not profile.record:
        messages.error(request, 'Your account is not yet linked to an official clergy record — ask the Registrar to link one before uploading documents.')
        return redirect('clergy:dashboard')

    if request.method == 'POST':
        form = DocumentUploadForm(request.POST, request.FILES)
        if form.is_valid() and form.cleaned_data['document_type'].is_noted_only:
            messages.error(request, 'That requirement does not accept document uploads — it is noted directly by the Registrar.')
        elif form.is_valid() and form.cleaned_data['document_type'].uploaded_by == 'registrar':
            messages.error(request, 'That document is provided by the Registrar — you cannot upload it yourself.')
        elif form.is_valid():
            document = save_document(
                profile.record,
                form.cleaned_data['document_type'],
                form.cleaned_data['file'],
                form.cleaned_data['notes'],
            )
            document_uploaded(document)
            messages.success(request, f"{form.cleaned_data['document_type'].name} uploaded successfully.")
            return redirect('clergy:my_requirements')
        else:
            messages.error(request, first_error(form) or 'Please choose a document type and a file.')

    ember_upload_url = reverse('clergy:upload_ember_letter')
    return render(request, 'clergy/my-requirements.html', {
        'active_page': 'my_requirements',
        'profile': profile,
        'checklist': profile.record.document_checklist_grouped(),
        'active_tab': request.GET.get('tab', 'checklist'),
        **build_ember_context(profile.record, ember_upload_url),
    })


@clergy_required
def upload_ember_letter(request):
    profile = _get_or_create_profile(request.user)
    if not profile.record:
        return JsonResponse({
            'ok': False,
            'error': 'Your account is not yet linked to an official clergy record.',
        }, status=400)
    return handle_ember_upload(request, profile.record)
