from datetime import date

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse

from ednp.password_helpers import validate_password_or_flag_errors
from registrar.forms import DocumentUploadForm, first_error
from registrar.models import save_document

from .decorators import clergy_required
from .forms import ClergyProfileForm, ClergyRecordSelfServiceForm
from .models import ClergyProfile


def _get_or_create_profile(user):
    profile, _ = ClergyProfile.objects.get_or_create(user=user)
    return profile


@clergy_required
def dashboard(request):
    profile = _get_or_create_profile(request.user)
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

    if request.method == 'POST':
        form = ClergyProfileForm(request.POST, instance=profile)
        record_form = ClergyRecordSelfServiceForm(request.POST, instance=profile.record)
        new_username = request.POST.get('username', '').strip()
        new_password = request.POST.get('password', '')

        password_ok = not new_password or validate_password_or_flag_errors(request, new_password, user=request.user)

        if not password_ok:
            pass  # validate_password_or_flag_errors() already queued the error messages
        elif (new_username != request.user.username
              and User.objects.filter(username__iexact=new_username).exclude(pk=request.user.pk).exists()):
            messages.error(request, 'That username is already taken.')
        elif form.is_valid() and record_form.is_valid():
            form.save()
            record_form.save()
            request.user.username = new_username
            if new_password:
                request.user.set_password(new_password)
            request.user.save()
            update_session_auth_hash(request, request.user)
            messages.success(request, 'Profile updated successfully.')
            return redirect('clergy:my_profile')
        else:
            messages.error(request, 'Please check the form and try again.')
    else:
        form = ClergyProfileForm(instance=profile)
        record_form = ClergyRecordSelfServiceForm(instance=profile.record)

    return render(request, 'clergy/my-profile.html', {
        'active_page': 'my_profile',
        'profile': profile,
        'form': form,
        'record_form': record_form,
        'days_left': days_left,
        'medical_psych': medical_psych,
    })


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

    from weasyprint import HTML

    html = render_to_string('clergy/print-record.html', {
        'profile': profile,
        'checklist': profile.record.document_checklist(),
    })
    pdf_bytes = HTML(string=html).write_pdf()
    filename = f"clergy_record_{profile.record.id or request.user.username}.pdf"
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@clergy_required
def notifications(request):
    profile = _get_or_create_profile(request.user)
    return render(request, 'clergy/notifications.html', {'active_page': 'notifications', 'profile': profile})


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
            save_document(
                profile.record,
                form.cleaned_data['document_type'],
                form.cleaned_data['file'],
                form.cleaned_data['notes'],
            )
            messages.success(request, f"{form.cleaned_data['document_type'].name} uploaded successfully.")
            return redirect('clergy:my_requirements')
        else:
            messages.error(request, first_error(form) or 'Please choose a document type and a file.')

    return render(request, 'clergy/my-requirements.html', {
        'active_page': 'my_requirements',
        'profile': profile,
        'checklist': profile.record.document_checklist_grouped(),
    })
