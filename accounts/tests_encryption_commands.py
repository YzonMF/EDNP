"""The commands that switch encryption on for data already stored: generate_data_key and
encrypt_existing_data. The point of most of these is that nothing is lost: the original is only
replaced by something proven to read back identically, a change someone saves meanwhile is never
overwritten, and a wrong key changes nothing."""

import contextlib
import io
import os
import sys
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import ManagementUtility, call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TestCase

from clergy.models import ClergyProfile
from ednp import encryption
from ednp.storage import EncryptedFileSystemStorage
from ednp.testing import MediaFolderMixin, encrypted, needs_crypto, new_key, old_format, plaintext_mode, raw_value
from registrar.models import ClergyRecord

from .management.commands.encrypt_existing_data import Command

User = get_user_model()
MAGIC = encryption.FILE_MAGIC
NUMBER = '33-1234567-8'

FILES = {
    'clergy_documents/CLG-001/1_baptism.pdf': b'%PDF-1.4 BAPTISM ' * 300,
    'clergy_documents/CLG-001/2_psychiatric.pdf': b'%PDF-1.4 PSYCHIATRIC ' * 500,
    'generated_reports/status_report.pdf': b'%PDF-1.4 REPORT ' * 100,
    'backups/documents_archive.zip': b'PK\x03\x04 ARCHIVE ' * 100,
    'clergy_documents/CLG-002/empty.pdf': b'',
}


class DataCase(MediaFolderMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.key = new_key()
        self.record = ClergyRecord.objects.create(id='CLG-001', name='Fr. Juan')
        self.user = User.objects.create_user('CLG-001', password='pw12345!x')
        with plaintext_mode():   # as it was stored before encryption existed
            self.profile = ClergyProfile.objects.create(
                user=self.user, record=self.record, sss_number=NUMBER, pagibig_number='1234-5678-9012')
        for name, content in FILES.items():
            self.write(name, content)

    def path(self, name):
        return os.path.join(self.media, *name.split('/'))

    def write(self, name, content):
        path = self.path(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as file:
            file.write(content)

    def raw(self, name):
        with open(self.path(name), 'rb') as file:
            return file.read()

    def stored(self, field='sss_number'):
        return raw_value(ClergyProfile, self.profile.pk, field)

    def run_command(self, *args):
        out = io.StringIO()
        call_command('encrypt_existing_data', *args, stdout=out)
        return out.getvalue()

    def leftovers(self):
        return [name for _, _, names in os.walk(self.media) for name in names if name.startswith('.enc-')]


@needs_crypto
class EncryptExistingDataTests(DataCase):
    def test_it_needs_a_key(self):
        with plaintext_mode():
            with self.assertRaisesMessage(CommandError, 'DATA_ENCRYPTION_KEY is not set'):
                self.run_command()

    def test_a_key_that_is_not_a_key_is_reported(self):
        with encrypted('this-is-not-a-key'):
            with self.assertRaisesMessage(CommandError, 'generate_data_key'):
                self.run_command()

    def test_a_dry_run_changes_nothing_and_says_what_it_would_do(self):
        with encrypted(self.key):
            report = self.run_command('--dry-run')
            self.assertIn('would be encrypted', report)
            self.assertNotIn('encrypted now', report)
            for name, content in FILES.items():
                self.assertEqual(self.raw(name), content)
            self.assertEqual(self.stored(), NUMBER)

    def test_it_encrypts_every_file_and_every_id_number_and_all_of_it_still_reads_back(self):
        with encrypted(self.key):
            report = self.run_command()
            self.assertIn('5 encrypted now', report)       # every file, including the empty one
            storage = EncryptedFileSystemStorage()
            for name, content in FILES.items():
                self.assertTrue(self.raw(name).startswith(MAGIC), name)
                with storage.open(name) as file:
                    self.assertEqual(file.read(), content, name)
            for marker in (b'BAPTISM', b'PSYCHIATRIC', b'REPORT', b'ARCHIVE'):
                self.assertFalse(any(marker in self.raw(name) for name in FILES), marker)
            self.assertTrue(self.stored().startswith('enc1:'))
            self.assertTrue(self.stored('pagibig_number').startswith('enc1:'))
            self.assertEqual(self.stored('philhealth_number'), '')   # blank stays blank
            profile = ClergyProfile.objects.get(pk=self.profile.pk)
            self.assertEqual((profile.sss_number, profile.pagibig_number), (NUMBER, '1234-5678-9012'))
            self.assertEqual(self.leftovers(), [])

    def test_running_it_again_changes_nothing(self):
        with encrypted(self.key):
            self.run_command()
            before = {name: self.raw(name) for name in FILES}, self.stored()
            report = self.run_command()
            self.assertIn('already encrypted', report)
            self.assertNotIn('encrypted now', report)
            self.assertEqual(({name: self.raw(name) for name in FILES}, self.stored()), before)

    def test_it_leaves_dotfiles_and_lost_and_found_alone(self):
        self.write('.hidden', b'keep me plain')
        self.write('lost+found/x', b'keep me plain')
        with encrypted(self.key):
            self.run_command()
        self.assertEqual(self.raw('.hidden'), b'keep me plain')
        self.assertEqual(self.raw('lost+found/x'), b'keep me plain')

    def test_a_file_that_does_not_read_back_identically_is_never_replaced(self):
        with encrypted(self.key):
            with mock.patch.object(encryption, 'decrypt_stream', lambda read: iter([b'not the same'])):
                with self.assertRaisesMessage(CommandError, 'did not read back identically'):
                    self.run_command()
            for name, content in FILES.items():
                self.assertEqual(self.raw(name), content, name)   # every original intact
            self.assertEqual(self.leftovers(), [])               # and no half-written copy left behind

    def test_a_value_someone_saved_in_the_meantime_is_never_overwritten(self):
        quote = connection.ops.quote_name
        table, pk, column = quote('clergy_profiles'), quote('id'), quote('sss_number')
        field = ClergyProfile._meta.get_field('sss_number')
        command = Command()
        command.dry_run, command.rotate = False, False
        with encrypted(self.key):
            with self.assertRaisesMessage(encryption.EncryptionError, 'changed while this ran'):
                # the command read 'OLD'; by the time it writes, the row holds the plaintext NUMBER
                command._value(table, pk, column, field, self.profile.pk, 'OLD')
            self.assertEqual(self.stored(), NUMBER)

    def test_the_wrong_key_is_reported_and_changes_nothing(self):
        with encrypted(self.key):
            self.run_command()
        snapshot = {name: self.raw(name) for name in FILES}, self.stored()
        with encrypted(new_key()):
            for args in (('--dry-run',), ()):
                with self.assertRaises(CommandError) as raised:
                    self.run_command(*args)
                self.assertIn('could not be decrypted', str(raised.exception))
        self.assertEqual(({name: self.raw(name) for name in FILES}, self.stored()), snapshot)

    def test_a_key_can_be_replaced_and_the_old_one_retired(self):
        old, new = new_key(), new_key()
        with encrypted(old):
            self.run_command()
        with encrypted(new, old):
            self.assertIn('older key (use --rotate)', self.run_command())          # reads fine, not yet moved
            self.assertIn('would be re-encrypted', self.run_command('--rotate', '--dry-run'))
            report = self.run_command('--rotate')
            self.assertIn('re-encrypted now', report)
            self.assertNotIn('older key', self.run_command())
        with encrypted(new):   # the old key is gone and everything still reads
            storage = EncryptedFileSystemStorage()
            for name, content in FILES.items():
                with storage.open(name) as file:
                    self.assertEqual(file.read(), content, name)
            self.assertEqual(ClergyProfile.objects.get(pk=self.profile.pk).sss_number, NUMBER)
            self.assertIn('already encrypted', self.run_command('--rotate'))

    def test_files_in_the_first_bigger_format_are_rewritten_in_the_smaller_one(self):
        with encrypted(self.key):
            self.run_command()
            for name in FILES:
                self.write(name, old_format(self.raw(name)))
            before = sum(len(self.raw(name)) for name in FILES)

            self.assertIn('5 would be rewritten in the smaller format', self.run_command('--dry-run'))
            self.assertTrue(all(self.raw(name).startswith(encryption.OLD_FILE_MAGIC) for name in FILES))

            self.assertIn('5 rewritten in the smaller format now', self.run_command())
            self.assertLess(sum(len(self.raw(name)) for name in FILES), before)
            storage = EncryptedFileSystemStorage()
            for name, content in FILES.items():
                self.assertTrue(self.raw(name).startswith(MAGIC), name)
                with storage.open(name) as file:
                    self.assertEqual(file.read(), content, name)
            self.assertIn('5 already encrypted', self.run_command())
            self.assertEqual(self.leftovers(), [])

    def test_it_copes_with_a_mixture_of_encrypted_and_plain(self):
        with encrypted(self.key):
            EncryptedFileSystemStorage().save('clergy_documents/CLG-003/new.pdf', ContentFile(b'%PDF-1.4 new'))
            report = self.run_command()
            self.assertIn('1 already encrypted', report)
            self.assertIn('5 encrypted now', report)


class GenerateDataKeyTests(SimpleTestCase):
    @needs_crypto
    def test_it_prints_a_working_key_and_warns_about_losing_it(self):
        from cryptography.fernet import Fernet
        out, err = io.StringIO(), io.StringIO()
        call_command('generate_data_key', stdout=out, stderr=err)
        key = out.getvalue().strip()
        Fernet(key)   # a valid key, and nothing else was printed with it
        self.assertEqual(len(out.getvalue().strip().splitlines()), 1)
        self.assertIn('can NEVER be recovered', err.getvalue())
        again = io.StringIO()
        call_command('generate_data_key', stdout=again, stderr=io.StringIO())
        self.assertNotEqual(key, again.getvalue().strip())

    @needs_crypto
    def test_it_still_works_when_the_key_in_the_settings_is_broken(self):
        # While the key is broken the startup check stops every other command, so this one is the way out of
        # that: it must not be stopped by the very check it exists to fix. (Run the way manage.py runs it,
        # checks included; call_command skips them.)
        from cryptography.fernet import Fernet
        out = io.StringIO()
        with encrypted('this-is-not-a-key'), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            ManagementUtility(['manage.py', 'generate_data_key']).execute()
        Fernet(out.getvalue().strip())

    def test_it_says_what_to_do_if_the_library_is_missing(self):
        with mock.patch.dict(sys.modules, {'cryptography': None, 'cryptography.fernet': None}):
            with self.assertRaisesMessage(CommandError, 'pip install -r requirements.txt'):
                call_command('generate_data_key', stdout=io.StringIO(), stderr=io.StringIO())
