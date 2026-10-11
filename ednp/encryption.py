"""Encryption at rest for the most sensitive stored data: uploaded documents (and the
reports and archives built from them) and government ID numbers.

What it protects against: a copy of the database, the file volume or a backup falling
into the wrong hands, or someone reading the database with a leaked password. What it
does not: someone who controls the running site, which holds the key.

The key is DATA_ENCRYPTION_KEY, kept in the environment and never in the database or
the repository. It is deliberately not derived from SECRET_KEY, so that changing one
can never make the data unreadable. Without the key the data cannot be recovered, by
anyone, so keep a copy somewhere safe that is not this server.

With no key set nothing here does anything and the site behaves exactly as it did
before encryption existed.

Files are encrypted in 64 KiB pieces, each one authenticated and numbered, so that a
damaged, truncated, reordered or swapped piece is detected, and a large file is never
held in memory whole. Text values are a single piece. Both use Fernet (AES-128-CBC with
an HMAC), and several keys may be configured: the first encrypts, every one of them
decrypts, which is how a key is replaced.

A Fernet token is base64 text, a third bigger than the bytes it carries. Files store each
token's bytes instead (format 2), so an encrypted file is only about 0.1% bigger than the
original. Files written in the first format (base64 tokens) are still read, and
`manage.py encrypt_existing_data` converts them.
"""

import base64
import struct
from functools import lru_cache

from django.conf import settings

FILE_MAGIC = b'EDNPENC2'     # the first bytes of every encrypted file
OLD_FILE_MAGIC = b'EDNPENC1'  # the first format, whose pieces are stored as base64 text
TEXT_PREFIX = 'enc1:'        # the first characters of every encrypted text value
CHUNK_SIZE = 64 * 1024
_MAX_TOKEN = 1024 * 1024     # a piece's token is about 66 KB (90 KB in the old format); anything near this is a damaged file
_FRAME = struct.Struct('>QB')  # piece number, and 1 for the last piece


class EncryptionError(Exception):
    """Stored data could not be encrypted or decrypted. The message never contains
    the key or the data, so it is safe to show or log."""


def keys():
    return tuple(settings.DATA_ENCRYPTION_KEYS)


def configured():
    return bool(keys())


def required():
    """True once everything is meant to be encrypted: plaintext is then refused, not tolerated."""
    return bool(settings.DATA_ENCRYPTION_REQUIRED)


@lru_cache(maxsize=8)
def _fernets(key_tuple):
    # Imported here, not at the top, so the site starts and runs without the library
    # until a key is actually set.
    try:
        from cryptography.fernet import Fernet, MultiFernet
    except ImportError as exc:
        raise EncryptionError('DATA_ENCRYPTION_KEY is set but the "cryptography" package is not installed '
                              '(pip install -r requirements.txt).') from exc
    try:
        singles = [Fernet(key) for key in key_tuple]
    except (ValueError, TypeError) as exc:
        # Only the length is shown, never the key. A real key is 44 characters, so a different number
        # usually says what went wrong (43: the final "=" was left off when copying).
        lengths = ' and '.join(str(len(key)) for key in key_tuple)
        raise EncryptionError(f'DATA_ENCRYPTION_KEY is not a valid key: a key is 44 characters ending with "=", '
                              f'and what is set is {lengths} characters. Check it was copied whole, on one line. '
                              f'Make a new one with: python manage.py generate_data_key') from exc
    return singles, MultiFernet(singles)


def _ciphers():
    if not configured():
        raise EncryptionError('This data is encrypted but DATA_ENCRYPTION_KEY is not set.')
    return _fernets(keys())


def _invalid_token():
    from cryptography.fernet import InvalidToken
    return InvalidToken


# --- Text values ---------------------------------------------------------

def is_encrypted_text(value):
    return isinstance(value, str) and value.startswith(TEXT_PREFIX)


def encrypt_text(plain):
    _, many = _ciphers()
    return TEXT_PREFIX + many.encrypt(plain.encode('utf-8')).decode('ascii')


def decrypt_text(value):
    _, many = _ciphers()
    try:
        return many.decrypt(value[len(TEXT_PREFIX):].encode('ascii')).decode('utf-8')
    except (_invalid_token(), UnicodeError) as exc:
        raise EncryptionError('A stored value could not be decrypted: it is damaged, or it was '
                              'encrypted with a key that is not in DATA_ENCRYPTION_KEY.') from exc


def text_uses_current_key(value):
    """True if `value` was encrypted with the key that encrypts new data (the first one)."""
    singles, _ = _ciphers()
    try:
        singles[0].decrypt(value[len(TEXT_PREFIX):].encode('ascii'))
    except (_invalid_token(), UnicodeError):
        return False
    return True


# --- Files ---------------------------------------------------------------

def is_encrypted_header(head):
    return head[:len(FILE_MAGIC)] in (FILE_MAGIC, OLD_FILE_MAGIC)


def is_old_format(head):
    """True for a file encrypted in the first, bigger format (see the module docstring)."""
    return head[:len(OLD_FILE_MAGIC)] == OLD_FILE_MAGIC


def _rechunk(chunks, size):
    """The same bytes, in pieces of exactly `size` (the last may be shorter)."""
    buffer = bytearray()
    for chunk in chunks:
        buffer += chunk
        while len(buffer) >= size:
            yield bytes(buffer[:size])
            del buffer[:size]
    if buffer:
        yield bytes(buffer)


def _frame(many, number, last, piece):
    token = base64.urlsafe_b64decode(many.encrypt(_FRAME.pack(number, last) + piece))
    return len(token).to_bytes(4, 'big') + token


def encrypt_stream(chunks):
    """Yields the encrypted form of the bytes in `chunks`. Nothing is held but one piece
    (plus the next one, to know which is last)."""
    _, many = _ciphers()
    yield FILE_MAGIC
    number, pending = 0, None
    for piece in _rechunk(chunks, CHUNK_SIZE):
        if pending is not None:
            yield _frame(many, number, 0, pending)
            number += 1
        pending = piece
    yield _frame(many, number, 1, pending or b'')   # an empty file still has one (last) piece


def _read_exact(read, count):
    data = b''
    while len(data) < count:
        part = read(count - len(data))
        if not part:
            raise EncryptionError('An encrypted file is cut short.')
        data += part
    return data


def _tokens(read):
    """Yields each piece's token (as Fernet takes it) from an encrypted file, checking the
    framing as it goes."""
    head = read(len(FILE_MAGIC))
    if not is_encrypted_header(head):
        raise EncryptionError('This file is not in the encrypted format.')
    stored_as_text = is_old_format(head)
    while True:
        length = int.from_bytes(_read_exact(read, 4), 'big')
        if not 0 < length <= _MAX_TOKEN:
            raise EncryptionError('An encrypted file is damaged.')
        token = _read_exact(read, length)
        yield token if stored_as_text else base64.urlsafe_b64encode(token)


def decrypt_stream(read):
    """Yields the original bytes of an encrypted file, given its `read` method. Raises
    EncryptionError for a wrong key and for any piece that is damaged, missing,
    repeated or out of order."""
    _, many = _ciphers()
    invalid = _invalid_token()
    expected = 0
    for token in _tokens(read):
        try:
            frame = many.decrypt(token)
        except invalid as exc:
            raise EncryptionError('A stored file could not be decrypted: it is damaged, or it was '
                                  'encrypted with a key that is not in DATA_ENCRYPTION_KEY.') from exc
        try:
            number, last = _FRAME.unpack(frame[:_FRAME.size])
        except struct.error as exc:
            raise EncryptionError('An encrypted file is damaged.') from exc
        if number != expected or last not in (0, 1):
            raise EncryptionError('An encrypted file is damaged: its pieces are out of order.')
        yield frame[_FRAME.size:]
        if last:
            if read(1):
                raise EncryptionError('An encrypted file is damaged: it has data after its end.')
            return
        expected += 1


def first_token_uses_current_key(read):
    """True if an encrypted file's first piece was encrypted with the current (first) key."""
    singles, _ = _ciphers()
    invalid = _invalid_token()
    try:
        singles[0].decrypt(next(_tokens(read)))
    except invalid:
        return False
    return True
