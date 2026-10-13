"""The printable credential slips handed to clergy after accounts are made.

Eight slips per A4 sheet (2 across, 4 down) to save paper, each one cut out
and given to the person named on it. The plaintext passwords only ever exist
in the `entries` passed in here; nothing is saved, so the registrar can't pull
the same slips up again later — a lost slip means a reset and a new reprint.
"""

import segno
from django.template.loader import render_to_string

SLIPS_PER_ROW = 2
ROWS_PER_PAGE = 4


def _chunk(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


def _qr_data_uri(url):
    """Only the portal address goes in the QR — never the password — so a photo
    of one slip's QR code reveals nothing that isn't already public."""
    return segno.make(url, error='m').svg_data_uri(scale=4, border=1)


def build_pages(entries):
    """entries -> pages -> rows -> slips, so the template can walk it without
    doing any arithmetic. A short last row/page is padded with None so the
    table keeps its two columns."""
    rows = _chunk(list(entries), SLIPS_PER_ROW)
    for row in rows:
        row.extend([None] * (SLIPS_PER_ROW - len(row)))
    return _chunk(rows, ROWS_PER_PAGE)


def render_slips_pdf(entries, portal_url):
    """entries: dicts with name, username, password, expires_on (a date)."""
    from ednp.pdf import html_to_pdf

    html = render_to_string('registrar/credential_slips.html', {
        'pages': build_pages(entries),
        'portal_url': portal_url,
        'qr': _qr_data_uri(portal_url),
        'count': len(entries),
    })
    return html_to_pdf(html)
