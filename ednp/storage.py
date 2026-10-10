"""File storage that encrypts uploads as they are saved and decrypts them as they are read
(see ednp/encryption.py). Everything that stores a file does it through this: uploaded
clergy documents, generated reports and backup archives.

With no DATA_ENCRYPTION_KEY it is Django's ordinary FileSystemStorage, untouched. With one,
new files are written encrypted, and a file that is already encrypted is decrypted when it
is opened. A file that is still plaintext is read as it always was until DATA_ENCRYPTION_REQUIRED
is turned on (after `manage.py encrypt_existing_data` has run), when it is refused instead.
"""

import tempfile

from django.core.files import File
from django.core.files.storage import FileSystemStorage

from . import encryption

# Decrypted files stay in memory up to this size and spill to a temporary file beyond it.
_MEMORY_LIMIT = 8 * 1024 * 1024


class _Encrypting:
    """Hands the storage the encrypted form of `source`, a piece at a time."""

    def __init__(self, source):
        self._source = source
        self.name = getattr(source, 'name', None)

    def chunks(self, chunk_size=None):
        return encryption.encrypt_stream(self._source.chunks())


def _decrypted(file, name):
    spool = tempfile.SpooledTemporaryFile(max_size=_MEMORY_LIMIT)
    try:
        for piece in encryption.decrypt_stream(file.read):
            spool.write(piece)
    except BaseException:
        spool.close()
        raise
    spool.seek(0)
    return File(spool, name=name)


class EncryptedFileSystemStorage(FileSystemStorage):
    def _save(self, name, content):
        if encryption.configured():
            content = _Encrypting(content)
        return super()._save(name, content)

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

    def is_encrypted(self, name):
        """Whether the stored file is in the encrypted format (without decrypting it)."""
        with FileSystemStorage._open(self, name, 'rb') as file:
            return encryption.is_encrypted_header(file.read(len(encryption.FILE_MAGIC)))
