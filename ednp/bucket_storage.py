"""Stored files in a bucket: a Railway Storage Bucket, or any other S3-compatible one (Cloudflare
R2, Backblaze B2, AWS S3). Unlike the server's own disk, a bucket outlives redeploys. Settings
choose this when BUCKET_NAME is set, and it takes its connection details from BUCKET_OPTIONS;
the setup is in resources/storage-bucket.md.

Files are encrypted exactly as they are on disk (EncryptingStorageMixin). The bucket is private
and the site never hands out links into it: every file is still served by the views that check
who may see it. Kept apart from ednp/storage.py so that a site without a bucket never imports
the bucket libraries.
"""

import tempfile
from contextlib import contextmanager

from botocore.config import Config
from django.conf import settings
from django.core.files import File
from storages.backends.s3 import S3Storage
from storages.utils import clean_name

from . import encryption
from .storage import MEMORY_LIMIT, EncryptingStorageMixin


class EncryptedS3Storage(EncryptingStorageMixin, S3Storage):
    def __init__(self, **options):
        options = {**settings.BUCKET_OPTIONS, **options}
        # Never overwrite. save_document deletes the replaced file by name once the new one is
        # in, so a re-upload under the same file name has to be saved under a new name.
        options['file_overwrite'] = False
        # A file read from the bucket is held in memory up to this size, then in a temporary file.
        options['max_memory_size'] = MEMORY_LIMIT
        options['client_config'] = Config(
            s3={'addressing_style': options.pop('addressing_style', None) or 'virtual'},
            signature_version='s3v4',
            # Checksums only where S3 itself requires them: S3-compatible services don't all
            # accept the newer ones the AWS SDK otherwise adds to every upload.
            request_checksum_calculation='when_required',
            response_checksum_validation='when_required',
        )
        super().__init__(**options)

    @contextmanager
    def _encrypted_form(self, content):
        """An upload to a bucket reads a file rather than taking pieces, so the encrypted form is
        written to a temporary file first (in memory while it is small)."""
        with tempfile.SpooledTemporaryFile(max_size=MEMORY_LIMIT) as spool:
            for piece in encryption.encrypt_stream(content.chunks()):
                spool.write(piece)
            spool.seek(0)
            yield File(spool, name=getattr(content, 'name', None))

    def stored_names(self):
        prefix = f'{self.location}/' if self.location else ''
        paginator = self.connection.meta.client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix):
            for entry in page.get('Contents', ()):
                yield entry['Key'][len(prefix):]

    def put_stored(self, name, file):
        # An upload replaces the object whole, only once it has all arrived.
        key = self._normalize_name(clean_name(name))
        self.bucket.Object(key).upload_fileobj(file, Config=self.transfer_config)
