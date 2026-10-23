"""Uploaded photos and scans are shrunk before they are stored (registrar/images.py); PDFs are
stored as they are, up to 5 MB."""

import io
import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from PIL import Image

from accounts.testing import activate
from ednp.testing import MediaFolderMixin

from . import images
from .forms import MAX_STORED_BYTES, DocumentUploadForm
from .models import ClergyRecord, Document, DocumentType

User = get_user_model()


def photo_like(width, height, mode='RGB'):
    """An image with the smooth detail of a photo or scan: random at 1/20 the size, enlarged."""
    small = Image.frombytes(mode, (max(width // 20, 1), max(height // 20, 1)),
                            os.urandom(max(width // 20, 1) * max(height // 20, 1) * len(mode)))
    return small.resize((width, height), Image.Resampling.BICUBIC)


def noise(width, height):
    """As hard to compress as an image can be."""
    return Image.frombytes('RGB', (width, height), os.urandom(width * height * 3))


def encoded(image, fmt='PNG', **options):
    out = io.BytesIO()
    image.save(out, fmt, **options)
    return out.getvalue()


def upload(name, content):
    return SimpleUploadedFile(name, content)


def opened(stored):
    stored.seek(0)
    return Image.open(io.BytesIO(stored.read()))


class ShrinkTests(TestCase):
    def test_a_large_scan_becomes_a_much_smaller_jpeg_no_more_than_max_side_across(self):
        original = upload('scan.png', encoded(photo_like(2400, 3200)))
        stored = images.shrink(original)
        self.assertEqual(stored.name, 'scan.jpg')
        self.assertEqual(stored.content_type, 'image/jpeg')
        self.assertLess(stored.size, original.size / 5)
        with opened(stored) as image:
            self.assertEqual((image.format, image.size), ('JPEG', (1500, 2000)))

    def test_a_phone_photo_is_turned_the_right_way_up_and_loses_its_metadata(self):
        exif = Image.Exif()
        exif[0x0112] = 6                                  # orientation: rotate 90 degrees to view
        exif[0x010F] = 'PhoneMaker'                       # camera make
        original = upload('photo.jpg', encoded(photo_like(4000, 3000), 'JPEG', quality=95, exif=exif))
        stored = images.shrink(original)
        with opened(stored) as image:
            self.assertEqual(image.size, (1500, 2000))    # stored upright
            self.assertEqual(dict(image.getexif()), {})

    def test_transparent_parts_become_white(self):
        picture = Image.new('RGBA', (2400, 1200), (0, 0, 0, 0))
        picture.paste(photo_like(1200, 1200, 'RGBA').convert('RGB'), (1200, 0))
        stored = images.shrink(upload('signature.png', encoded(picture)))
        with opened(stored) as image:
            red, green, blue = image.convert('RGB').getpixel((10, 10))
            self.assertTrue(min(red, green, blue) > 245)

    def test_an_image_already_smaller_as_it_is_is_kept_as_uploaded(self):
        original = upload('small.jpg', encoded(photo_like(600, 400), 'JPEG', quality=40))
        self.assertIs(images.shrink(original), original)

    def test_an_image_that_cannot_be_read_is_kept_as_uploaded(self):
        original = upload('broken.png', b'\x89PNG\r\n\x1a\n' + b'not really an image' * 100)
        self.assertIs(images.shrink(original), original)
        self.assertEqual(original.read(8), b'\x89PNG\r\n\x1a\n')   # and handed back ready to be read from the start

    def test_a_png_too_big_to_decode_safely_is_kept_as_uploaded(self):
        original = upload('huge.png', encoded(Image.new('L', (2000, 2000), 255)))
        with mock.patch.object(images, 'MAX_DECODE_PIXELS', 1_000_000), \
                mock.patch('PIL.ImageFile.ImageFile.load', side_effect=AssertionError('decoded')):
            self.assertIs(images.shrink(original), original)

    def test_a_far_bigger_jpeg_is_still_shrunk(self):
        original = upload('big.jpg', encoded(photo_like(6000, 4500), 'JPEG', quality=90))
        with mock.patch.object(images, 'MAX_DECODE_PIXELS', 1_000_000):
            stored = images.shrink(original)
        with opened(stored) as image:
            self.assertEqual(image.size, (2000, 1500))


class UploadFormTests(TestCase):
    def setUp(self):
        self.document_type, _ = DocumentType.objects.get_or_create(name='Baptismal Certificate')

    def form(self, name, content):
        return DocumentUploadForm({'document_type': self.document_type.pk, 'notes': ''},
                                  {'file': upload(name, content)})

    def test_a_pdf_is_stored_as_it_is(self):
        pdf = b'%PDF-1.4\n' + os.urandom(4 * 1024 * 1024)
        form = self.form('result.pdf', pdf)
        self.assertTrue(form.is_valid(), form.errors)
        stored = form.cleaned_data['file']
        stored.seek(0)
        self.assertEqual((stored.name, stored.read()), ('result.pdf', pdf))

    def test_a_pdf_over_5_mb_is_refused_with_advice(self):
        form = self.form('result.pdf', b'%PDF-1.4\n' + bytes(MAX_STORED_BYTES))
        self.assertFalse(form.is_valid())
        self.assertIn('That PDF is too large — the maximum is 5 MB.', form.errors['file'][0])

    def test_a_photo_too_big_to_store_as_it_is_is_accepted_once_shrunk(self):
        content = encoded(noise(1800, 1500))
        self.assertGreater(len(content), MAX_STORED_BYTES)
        form = self.form('photo.png', content)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertLessEqual(form.cleaned_data['file'].size, MAX_STORED_BYTES)
        self.assertEqual(form.cleaned_data['file'].name, 'photo.jpg')

    def test_an_image_that_stays_over_5_mb_is_refused(self):
        form = self.form('scan.png', b'\x89PNG\r\n\x1a\n' + bytes(MAX_STORED_BYTES))   # unreadable, so not shrunk
        self.assertFalse(form.is_valid())
        self.assertIn('That image is too large to store', form.errors['file'][0])

    def test_nothing_over_10_mb_is_taken_at_all(self):
        with mock.patch.object(images, 'shrink') as shrink:
            form = self.form('photo.jpg', b'\xff\xd8\xff' + bytes(10 * 1024 * 1024))
            self.assertFalse(form.is_valid())
        shrink.assert_not_called()
        self.assertIn('the maximum is 10 MB', form.errors['file'][0])

    def test_a_file_that_is_not_what_it_claims_is_still_refused_before_any_image_work(self):
        with mock.patch.object(images, 'shrink') as shrink:
            form = self.form('photo.png', b'<html>not a picture</html>')
            self.assertFalse(form.is_valid())
        shrink.assert_not_called()


class UploadPageTests(MediaFolderMixin, TestCase):
    def test_a_photo_uploaded_by_the_registrar_is_stored_shrunk_and_opens_as_a_jpeg(self):
        registrar = activate(User.objects.create_user('reg', password='pw12345!x', is_staff=True))
        record = ClergyRecord.objects.create(id='CLG-001', name='Fr. Juan')
        document_type, _ = DocumentType.objects.get_or_create(name='Baptismal Certificate')
        client = Client()
        client.force_login(registrar)
        original = encoded(photo_like(3000, 4000, 'L'))   # a greyscale scan
        client.post(reverse('registrar:record_detail', args=[record.id]), {
            'document_type': document_type.pk, 'file': upload('baptism.png', original), 'notes': ''})

        document = Document.objects.get()
        self.assertTrue(document.file.name.endswith('_baptism.jpg'))
        self.assertLess(document.file.size, len(original) / 4)
        response = client.get(reverse('registrar:view_document', args=[document.id]))
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        with Image.open(io.BytesIO(b''.join(response.streaming_content))) as image:
            self.assertEqual(image.size, (1500, 2000))
