from datetime import date

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from accounts.views import security_context
from registrar.forms import DocumentUploadForm, first_error
from registrar.models import save_document

from .decorators import clergy_required
from .forms import ClergyProfileForm, ClergyRecordSelfServiceForm
from .models import ClergyProfile, Notification
from .notifications import (ago, attention, document_uploaded, mark_all_read, mark_read, profile_updated,
                            sync_reminders)


def _get_or_create_profile(user):
    profile, _ = ClergyProfile.objects.get_or_create(user=user)
    return profile


@clergy_required
def dashboard(request):
    profile = _get_or_create_profile(request.user)
    sync_reminders(request.user, profile.record)   # every sign-in lands here
    days_left = None
    if profile.record and profile.record.contract_end:
        days_left = (profile.record.contract_end - date.today()).days
    return render(request, 'clergy/dashboard.html', {
        'active_page': 'dashboard',
        'profile': profile,
        'days_left': days_left,
    })


MEDICAL_PSYCH_DOCUMENT_NAMES = ('Medical Exam Result', 'Psychiatric Evaluation Result')


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

    if request.method == 'POST':
        form = ClergyProfileForm(request.POST, instance=profile)
        record_form = ClergyRecordSelfServiceForm(request.POST, instance=profile.record)
        if form.is_valid() and record_form.is_valid():
            form.save()
            record_form.save()
            profile_updated(request.user, (record_form, form))
            messages.success(request, 'Profile updated successfully.')
            return redirect('clergy:my_profile')
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
        if form.is_valid() and form.cleaned_data['document_type'].uploaded_by == 'registrar':
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

    return render(request, 'clergy/my-requirements.html', {
        'active_page': 'my_requirements',
        'profile': profile,
        'checklist': profile.record.document_checklist_grouped(),
    })
