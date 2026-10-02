"""Report data-building and PDF rendering for the registrar's Generate Report page.

Every report is normalized into the same shape — a title plus a list of
sections, each with its own columns/rows — so the HTML view and the PDF
export can walk the same structure instead of having per-report templates.
"""

from django.utils import timezone
from django.utils.html import escape
from django.utils.safestring import mark_safe

from .models import ClergyRecord, Parish

REPORT_TYPES = {
    'clerical_directory': 'Official Clergy List',
    'status_report': 'Status Report',
    'per_deanery': 'Per-Deanery Report',
    'ordination_documents': 'Ordination Documents Report (Registrar)',
}


def _assignment_to_deanery_map():
    """Clergy assignment is a free-text label ('St. Peter - Alab'); this maps
    that same label, as generated for the parish dropdown, back to its
    deanery so per-deanery grouping doesn't need a direct FK on the record."""
    mapping = {}
    for parish in Parish.objects.select_related('deanery'):
        label = f"{parish.parish_name} - {parish.place_name}" if parish.place_name else parish.parish_name
        mapping[label] = parish.deanery.name
    return mapping


def _fmt_date(value):
    return value.strftime('%b %d, %Y') if value else '—'


def _build_clerical_directory():
    records = ClergyRecord.objects.all().order_by('name')
    columns = ['No.', 'Name', 'Deployment / Assignment', 'Date of Ordination', 'Status']
    rows = []
    for i, r in enumerate(records, start=1):
        # The only cell that's intentionally HTML — everything else in every
        # report stays plain text and relies on the template's normal
        # auto-escaping (no blanket `|safe` on the table cells anymore).
        name_cell = mark_safe(f'<b>{escape(r.name)}</b><span class="record-id">{escape(r.id)}</span>')
        rows.append([str(i), name_cell, r.assignment or '—', _fmt_date(r.ordination_date), r.status])
    return [{'heading': None, 'columns': columns, 'rows': rows}]


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
    records = ClergyRecord.objects.all().order_by('name')
    groups = {}
    for r in records:
        deanery = mapping.get(r.assignment, 'Unassigned / Not Matched to a Parish')
        groups.setdefault(deanery, []).append(r)

    columns = ['Name', 'Rank', 'Current Deployment', 'Status']
    sections = []
    for deanery in sorted(groups):
        rows = [[r.name, r.get_rank_display(), r.assignment or '—', r.status] for r in groups[deanery]]
        sections.append({'heading': deanery, 'columns': columns, 'rows': rows})
    return sections


def _build_ordination_documents_report():
    records = ClergyRecord.objects.all().order_by('name')
    columns = ['Name', 'Rank', 'Current Deployment', 'Status', 'Documents Submitted', 'Documents Missing']
    rows = []
    for r in records:
        checklist = r.document_checklist()
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
    return {
        'type': report_type,
        'title': REPORT_TYPES[report_type],
        'generated_at': timezone.now(),
        'generated_by': (generated_by.get_full_name() or generated_by.username) if generated_by else 'Registrar',
        'sections': _BUILDERS[report_type](),
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
    from weasyprint import HTML

    html = render_to_string('registrar/report_pdf.html', {'report': report})
    return HTML(string=html).write_pdf()
