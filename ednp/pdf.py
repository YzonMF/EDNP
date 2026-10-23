"""Rendered HTML -> PDF bytes, with WeasyPrint.

The printable templates (the clergy record, generated reports, login slips) link their
stylesheet with {% static %} like every other page, so the on-screen copy and the PDF
share one file. WeasyPrint renders from a string here, with no web server to ask, so
/static/ URLs are read straight from the static files on disk instead.

Import this inside the function that makes a PDF, not at the top of a module: WeasyPrint
needs the GTK/Pango native libraries (see its install docs), and the rest of the site
should still run on a machine without them."""

import mimetypes
from urllib.parse import unquote, urlsplit

from django.conf import settings
from django.contrib.staticfiles import finders
from weasyprint import HTML
from weasyprint.urls import URLFetcher, URLFetcherResponse

# A link such as /static/x.css resolves against this to file:///static/x.css, which
# StaticFileFetcher maps back to the real file.
BASE_URL = 'file:///'


class StaticFileFetcher(URLFetcher):
    """Serves file:///static/... from the static files and refuses any other file: URL,
    and blocks all outbound network requests (http/https), so a page can't pull in
    arbitrary files from the server's disk or probe external/internal networks."""

    def fetch(self, url, headers=None):
        parts = urlsplit(url)
        if parts.scheme in ('http', 'https', 'ftp'):
            raise ValueError(f'Outbound network requests are disabled: {url}')
        if parts.scheme != 'file':
            raise ValueError(f'Unsupported URL scheme: {url}')
        prefix = urlsplit(settings.STATIC_URL).path
        path = unquote(parts.path)
        found = path.startswith(prefix) and finders.find(path[len(prefix):])
        if not found:
            raise ValueError(f'Not a static file: {url}')
        content_type = mimetypes.guess_type(found)[0] or 'application/octet-stream'
        with open(found, 'rb') as f:
            return URLFetcherResponse(url, f.read(), {'Content-Type': content_type})


def html_to_pdf(html):
    return HTML(string=html, base_url=BASE_URL, url_fetcher=StaticFileFetcher()).write_pdf()
