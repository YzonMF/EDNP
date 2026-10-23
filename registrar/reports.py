"""Report data-building and PDF rendering for the registrar's Generate Report page.

Every report is normalized into the same shape — a title plus a list of
sections, each with its own columns/rows — so the HTML view and the PDF
export can walk the same structure instead of having per-report templates.
"""

from datetime import date

from django.utils import timezone
from django.utils.html import escape
from django.utils.safestring import mark_safe

from .models import ClergyRecord, DocumentType, Parish

REPORT_TYPES = {
    'clerical_directory': 'Clerical Directory',
    'status_report': 'Status Report',
    'per_deanery': 'Per-Deanery Report',
    'ordination_documents': 'Ordination Documents Report (Registrar)',
}


def _assignment_to_deanery_map():
    """Fallback for older records where `deanery` FK wasn't set yet: maps the
    parish dropdown label ('St. Peter - Alab') back to its deanery name."""
    mapping = {}
    for parish in Parish.objects.select_related('deanery'):
        label = f"{parish.parish_name} - {parish.place_name}" if parish.place_name else parish.parish_name
        mapping[label] = parish.deanery.name
    return mapping


def _fmt_date(value):
    return value.strftime('%b %d, %Y') if value else '—'


def _build_clerical_directory():
    records = (
        ClergyRecord.objects.all()
        .select_related('profile', 'deanery')
        .prefetch_related('deployments', 'profile__children', 'profile__educations')
        .order_by('name')
    )
    entries = []
    columns = ['No.', 'Name', 'Deployment / Assignment', 'Date of Ordination', 'Status']
    rows = []

    for i, r in enumerate(records, start=1):
        profile = getattr(r, 'profile', None)

        # DOB: Date of birth + place
        dob_parts = []
        if r.date_of_birth:
            dob_parts.append(r.date_of_birth.strftime('%b %d, %Y'))
        if profile and profile.place_of_birth:
            dob_parts.append(profile.place_of_birth.strip())
        dob_display = ' — '.join(dob_parts) if dob_parts else '—'

        # Grad: Grad-year graduated + place
        grad_items = []
        if profile:
            edus = sorted(profile.educations.all(), key=lambda e: (e.year_graduated or 0, e.id or 0))
            for edu in edus:
                parts = []
                if edu.year_graduated:
                    parts.append(str(edu.year_graduated))
                loc = ', '.join(p.strip() for p in [edu.institution, edu.place] if p and p.strip())
                if loc:
                    parts.append(loc)
                summary = ' — '.join(parts) if parts else ''
                if edu.degree:
                    summary = f"{summary} ({edu.degree})" if summary else edu.degree
                if summary:
                    grad_items.append(summary)
        if not grad_items and profile and profile.education:
            grad_items.append(profile.education.strip())
        grad_display = '; '.join(grad_items) if grad_items else '—'

        # OD: ordination to the diaconate : date and place
        od_parts = []
        if r.diaconate_date:
            od_parts.append(r.diaconate_date.strftime('%b %d, %Y'))
        if r.diaconate_place:
            od_parts.append(r.diaconate_place.strip())
        od_display = ' — '.join(od_parts) if od_parts else '—'

        # OP: ordination to the priesthood: date and place
        op_parts = []
        if r.ordination_date:
            op_parts.append(r.ordination_date.strftime('%b %d, %Y'))
        if r.ordination_place:
            op_parts.append(r.ordination_place.strip())
        op_display = ' — '.join(op_parts) if op_parts else '—'

        # Dep: deployments; date and place of each deployment, latest to oldest
        deployments = []
        for dep in r.deployments.all():
            summary = dep.summary_display
            if summary:
                deployments.append(summary)
        if not deployments and r.assignment:
            deployments.append(f"Current: {r.assignment}")

        # S: Spouse, name and birth date
        spouse_parts = []
        if profile and profile.spouse_name:
            spouse_parts.append(profile.spouse_name.strip())
        if profile and profile.spouse_birth_date:
            bdate_str = f"b. {profile.spouse_birth_date.strftime('%b %d, %Y')}"
            if spouse_parts:
                spouse_parts[0] = f"{spouse_parts[0]} ({bdate_str})"
            else:
                spouse_parts.append(bdate_str)
        spouse_display = spouse_parts[0] if spouse_parts else '—'

        # M: Place of marriage: date and place
        m_parts = []
        if profile and profile.marriage_date:
            m_parts.append(profile.marriage_date.strftime('%b %d, %Y'))
        if profile and profile.marriage_place:
            m_parts.append(profile.marriage_place.strip())
        marriage_display = ' — '.join(m_parts) if m_parts else '—'

        # C: Child/childrens: names ,and date of birth
        children_items = []
        if profile:
            kids = sorted(profile.children.all(), key=lambda c: (c.birth_date or date.min, c.id or 0))
            for child in kids:
                if child.birth_date:
                    children_items.append(f"{child.name.strip()} (b. {child.birth_date.strftime('%b %d, %Y')})")
                else:
                    children_items.append(child.name.strip())
        if not children_items and profile and profile.children_names:
            children_items.append(profile.children_names.strip())
        children_display = '; '.join(children_items) if children_items else '—'

        entries.append({
            'id': r.id,
            'name': r.name,
            'rank_display': r.get_rank_display(),
            'status': r.status,
            'dob': dob_display,
            'grad': grad_display,
            'od': od_display,
            'op': op_display,
            'deployments': deployments,
            'spouse': spouse_display,
            'marriage': marriage_display,
            'children': children_display,
        })

        name_cell = mark_safe(f'<b>{escape(r.name)}</b><span class="record-id">{escape(r.id)}</span>')
        rows.append([str(i), name_cell, r.assignment or '—', _fmt_date(r.ordination_date), r.status])

    return {
        'sections': [{'heading': None, 'columns': columns, 'rows': rows}],
        'entries': entries,
    }


def _build_status_report():
    records = ClergyRecord.objects.all().order_by('name')
    columns = ['Name', 'Status', 'Leave Reason', 'Current Deployment']
    rows = []
    for r in records:
        reason = r.leave_reason if r.status == 'On Leave' else ''
        rows.append([r.name, r.status, reason or '—', r.assignment or '—'])
    return [{'heading': None, 'columns': columns, 'rows': rows}]


def _build_per_deanery_report():
    mapping = _assignment_to_deanery_map()
    records = ClergyRecord.objects.select_related('deanery').order_by('name')
    groups = {}
    for r in records:
        deanery = (
            r.deanery.name if r.deanery
            else mapping.get(r.assignment, 'Unassigned / Not Matched to a Parish')
        )
        groups.setdefault(deanery, []).append(r)

    columns = ['Name', 'Rank', 'Current Deployment', 'Status']
    sections = []
    for deanery in sorted(groups):
        rows = [[r.name, r.get_rank_display(), r.assignment or '—', r.status] for r in groups[deanery]]
        sections.append({'heading': deanery, 'columns': columns, 'rows': rows})
    return sections


def _build_ordination_documents_report():
    required_types = list(DocumentType.objects.filter(is_required=True))
    records = ClergyRecord.objects.prefetch_related('documents__document_type').order_by('name')
    columns = ['Name', 'Rank', 'Current Deployment', 'Status', 'Documents Submitted', 'Documents Missing']
    rows = []
    for r in records:
        checklist = r.document_checklist(required_types=required_types)
        missing = [c['document_type'].name for c in checklist if c['status'] in ('Missing', 'Expired')]
        submitted_count = len(checklist) - len(missing)
        rows.append([
            r.name, r.get_rank_display(), r.assignment or '—', r.status,
            f"{submitted_count}/{len(checklist)}", ', '.join(missing) or '—',
        ])
    return [{'heading': None, 'columns': columns, 'rows': rows}]


_BUILDERS = {
    'clerical_directory': _build_clerical_directory,
    'status_report': _build_status_report,
    'per_deanery': _build_per_deanery_report,
    'ordination_documents': _build_ordination_documents_report,
}


def build_report(report_type, generated_by=None):
    if report_type not in REPORT_TYPES:
        raise ValueError(f"Unknown report type: {report_type}")
    result = _BUILDERS[report_type]()
    if isinstance(result, dict):
        sections = result.get('sections', [])
        entries = result.get('entries', [])
    else:
        sections = result
        entries = []
    return {
        'type': report_type,
        'title': REPORT_TYPES[report_type],
        'generated_at': timezone.now(),
        'generated_by': (generated_by.get_full_name() or generated_by.username) if generated_by else 'Registrar',
        'sections': sections,
        'entries': entries,
    }


def render_pdf(report):
    """Renders the same registrar/report_pdf.html template used for the
    on-screen preview (see views.preview_generated_report) into a real PDF,
    so the letterhead design is written once in ordinary HTML/CSS instead of
    being built twice — once by hand in a template, once again in a PDF
    library's own drawing API. WeasyPrint needs the GTK/Pango native
    libraries installed on the machine running this (see its install docs);
    that's a one-time environment setup step, not something pip alone
    provides on Windows."""
    from django.template.loader import render_to_string

    from ednp.pdf import html_to_pdf

    html = render_to_string('registrar/report_pdf.html', {'report': report})
    return html_to_pdf(html)
