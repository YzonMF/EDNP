"""File storage that encrypts files as they are saved and decrypts them as they are read
(see ednp/encryption.py). Everything that stores a file does it through this: uploaded
clergy documents, generated reports, and the documents archives that were saved on the
server before the archive became a straight download.

Where the files are kept is chosen in settings: a bucket (EncryptedS3Storage in
ednp/bucket_storage.py: a Railway Storage Bucket, or any S3-compatible one), which outlives
redeploys, or MEDIA_ROOT on the server's own disk (EncryptedFileSystemStorage, below). The
encryption is the same either way; it lives in EncryptingStorageMixin.

With no DATA_ENCRYPTION_KEY it is the ordinary storage, untouched. With one, new files are
written encrypted, and a file that is already encrypted is decrypted when it is opened. A file
that is still plaintext is read as it always was until DATA_ENCRYPTION_REQUIRED is turned on
(after `manage.py encrypt_existing_data` has run), when it is refused instead.
"""

import os
import shutil
import tempfile
from contextlib import nullcontext

from django.core.files import File
from django.core.files.storage import FileSystemStorage

from . import encryption

# Decrypted files stay in memory up to this size and spill to a temporary file beyond it.
MEMORY_LIMIT = 8 * 1024 * 1024

SKIPPED_FOLDERS = {'lost+found'}


class _Encrypting:
    """Hands the storage the encrypted form of `source`, a piece at a time."""

    def __init__(self, source):
        self._source = source
        self.name = getattr(source, 'name', None)

    def chunks(self, chunk_size=None):
        return encryption.encrypt_stream(self._source.chunks())


def _decrypted(file, name):
    spool = tempfile.SpooledTemporaryFile(max_size=MEMORY_LIMIT)
    try:
        for piece in encryption.decrypt_stream(file.read):
            spool.write(piece)
    except BaseException:
        spool.close()
        raise
    spool.seek(0)
    return File(spool, name=name)


class EncryptingStorageMixin:
    """The encryption, for any Django storage class; it goes ahead of that class in the bases.
    The class itself supplies stored_names() and put_stored(), the two things that depend on
    where the files are."""

    def _encrypted_form(self, content):
        """What the storage underneath saves in place of `content`, as a context manager."""
        return nullcontext(_Encrypting(content))

    def _save(self, name, content):
        if not encryption.configured():
            return super()._save(name, content)
        with self._encrypted_form(content) as encrypted_content:
            return super()._save(name, encrypted_content)

    def _open(self, name, mode='rb'):
        file = super()._open(name, mode)
        if mode != 'rb':
            return file
        try:
            if encryption.is_encrypted_header(file.read(len(encryption.FILE_MAGIC))):
                file.seek(0)
                try:
                    return _decrypted(file, name)
                finally:
                    file.close()
            file.seek(0)
            if encryption.required():
                raise encryption.EncryptionError(
                    f'{name} is stored unencrypted, which DATA_ENCRYPTION_REQUIRED does not allow.')
            return file
        except BaseException:
            file.close()
            raise

    # --- Files exactly as stored, for the documents archive and encrypt_existing_data ---

    def open_stored(self, name):
        """The stored file as it is, still encrypted if it is. Raises FileNotFoundError if it is gone."""
        return super()._open(name, 'rb')

    def is_encrypted(self, name):
        """Whether the stored file is in the encrypted format (without decrypting it)."""
        with self.open_stored(name) as file:
            return encryption.is_encrypted_header(file.read(len(encryption.FILE_MAGIC)))

    def stored_names(self):
        """Every stored file's name, as a FileField holds it."""
        raise NotImplementedError

    def put_stored(self, name, file):
        """Stores the bytes read from `file` under `name` exactly as they are (nothing is encrypted
        here), replacing any file already there all at once: a failure part way leaves it as it was."""
        raise NotImplementedError


class EncryptedFileSystemStorage(EncryptingStorageMixin, FileSystemStorage):
    def stored_names(self):
        """Skips dotfiles (this module's own temporary files among them) and lost+found."""
        root = self.location
        if not os.path.isdir(root):
            return
        for folder, subfolders, names in os.walk(root):
            subfolders[:] = sorted(name for name in subfolders if name not in SKIPPED_FOLDERS and not name.startswith('.'))
            for name in sorted(names):
                if not name.startswith('.'):
                    yield os.path.relpath(os.path.join(folder, name), root).replace(os.sep, '/')

    def put_stored(self, name, file):
        # Written beside it under a dotfile name, then swapped in.
        path = self.path(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.enc-', suffix='.tmp')
        try:
            with os.fdopen(handle, 'wb') as out:
                shutil.copyfileobj(file, out)
            if os.path.exists(path):
                shutil.copymode(path, temp)
            elif self.file_permissions_mode is not None:
                os.chmod(temp, self.file_permissions_mode)
            os.replace(temp, path)
        except BaseException:
            if os.path.exists(temp):
                os.remove(temp)
            raise
