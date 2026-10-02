"""Backup Records: a database dump plus a documents archive.

Kept as two separate operations on purpose — see BackupArchive's docstring
in models.py for why. build_database_dump() is cheap at any record count
and is downloaded fresh every time; build_documents_zip() only covers the
uploaded clergy documents folder and is saved as a BackupArchive so it
doesn't have to be rebuilt (or re-downloaded) on every visit.
"""

import io
import json
import os
import zipfile

from django.conf import settings
from django.core.management import call_command


def build_database_dump():
    """Everything needed to restore clergy/registrar data, minus password
    hashes — this file is downloaded as a plain, unencrypted .json, so it
    shouldn't double as an offline credential-cracking target for every
    account in the system. A restore from this dump still recreates accounts
    with their usernames/flags intact; passwords would need to be reset
    afterward, which is an acceptable trade for not shipping hashes in a
    backup file that could end up in email or a shared drive."""
    buffer = io.StringIO()
    call_command(
        'dumpdata', 'auth.user', 'clergy', 'registrar',
        natural_foreign=True, indent=2, stdout=buffer,
    )
    data = json.loads(buffer.getvalue())
    for entry in data:
        if entry['model'] == 'auth.user':
            entry['fields']['password'] = ''
    return json.dumps(data, indent=2).encode('utf-8')


def build_documents_zip():
    documents_root = os.path.join(settings.MEDIA_ROOT, 'clergy_documents')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(documents_root):
            for filename in files:
                filepath = os.path.join(root, filename)
                arcname = os.path.relpath(filepath, settings.MEDIA_ROOT)
                zf.write(filepath, arcname)
    buffer.seek(0)
    return buffer.getvalue()
