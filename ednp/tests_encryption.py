"""Encryption at rest: the primitives, the file storage, the encrypted field, and the startup
checks. The point of most of these is what happens when something is wrong: a damaged file, a
wrong key, a missing key, a plaintext leftover."""

import hashlib
import io
import os
import sys
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import serializers
from django.core.exceptions import FieldError
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile, TemporaryUploadedFile
from django.core.management import call_command
from django.core.management.base import SystemCheckError
from django.http import FileResponse
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.testing import activate
from clergy.forms import ClergyProfileForm
from clergy.models import ClergyProfile
from registrar.backups import build_database_dump
from registrar.models import BackupArchive, ClergyRecord, Document, GeneratedReport

from . import checks, encryption
from .storage import EncryptedFileSystemStorage
from .testing import MediaFolderMixin, encrypted, needs_crypto, new_key, old_format, plaintext_mode, raw_value

User = get_user_model()
CHUNK = encryption.CHUNK_SIZE
MAGIC = encryption.FILE_MAGIC
NUMBER = '33-1234567-8'


def chunked(data, size=7919):
    for start in range(0, len(data), size):
        yield data[start:start + size]


def encrypt_all(data, size=7919):
    return b''.join(encryption.encrypt_stream(chunked(data, size)))


def decrypt_all(blob):
    return b''.join(encryption.decrypt_stream(io.BytesIO(blob).read))


def frames_of(blob):
    """The encrypted file split into its pieces (each is a 4-byte length and a token)."""
    pieces, position = [], len(MAGIC)
    while position < len(blob):
        length = int.from_bytes(blob[position:position + 4], 'big')
        pieces.append(blob[position:position + 4 + length])
        position += 4 + length
    return pieces


@needs_crypto
class TextTests(SimpleTestCase):
    def setUp(self):
        self.key = new_key()

    def test_it_round_trips_and_the_stored_form_hides_the_value(self):
        with encrypted(self.key):
            stored = encryption.encrypt_text(NUMBER)
            self.assertTrue(stored.startswith('enc1:'))
            self.assertNotIn('1234567', stored)
            self.assertEqual(encryption.decrypt_text(stored), NUMBER)

    def test_the_same_value_is_stored_differently_each_time(self):
        with encrypted(self.key):
            self.assertNotEqual(encryption.encrypt_text(NUMBER), encryption.encrypt_text(NUMBER))

    def test_non_ascii_text_survives(self):
        with encrypted(self.key):
            self.assertEqual(encryption.decrypt_text(encryption.encrypt_text('Ñandú ☺ 日本')), 'Ñandú ☺ 日本')

    def test_a_different_key_cannot_read_it(self):
        with encrypted(self.key):
            stored = encryption.encrypt_text(NUMBER)
        with encrypted(new_key()):
            with self.assertRaisesMessage(encryption.EncryptionError, 'could not be decrypted'):
                encryption.decrypt_text(stored)

    def test_a_damaged_value_is_refused(self):
        with encrypted(self.key):
            stored = encryption.encrypt_text(NUMBER)
            for damaged in (stored[:-3] + 'AAA', 'enc1:not a token', 'enc1:é'):
                with self.assertRaises(encryption.EncryptionError):
                    encryption.decrypt_text(damaged)

    def test_with_no_key_nothing_can_be_encrypted_or_read(self):
        with plaintext_mode():
            with self.assertRaisesMessage(encryption.EncryptionError, 'not set'):
                encryption.encrypt_text(NUMBER)
            with self.assertRaisesMessage(encryption.EncryptionError, 'not set'):
                encryption.decrypt_text('enc1:abc')

    def test_a_key_that_is_not_a_key_is_reported_clearly(self):
        with encrypted('this-is-not-a-key'):
            with self.assertRaisesMessage(encryption.EncryptionError, 'generate_data_key'):
                encryption.encrypt_text(NUMBER)

    def test_the_message_says_how_long_the_broken_key_is_but_never_what_it_is(self):
        good = new_key()
        # The two ways a copy usually goes wrong: the final "=" left off, and something in front of the key.
        # (Fernet itself shrugs off stray characters after the "=", so those are not errors.)
        for broken, length in ((good[:-1], 43), ('x' + good, 45)):
            with encrypted(broken):
                with self.assertRaises(encryption.EncryptionError) as raised:
                    encryption.encrypt_text(NUMBER)
            message = str(raised.exception)
            self.assertIn(f'{length} characters', message)
            self.assertNotIn(broken[:12], message)

    def test_a_key_can_be_replaced_without_losing_anything(self):
        old, new = new_key(), new_key()
        with encrypted(old):
            stored = encryption.encrypt_text(NUMBER)
        with encrypted(new, old):                       # the new key first, the old one kept to read with
            self.assertEqual(encryption.decrypt_text(stored), NUMBER)
            self.assertFalse(encryption.text_uses_current_key(stored))
            fresh = encryption.encrypt_text(NUMBER)
            self.assertTrue(encryption.text_uses_current_key(fresh))
        with encrypted(new):                            # the old key retired
            self.assertEqual(encryption.decrypt_text(fresh), NUMBER)
            with self.assertRaises(encryption.EncryptionError):
                encryption.decrypt_text(stored)


@needs_crypto
class StreamTests(SimpleTestCase):
    def setUp(self):
        self.key_override = encrypted(new_key())
        self.key_override.enable()
        self.addCleanup(self.key_override.disable)

    def test_it_round_trips_at_every_awkward_size(self):
        for size in (0, 1, 100, CHUNK - 1, CHUNK, CHUNK + 1, 3 * CHUNK, 3 * CHUNK + 17):
            data = os.urandom(size)
            self.assertEqual(decrypt_all(encrypt_all(data)), data, size)

    def test_the_stored_form_hides_the_content(self):
        blob = encrypt_all(b'PSYCHIATRIC-EVALUATION ' * 5000)
        self.assertTrue(blob.startswith(MAGIC))
        self.assertNotIn(b'PSYCHIATRIC', blob)

    def test_the_same_file_is_stored_differently_each_time(self):
        self.assertNotEqual(encrypt_all(b'same'), encrypt_all(b'same'))

    def test_an_encrypted_file_is_barely_bigger_than_the_original(self):
        # Each piece's token is stored as bytes, not as base64 text (which made files a third bigger).
        data = os.urandom(1024 * 1024)
        self.assertLess(len(encrypt_all(data)), len(data) * 1.005)

    def test_a_file_in_the_first_format_still_reads(self):
        data = os.urandom(2 * CHUNK + 9)
        blob = old_format(encrypt_all(data))
        self.assertTrue(blob.startswith(encryption.OLD_FILE_MAGIC))
        self.assertGreater(len(blob), len(data) * 1.3)
        self.assertTrue(encryption.is_encrypted_header(blob) and encryption.is_old_format(blob))
        self.assertEqual(decrypt_all(blob), data)
        self.assertTrue(encryption.first_token_uses_current_key(io.BytesIO(blob).read))

    def test_a_damaged_piece_in_the_first_format_is_detected_too(self):
        blob = bytearray(old_format(encrypt_all(os.urandom(CHUNK + 5))))
        blob[len(MAGIC) + 4 + 50] ^= 0x01
        with self.assertRaises(encryption.EncryptionError):
            decrypt_all(bytes(blob))

    def test_how_the_source_is_chunked_does_not_matter(self):
        data = os.urandom(2 * CHUNK + 5)
        self.assertEqual(decrypt_all(encrypt_all(data, size=1000)), decrypt_all(encrypt_all(data, size=3 * CHUNK)))

    def test_it_streams_instead_of_reading_everything_first(self):
        consumed = []

        def source():
            for number in range(100):
                consumed.append(number)
                yield b'x' * CHUNK

        output = encryption.encrypt_stream(source())
        next(output)   # the header
        next(output)   # the first piece
        self.assertLessEqual(len(consumed), 3)

    def test_a_damaged_piece_is_detected_wherever_it_is(self):
        blob = bytearray(encrypt_all(os.urandom(3 * CHUNK)))
        start = len(MAGIC)
        for piece in frames_of(bytes(blob)):
            damaged = bytearray(blob)
            damaged[start + 4 + (len(piece) - 4) // 2] ^= 0x01   # a byte in the middle of this piece's token
            with self.assertRaises(encryption.EncryptionError):
                decrypt_all(bytes(damaged))
            start += len(piece)

    def test_a_file_that_is_cut_short_is_detected(self):
        blob = encrypt_all(os.urandom(3 * CHUNK))
        pieces = frames_of(blob)
        for cut in (MAGIC + b''.join(pieces[:-1]),        # the last piece missing, cleanly
                    blob[:-10],                           # cut inside the last piece
                    MAGIC + pieces[0][:2],                # cut inside a length
                    MAGIC[:4]):                           # cut inside the header
            with self.assertRaises(encryption.EncryptionError):
                decrypt_all(cut)

    def test_pieces_cannot_be_reordered_repeated_or_dropped(self):
        pieces = frames_of(encrypt_all(os.urandom(3 * CHUNK + 1)))   # three full pieces and one byte
        self.assertEqual(len(pieces), 4)
        for arrangement in ([1, 0, 2, 3], [0, 0, 1, 2, 3], [0, 2, 3], [0, 1, 1, 3]):
            with self.assertRaises(encryption.EncryptionError):
                decrypt_all(MAGIC + b''.join(pieces[i] for i in arrangement))

    def test_nothing_may_follow_the_last_piece(self):
        with self.assertRaisesMessage(encryption.EncryptionError, 'after its end'):
            decrypt_all(encrypt_all(b'data') + b'x')

    def test_an_absurd_length_is_refused_without_trying_to_read_it(self):
        with self.assertRaisesMessage(encryption.EncryptionError, 'damaged'):
            decrypt_all(MAGIC + b'\xff\xff\xff\xff' + b'x' * 10)

    def test_a_file_that_is_not_encrypted_is_refused(self):
        with self.assertRaisesMessage(encryption.EncryptionError, 'not in the encrypted format'):
            decrypt_all(b'%PDF-1.4 plain')

    def test_the_wrong_key_cannot_read_it(self):
        blob = encrypt_all(b'secret')
        with encrypted(new_key()):
            with self.assertRaisesMessage(encryption.EncryptionError, 'could not be decrypted'):
                decrypt_all(blob)

    def test_a_key_can_be_replaced_without_losing_files(self):
        old, new = new_key(), new_key()
        data = os.urandom(2 * CHUNK + 3)
        with encrypted(old):
            blob = encrypt_all(data)
        with encrypted(new, old):
            self.assertEqual(decrypt_all(blob), data)
            self.assertFalse(encryption.first_token_uses_current_key(io.BytesIO(blob).read))
            rotated = b''.join(encryption.encrypt_stream(encryption.decrypt_stream(io.BytesIO(blob).read)))
            self.assertTrue(encryption.first_token_uses_current_key(io.BytesIO(rotated).read))
        with encrypted(new):   # the old key can now be retired
            self.assertEqual(decrypt_all(rotated), data)


@needs_crypto
class StorageTests(MediaFolderMixin, SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.storage = EncryptedFileSystemStorage()
        self.key = new_key()

    def raw(self, name):
        with open(self.storage.path(name), 'rb') as file:
            return file.read()

    def test_every_file_field_in_the_project_uses_it(self):
        self.assertIsInstance(storages['default'], EncryptedFileSystemStorage)
        for model in (Document, GeneratedReport, BackupArchive):
            self.assertIsInstance(model._meta.get_field('file').storage, EncryptedFileSystemStorage)

    def test_a_saved_file_is_stored_encrypted_and_reads_back_whole(self):
        plain = b'%PDF-1.4 PSYCHIATRIC-REPORT ' * 5000
        with encrypted(self.key):
            name = self.storage.save('clergy_documents/CLG-001/1_report.pdf', ContentFile(plain))
            raw = self.raw(name)
            self.assertTrue(raw.startswith(MAGIC))
            self.assertNotIn(b'PSYCHIATRIC-REPORT', raw)
            self.assertTrue(self.storage.is_encrypted(name))
            with self.storage.open(name) as file:
                self.assertEqual(file.read(), plain)

    def test_an_uploaded_file_object_is_encrypted_too(self):
        with encrypted(self.key):
            name = self.storage.save('clergy_documents/a.pdf', SimpleUploadedFile('a.pdf', b'%PDF-1.4 hello'))
            self.assertTrue(self.raw(name).startswith(MAGIC))
            self.assertEqual(self.storage.open(name).read(), b'%PDF-1.4 hello')

    def test_a_large_upload_that_arrives_as_a_temporary_file_is_encrypted_not_just_moved(self):
        plain = os.urandom(300_000)
        upload = TemporaryUploadedFile('big.pdf', 'application/pdf', len(plain), 'utf-8')
        self.addCleanup(upload.close)
        upload.write(plain)
        upload.flush()
        with encrypted(self.key):
            name = self.storage.save('clergy_documents/big.pdf', upload)
            self.assertTrue(self.raw(name).startswith(MAGIC))
            self.assertEqual(self.storage.open(name).read(), plain)

    def test_with_no_key_files_are_stored_exactly_as_before(self):
        with plaintext_mode():
            name = self.storage.save('clergy_documents/a.pdf', ContentFile(b'%PDF-1.4 plain'))
            self.assertEqual(self.raw(name), b'%PDF-1.4 plain')
            self.assertFalse(self.storage.is_encrypted(name))
            self.assertEqual(self.storage.open(name).read(), b'%PDF-1.4 plain')

    def test_a_plaintext_file_from_before_is_still_read_until_encryption_is_required(self):
        with plaintext_mode():
            name = self.storage.save('clergy_documents/old.pdf', ContentFile(b'%PDF-1.4 old'))
        with encrypted(self.key):
            self.assertEqual(self.storage.open(name).read(), b'%PDF-1.4 old')
        with encrypted(self.key, required=True):
            with self.assertRaisesMessage(encryption.EncryptionError, 'DATA_ENCRYPTION_REQUIRED'):
                self.storage.open(name)
        os.remove(self.storage.path(name))   # no handle was left open (Windows would refuse otherwise)

    def test_an_encrypted_file_with_no_key_is_an_error_not_garbage(self):
        with encrypted(self.key):
            name = self.storage.save('clergy_documents/a.pdf', ContentFile(b'%PDF-1.4 secret'))
        with plaintext_mode():
            with self.assertRaisesMessage(encryption.EncryptionError, 'not set'):
                self.storage.open(name)
        os.remove(self.storage.path(name))

    def test_the_wrong_key_is_an_error_and_leaves_no_handle_open(self):
        with encrypted(self.key):
            name = self.storage.save('clergy_documents/a.pdf', ContentFile(b'%PDF-1.4 secret'))
        with encrypted(new_key()):
            with self.assertRaises(encryption.EncryptionError):
                self.storage.open(name)
        os.remove(self.storage.path(name))

    def test_a_large_file_round_trips_through_a_temporary_file(self):
        plain = os.urandom(9 * 1024 * 1024)   # more than is kept in memory
        with encrypted(self.key):
            name = self.storage.save('clergy_documents/large.pdf', ContentFile(plain))
            with self.storage.open(name) as file:
                self.assertEqual(hashlib.sha256(file.read()).digest(), hashlib.sha256(plain).digest())

    def test_a_download_response_knows_the_real_length_and_can_be_streamed(self):
        plain = b'%PDF-1.4 ' + os.urandom(100_000)
        with encrypted(self.key):
            name = self.storage.save('clergy_documents/a.pdf', ContentFile(plain))
            response = FileResponse(self.storage.open(name), filename='a.pdf')
            self.assertEqual(response['Content-Length'], str(len(plain)))
            self.assertEqual(response['Content-Type'], 'application/pdf')
            self.assertEqual(b''.join(response.streaming_content), plain)

    def test_a_missing_file_still_raises_the_usual_error(self):
        with encrypted(self.key):
            with self.assertRaises(FileNotFoundError):
                self.storage.open('clergy_documents/nope.pdf')
            with self.assertRaises(FileNotFoundError):
                self.storage.open_stored('clergy_documents/nope.pdf')

    def test_a_file_in_the_first_format_opens_as_before(self):
        plain = b'%PDF-1.4 ' + os.urandom(100_000)
        with encrypted(self.key):
            name = self.storage.save('clergy_documents/a.pdf', ContentFile(plain))
            first_format = old_format(self.raw(name))
            with open(self.storage.path(name), 'wb') as file:
                file.write(first_format)
            self.assertTrue(self.storage.is_encrypted(name))
            self.assertEqual(self.storage.open(name).read(), plain)

    def test_open_stored_gives_the_file_still_encrypted(self):
        with encrypted(self.key):
            name = self.storage.save('clergy_documents/a.pdf', ContentFile(b'%PDF-1.4 secret'))
            with self.storage.open_stored(name) as file:
                self.assertEqual(file.read(), self.raw(name))

    def test_stored_names_lists_every_file_but_dotfiles_and_lost_and_found(self):
        for name in ('clergy_documents/CLG-001/1_a.pdf', 'generated_reports/r.pdf', '.hidden', 'lost+found/x',
                     'clergy_documents/.enc-123.tmp'):
            os.makedirs(os.path.dirname(os.path.join(self.media, name)) or self.media, exist_ok=True)
            with open(os.path.join(self.media, name), 'wb') as file:
                file.write(b'x')
        self.assertEqual(sorted(self.storage.stored_names()),
                         ['clergy_documents/CLG-001/1_a.pdf', 'generated_reports/r.pdf'])

    def test_with_no_folder_yet_there_is_nothing_stored(self):
        os.rmdir(self.media)
        self.assertEqual(list(self.storage.stored_names()), [])

    def test_put_stored_writes_the_bytes_exactly_new_or_in_place_and_leaves_nothing_behind(self):
        with encrypted(self.key):   # put_stored never encrypts: it is given what is to be stored
            self.storage.put_stored('clergy_documents/CLG-001/new.pdf', io.BytesIO(b'as given'))
            self.assertEqual(self.raw('clergy_documents/CLG-001/new.pdf'), b'as given')
            self.storage.put_stored('clergy_documents/CLG-001/new.pdf', io.BytesIO(b'replaced'))
            self.assertEqual(self.raw('clergy_documents/CLG-001/new.pdf'), b'replaced')
        self.assertEqual(os.listdir(os.path.join(self.media, 'clergy_documents', 'CLG-001')), ['new.pdf'])

    def test_a_failed_put_stored_leaves_the_original(self):
        with plaintext_mode():
            name = self.storage.save('clergy_documents/a.pdf', ContentFile(b'original'))

        class Broken(io.BytesIO):
            def read(self, *args):
                raise OSError('disk full')

        with self.assertRaises(OSError):
            self.storage.put_stored(name, Broken())
        self.assertEqual(self.raw(name), b'original')
        self.assertEqual(os.listdir(os.path.join(self.media, 'clergy_documents')), ['a.pdf'])


@needs_crypto
class FieldTests(TestCase):
    def setUp(self):
        self.record = ClergyRecord.objects.create(id='CLG-001', name='Fr. Juan')
        self.user = User.objects.create_user('CLG-001', password='pw12345!x')
        self.key = new_key()

    def profile(self, **fields):
        return ClergyProfile.objects.create(user=self.user, record=self.record, **fields)

    def raw(self, profile, field='sss_number'):
        return raw_value(ClergyProfile, profile.pk, field)

    def test_it_is_stored_encrypted_and_reads_back_as_the_real_value(self):
        with encrypted(self.key):
            profile = self.profile(sss_number=NUMBER)
            stored = self.raw(profile)
            self.assertTrue(stored.startswith('enc1:'))
            self.assertNotIn('1234567', stored)
            self.assertEqual(ClergyProfile.objects.get(pk=profile.pk).sss_number, NUMBER)
            self.assertEqual(ClergyProfile.objects.values_list('sss_number', flat=True).get(pk=profile.pk), NUMBER)

    def test_all_three_government_ids_are_covered(self):
        with encrypted(self.key):
            profile = self.profile(sss_number='1', philhealth_number='2', pagibig_number='3')
            for field, value in (('sss_number', '1'), ('philhealth_number', '2'), ('pagibig_number', '3')):
                self.assertTrue(self.raw(profile, field).startswith('enc1:'))
                self.assertEqual(getattr(ClergyProfile.objects.get(pk=profile.pk), field), value)

    def test_with_no_key_it_is_plain_text_exactly_as_before(self):
        with plaintext_mode():
            profile = self.profile(sss_number=NUMBER)
            self.assertEqual(self.raw(profile), NUMBER)
            self.assertEqual(ClergyProfile.objects.get(pk=profile.pk).sss_number, NUMBER)

    def test_a_blank_value_stays_blank(self):
        with encrypted(self.key):
            self.assertEqual(self.raw(self.profile()), '')

    def test_a_value_from_before_is_read_until_encryption_is_required_and_is_encrypted_on_the_next_save(self):
        with plaintext_mode():
            profile = self.profile(sss_number=NUMBER)
        with encrypted(self.key):
            profile = ClergyProfile.objects.get(pk=profile.pk)
            self.assertEqual(profile.sss_number, NUMBER)        # read as it always was
            self.assertEqual(self.raw(profile), NUMBER)         # and still plaintext at rest
            profile.save()
            self.assertTrue(self.raw(profile).startswith('enc1:'))   # until the next save
            self.assertEqual(ClergyProfile.objects.get(pk=profile.pk).sss_number, NUMBER)

    def test_a_plaintext_value_is_refused_when_encryption_is_required(self):
        with plaintext_mode():
            profile = self.profile(sss_number=NUMBER)
        with encrypted(self.key, required=True):
            with self.assertRaisesMessage(encryption.EncryptionError, 'DATA_ENCRYPTION_REQUIRED'):
                ClergyProfile.objects.get(pk=profile.pk)

    def test_update_stores_it_encrypted(self):
        with encrypted(self.key):
            profile = self.profile()
            ClergyProfile.objects.filter(pk=profile.pk).update(sss_number=NUMBER)
            self.assertTrue(self.raw(profile).startswith('enc1:'))

    def test_it_cannot_be_searched_by_value_so_it_fails_loudly_instead_of_finding_nothing(self):
        with encrypted(self.key):
            self.profile(sss_number=NUMBER)
            for lookup in ({'sss_number': NUMBER}, {'sss_number__icontains': '123'}, {'sss_number__in': [NUMBER]}):
                with self.assertRaisesMessage(FieldError, 'stored encrypted'):
                    ClergyProfile.objects.filter(**lookup)
            self.assertEqual(ClergyProfile.objects.filter(sss_number__isnull=False).count(), 1)

    def test_a_damaged_stored_value_is_a_clear_error_not_garbage(self):
        with encrypted(self.key):
            profile = self.profile(sss_number=NUMBER)
            ClergyProfile.objects.filter(pk=profile.pk).update(sss_number='enc1:garbage')
            with self.assertRaises(encryption.EncryptionError):
                ClergyProfile.objects.get(pk=profile.pk)

    def test_the_form_still_limits_length_and_saves_encrypted(self):
        with encrypted(self.key):
            profile = self.profile()
            form = ClergyProfileForm({'sss_number': 'x' * 31}, instance=profile)
            self.assertFalse(form.is_valid())
            self.assertIn('at most 30', form.errors['sss_number'][0])

            form = ClergyProfileForm({'sss_number': NUMBER, 'pagibig_number': '1234-5678-9012'}, instance=profile)
            self.assertTrue(form.is_valid(), form.errors)
            form.save()
            self.assertTrue(self.raw(profile).startswith('enc1:'))
            self.assertEqual(ClergyProfile.objects.get(pk=profile.pk).pagibig_number, '1234-5678-9012')

    def test_typing_something_that_looks_like_stored_data_is_rejected(self):
        with encrypted(self.key):
            form = ClergyProfileForm({'sss_number': 'enc1:made-up'}, instance=self.profile())
            self.assertFalse(form.is_valid())
            self.assertIn('Enter a valid value', form.errors['sss_number'][0])

    def test_the_profile_pages_still_show_the_real_numbers(self):
        with encrypted(self.key):
            self.profile(sss_number=NUMBER)
            activate(self.user)
            self.client.force_login(self.user)
            self.assertContains(self.client.get(reverse('clergy:print_own_record')), NUMBER)
            self.assertContains(self.client.get(reverse('clergy:my_profile')), f'value="{NUMBER}"')

    def test_a_backup_file_holds_the_encrypted_form_and_loads_back(self):
        with encrypted(self.key):
            profile = self.profile(sss_number=NUMBER)
            data = serializers.serialize('json', [profile])
            self.assertNotIn('1234567', data)
            self.assertIn('enc1:', data)
            profile.delete()
            for restored in serializers.deserialize('json', data):
                restored.save()
            restored = ClergyProfile.objects.get(user=self.user)
            self.assertEqual(restored.sss_number, NUMBER)
            self.assertTrue(self.raw(restored).startswith('enc1:'))

    def test_a_backup_cannot_be_loaded_with_the_wrong_key(self):
        with encrypted(self.key):
            data = serializers.serialize('json', [self.profile(sss_number=NUMBER)])
        with encrypted(new_key()):
            with self.assertRaises(serializers.base.DeserializationError):
                list(serializers.deserialize('json', data))

    def test_a_backup_made_before_a_key_change_loads_after_it(self):
        old, new = new_key(), new_key()
        with encrypted(old):
            profile = self.profile(sss_number=NUMBER)
            data = serializers.serialize('json', [profile])
            profile.delete()
        with encrypted(new, old):
            for restored in serializers.deserialize('json', data):
                restored.save()
            stored = self.raw(ClergyProfile.objects.get(user=self.user))
            self.assertTrue(encryption.text_uses_current_key(stored))   # re-encrypted under the new key
            self.assertEqual(ClergyProfile.objects.get(user=self.user).sss_number, NUMBER)

    def test_the_database_backup_download_holds_no_readable_id_numbers(self):
        with encrypted(self.key):
            self.profile(sss_number=NUMBER)
            dump = build_database_dump().decode()
            self.assertNotIn('1234567', dump)
            self.assertIn('enc1:', dump)

    def test_a_plaintext_backup_loads_into_an_encrypted_site(self):
        with plaintext_mode():
            profile = self.profile(sss_number=NUMBER)
            data = serializers.serialize('json', [profile])
            profile.delete()
        with encrypted(self.key):
            for restored in serializers.deserialize('json', data):
                restored.save()
            restored = ClergyProfile.objects.get(user=self.user)
            self.assertTrue(self.raw(restored).startswith('enc1:'))
            self.assertEqual(restored.sss_number, NUMBER)


class DormantTests(MediaFolderMixin, TestCase):
    """With no key the site must not even need the cryptography package."""

    def test_nothing_imports_or_needs_the_library_until_a_key_is_set(self):
        blocked = {'cryptography': None, 'cryptography.fernet': None}   # importing either now raises ImportError
        with mock.patch.dict(sys.modules, blocked), plaintext_mode():
            storage = EncryptedFileSystemStorage()
            name = storage.save('clergy_documents/a.pdf', ContentFile(b'%PDF-1.4 plain'))
            self.assertEqual(storage.open(name).read(), b'%PDF-1.4 plain')

            record = ClergyRecord.objects.create(id='CLG-001', name='Fr. Juan')
            user = User.objects.create_user('CLG-001', password='pw12345!x')
            profile = ClergyProfile.objects.create(user=user, record=record, sss_number=NUMBER)
            self.assertEqual(ClergyProfile.objects.get(pk=profile.pk).sss_number, NUMBER)

            self.assertEqual(checks.encryption_settings(None), [])
            call_command('check', stdout=io.StringIO())

    def test_the_library_being_missing_is_reported_clearly_once_a_key_is_set(self):
        blocked = {'cryptography': None, 'cryptography.fernet': None}
        with mock.patch.dict(sys.modules, blocked), encrypted('a-key-that-was-never-cached'):
            errors = checks.encryption_settings(None)
        self.assertEqual([error.id for error in errors], ['ednp.E001'])
        self.assertIn('not installed', errors[0].msg)


class CheckTests(SimpleTestCase):
    def test_with_no_key_there_is_no_error_but_a_deploy_warning(self):
        with plaintext_mode():
            self.assertEqual(checks.encryption_settings(None), [])
            self.assertEqual([w.id for w in checks.encryption_inactive(None)], ['ednp.W001'])

    @needs_crypto
    def test_a_good_key_gives_neither(self):
        with encrypted(new_key()):
            self.assertEqual(checks.encryption_settings(None), [])
            self.assertEqual(checks.encryption_inactive(None), [])

    @needs_crypto
    def test_a_bad_key_stops_the_site_starting(self):
        with encrypted('this-is-not-a-key'):
            self.assertEqual([e.id for e in checks.encryption_settings(None)], ['ednp.E001'])
            with self.assertRaises(SystemCheckError):
                call_command('check', stdout=io.StringIO(), stderr=io.StringIO())

    def test_requiring_encryption_without_a_key_stops_the_site_starting(self):
        with encrypted(required=True):
            self.assertEqual([e.id for e in checks.encryption_settings(None)], ['ednp.E002'])
