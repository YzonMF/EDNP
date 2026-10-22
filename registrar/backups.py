"""Backup Records: a database dump, and an archive of the uploaded documents.

Both are built as they are downloaded and go straight to the registrar's computer; neither is
kept on the server. A copy kept beside the originals takes as much storage again, and is lost
in the same accident. build_database_dump() is cheap at any record count; documents_zip()
streams, sending each document on as it is read, so the archive is never held whole in memory
or on disk however many documents there are.
"""

import io
import json
import zipfile

from django.core.management import call_command

from .models import Document, EmberLetter

COPY_CHUNK = 64 * 1024


def build_database_dump():
    """Everything needed to restore clergy/registrar data, minus password
    hashes — this file is downloaded as a plain, unencrypted .json, so it
    shouldn't double as an offline credential-cracking target for every
    account in the system. A restore from this dump still recreates accounts
    with their usernames/flags intact; passwords would need to be reset
    afterward, which is an acceptable trade for not shipping hashes in a
    backup file that could end up in email or a shared drive.

    While DATA_ENCRYPTION_KEY is set, government ID numbers appear in the file in their
    encrypted form (see ednp/fields.py), and loading the file back needs the same key."""
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


class _Outbox:
    """A write-only file that collects what zipfile writes, to be sent on in pieces."""

    def __init__(self):
        self._pieces = []

    def write(self, data):
        self._pieces.append(bytes(data))
        return len(data)

    def flush(self):
        pass

    def take(self):
        data = b''.join(self._pieces)
        self._pieces.clear()
        return data


def documents_zip():
    """Yields a .zip of every uploaded document, each copied exactly as stored, at the same path
    inside the archive. While DATA_ENCRYPTION_KEY is set they are stored encrypted, so they stay
    encrypted inside it; restoring one means putting it back at that path, and needs the same key.
    A document whose file is no longer stored is named in MISSING-FILES.txt instead."""
    outbox, missing = _Outbox(), []
    documents = Document.objects.select_related('record', 'document_type').order_by('record_id', 'document_type__name')
    ember_letters = EmberLetter.objects.select_related('record').order_by('record_id', 'year', 'quarter')
    with zipfile.ZipFile(outbox, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for document in documents.iterator():
            if not document.file or not document.file.name:
                continue
            name = document.file.name
            try:
                stored = document.file.storage.open_stored(name)
            except FileNotFoundError:
                missing.append(f'{name} ({document.record.name}: {document.document_type.name})')
                continue
            with stored, archive.open(name, 'w') as member:
                while piece := stored.read(COPY_CHUNK):
                    member.write(piece)
                    if data := outbox.take():
                        yield data
        for ember in ember_letters.iterator():
            name = ember.file.name
            try:
                stored = ember.file.storage.open_stored(name)
            except FileNotFoundError:
                missing.append(f'{name} ({ember.record.name}: Ember Letter {ember.year} Q{ember.quarter})')
                continue
            with stored, archive.open(name, 'w') as member:
                while piece := stored.read(COPY_CHUNK):
                    member.write(piece)
                    if data := outbox.take():
                        yield data
        if missing:
            archive.writestr('MISSING-FILES.txt', 'These documents are on record but their files are no longer '
                                                  'stored, so they are not in this archive:\n' + '\n'.join(missing) + '\n')
    yield outbox.take()
