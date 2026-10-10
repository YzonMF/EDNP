import hashlib
import os
import shutil
import tempfile
from collections import Counter

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection

from ednp import encryption
from ednp.fields import EncryptedCharField

SKIPPED_FOLDERS = {'lost+found'}


def _plain_chunks(path):
    with open(path, 'rb') as file:
        while True:
            piece = file.read(encryption.CHUNK_SIZE)
            if not piece:
                return
            yield piece


def _decrypted_chunks(path):
    with open(path, 'rb') as file:
        yield from encryption.decrypt_stream(file.read)


class Command(BaseCommand):
    help = ('Encrypts every stored file and government ID number that is still plaintext, using DATA_ENCRYPTION_KEY, '
            'and checks each one reads back correctly before replacing the original. Safe to run again and again, '
            'and while the site is running. Use --dry-run first.')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Change nothing: report what would be encrypted, and check that everything '
                                 'already encrypted can be read with the current key.')
        parser.add_argument('--rotate', action='store_true',
                            help='Also re-encrypt anything still under an older key in DATA_ENCRYPTION_KEY, '
                                 'so that key can be retired.')

    def handle(self, *args, **options):
        if not encryption.configured():
            raise CommandError('DATA_ENCRYPTION_KEY is not set, so there is nothing to encrypt with. '
                               'Make one with: python manage.py generate_data_key')
        try:
            encryption._fernets(encryption.keys())
        except encryption.EncryptionError as exc:
            raise CommandError(str(exc))

        self.dry_run, self.rotate = options['dry_run'], options['rotate']
        if self.dry_run:
            self.stdout.write('Dry run: nothing will be changed.\n')

        failures = self._files() + self._fields()

        self.stdout.write('')
        if failures:
            raise CommandError(f'{len(failures)} item(s) failed and were left as they were:\n  ' + '\n  '.join(failures[:20]))
        if self.dry_run:
            self.stdout.write(self.style.SUCCESS('Dry run finished: everything already encrypted can be read with the current key.'))
        else:
            self.stdout.write(self.style.SUCCESS(
                'Done. Once nothing is left plaintext, set DATA_ENCRYPTION_REQUIRED=True so a plaintext file or value '
                'is refused from then on.'))

    def _report(self, label, tally):
        parts = [f'{count} {state}' for state, count in sorted(tally.items()) if count]
        self.stdout.write(f'{label}: ' + (', '.join(parts) or 'nothing found'))

    # --- Files ---------------------------------------------------------

    def _files(self):
        root = str(settings.MEDIA_ROOT)
        tally, failures = Counter(), []
        if os.path.isdir(root):
            for folder, subfolders, names in os.walk(root):
                subfolders[:] = [name for name in subfolders if name not in SKIPPED_FOLDERS and not name.startswith('.')]
                for name in names:
                    if name.startswith('.'):   # our own temporary files, and dotfiles
                        continue
                    path = os.path.join(folder, name)
                    try:
                        tally[self._file(path)] += 1
                    except (OSError, encryption.EncryptionError) as exc:
                        tally['failed'] += 1
                        failures.append(f'{os.path.relpath(path, root)}: {exc}')
        self._report('Files', tally)
        return failures

    def _file(self, path):
        with open(path, 'rb') as file:
            head = file.read(len(encryption.FILE_MAGIC))
            if encryption.is_encrypted_header(head):
                file.seek(0)
                current = encryption.first_token_uses_current_key(file.read)
            else:
                current = None
        if current is None:
            if self.dry_run:
                return 'would be encrypted'
            self._rewrite(path, _plain_chunks(path))
            return 'encrypted now'

        # Already encrypted. Prove it can be read with the current keys (this is what catches a wrong key).
        for _ in _decrypted_chunks(path):
            pass
        if current or not self.rotate:
            return 'already encrypted' if current else 'encrypted under an older key (use --rotate)'
        if self.dry_run:
            return 'would be re-encrypted'
        self._rewrite(path, _decrypted_chunks(path))
        return 're-encrypted now'

    def _rewrite(self, path, source):
        """Writes the encrypted form beside the original, proves it decrypts to the same bytes,
        and only then swaps it in, so the original is never lost to a failed attempt."""
        handle, temp = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.enc-', suffix='.tmp')
        try:
            before = hashlib.sha256()

            def watched():
                for piece in source:
                    before.update(piece)
                    yield piece

            with os.fdopen(handle, 'wb') as out:
                for piece in encryption.encrypt_stream(watched()):
                    out.write(piece)
            after = hashlib.sha256()
            for piece in _decrypted_chunks(temp):
                after.update(piece)
            if after.digest() != before.digest():
                raise encryption.EncryptionError('the encrypted copy did not read back identically')
            shutil.copymode(path, temp)
            os.replace(temp, path)
        except BaseException:
            if os.path.exists(temp):
                os.remove(temp)
            raise

    # --- Database values -----------------------------------------------

    def _fields(self):
        failures = []
        for model in apps.get_models():
            for field in model._meta.get_fields():
                if isinstance(field, EncryptedCharField):
                    failures += self._field(model, field)
        return failures

    def _field(self, model, field):
        quote = connection.ops.quote_name
        table, pk, column = quote(model._meta.db_table), quote(model._meta.pk.column), quote(field.column)
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT {pk}, {column} FROM {table}')
            rows = cursor.fetchall()

        tally, failures = Counter(), []
        for pk_value, stored in rows:
            if not stored:
                continue
            try:
                outcome = self._value(table, pk, column, field, pk_value, stored)
            except (encryption.EncryptionError, DatabaseError) as exc:
                tally['failed'] += 1
                failures.append(f'{model._meta.label}.{field.name} #{pk_value}: {exc}')
            else:
                tally[outcome] += 1
        self._report(f'{model._meta.label}.{field.name}', tally)
        return failures

    def _value(self, table, pk, column, field, pk_value, stored):
        if encryption.is_encrypted_text(stored):
            plain = encryption.decrypt_text(stored)    # proves it can be read with the current keys
            if encryption.text_uses_current_key(stored):
                return 'already encrypted'
            if not self.rotate:
                return 'encrypted under an older key (use --rotate)'
            outcome = ('would be re-encrypted', 're-encrypted now')
        else:
            plain = stored
            outcome = ('would be encrypted', 'encrypted now')
        if self.dry_run:
            return outcome[0]

        replacement = field.get_prep_value(plain)
        with connection.cursor() as cursor:
            # Only if the row is still as it was read, so a change someone saves meanwhile is never overwritten.
            cursor.execute(f'UPDATE {table} SET {column} = %s WHERE {pk} = %s AND {column} = %s',
                           [replacement, pk_value, stored])
            if cursor.rowcount != 1:
                raise encryption.EncryptionError('it changed while this ran; run the command again')
            cursor.execute(f'SELECT {column} FROM {table} WHERE {pk} = %s', [pk_value])
            now = cursor.fetchone()[0]
        if not encryption.is_encrypted_text(now) or encryption.decrypt_text(now) != plain:
            raise encryption.EncryptionError('it did not read back identically')
        return outcome[1]
