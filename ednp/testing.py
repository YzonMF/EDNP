"""Helpers for the encryption tests."""

import base64
import shutil
import tempfile
import unittest

from django.db import connection
from django.test import override_settings

from . import encryption

try:
    from cryptography.fernet import Fernet
except ImportError:   # the package is only needed once a key is set; its tests are skipped without it
    Fernet = None

needs_crypto = unittest.skipIf(Fernet is None, 'the "cryptography" package is not installed (pip install -r requirements.txt)')


def new_key():
    return Fernet.generate_key().decode()


def encrypted(*keys, required=False):
    """Settings with encryption switched on, under the given key(s) (the first encrypts)."""
    return override_settings(DATA_ENCRYPTION_KEYS=list(keys), DATA_ENCRYPTION_REQUIRED=required)


def plaintext_mode():
    return override_settings(DATA_ENCRYPTION_KEYS=[], DATA_ENCRYPTION_REQUIRED=False)


class MediaFolderMixin:
    """Points MEDIA_ROOT at a folder that is thrown away afterwards."""

    def setUp(self):
        super().setUp()
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)


def old_format(blob):
    """An encrypted file as the first format stored it: each piece's token as base64 text."""
    pieces, position = [encryption.OLD_FILE_MAGIC], len(encryption.FILE_MAGIC)
    while position < len(blob):
        length = int.from_bytes(blob[position:position + 4], 'big')
        token = base64.urlsafe_b64encode(blob[position + 4:position + 4 + length])
        pieces.append(len(token).to_bytes(4, 'big') + token)
        position += 4 + length
    return b''.join(pieces)


def raw_value(model, pk, field_name):
    """What the database really holds for a field, with none of Django's conversion applied."""
    column = model._meta.get_field(field_name).column
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT {column} FROM {model._meta.db_table} WHERE {model._meta.pk.column} = %s', [pk])
        return cursor.fetchone()[0]
