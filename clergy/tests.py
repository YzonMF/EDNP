from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.testing import activate
from registrar.models import ClergyRecord

from .models import ClergyProfile

User = get_user_model()


class BlankFieldTests(TestCase):
    """Empty record fields on My Record & Profile are shown empty, not as the word "None",
    which the required address box counted as filled in and saved as the address."""

    def test_empty_record_fields_are_shown_empty(self):
        record = ClergyRecord.objects.create(id='CLG-001', name='Fr. New')   # a new record: name only
        user = User.objects.create_user('CLG-001', password='pw12345!x')
        ClergyProfile.objects.create(user=user, record=record)
        self.client.force_login(activate(user))

        page = self.client.get(reverse('clergy:my_profile'))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'value="None"')
        self.assertNotContains(page, '>None</textarea>')


class ProfilePictureTests(TestCase):
    """Profile pictures for clergy accounts: uploaded and managed by the clergy
    member on My Record & Profile, and displayed in the clergy portal navigation
    panel as well as on the Registrar's record detail page."""

    PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32

    def setUp(self):
        import shutil
        import tempfile
        from django.test import override_settings

        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)

        self.registrar = activate(User.objects.create_user('reg', password='pw12345!x', is_staff=True))
        self.record = ClergyRecord.objects.create(id='CLG-001', name='Fr. Juan Dela Cruz')
        self.clergy = activate(User.objects.create_user('CLG-001', password='pw12345!x'))
        self.profile = ClergyProfile.objects.create(user=self.clergy, record=self.record)

    def test_clergy_uploads_own_photo_and_both_nav_and_registrar_show_it(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import Client

        clergy_client = Client()
        clergy_client.force_login(self.clergy)
        dashboard = clergy_client.get(reverse('clergy:dashboard'))
        photo_url = reverse('registrar:view_profile_picture', args=[self.record.id])
        self.assertContains(dashboard, 'class="sidebar-avatar"')
        self.assertContains(dashboard, f'<span>{self.profile.initials}</span>')
        self.assertNotContains(dashboard, photo_url)

        # Clergy member uploads their profile picture on My Record & Profile
        res = clergy_client.post(
            reverse('clergy:my_profile'),
            {'upload_profile_picture': '1', 'profile_picture': SimpleUploadedFile('portrait.png', self.PNG)},
            follow=True,
        )
        self.assertContains(res, 'Profile picture updated successfully.')
        self.assertContains(res, photo_url)

        # Clergy nav panel and Registrar record detail both display the photo
        dashboard = clergy_client.get(reverse('clergy:dashboard'))
        self.assertContains(dashboard, f'src="{photo_url}"')
        self.assertContains(dashboard, 'class="top-profile"')
        self.assertEqual(clergy_client.get(photo_url).status_code, 200)

        reg_client = Client()
        reg_client.force_login(self.registrar)
        reg_detail = reg_client.get(reverse('registrar:record_detail', args=[self.record.id]))
        self.assertContains(reg_detail, f'src="{photo_url}"')
        self.assertNotContains(reg_detail, 'name="upload_profile_picture"')
        self.assertEqual(reg_client.get(photo_url).status_code, 200)

    def test_profile_picture_permissions_and_replacement_and_removal(self):
        import os
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import Client

        clergy_client = Client()
        clergy_client.force_login(self.clergy)
        profile_url = reverse('clergy:my_profile')
        photo_url = reverse('registrar:view_profile_picture', args=[self.record.id])

        # Rejects non-image uploads (e.g. PDF or HTML)
        bad = clergy_client.post(
            profile_url,
            {'upload_profile_picture': '1', 'profile_picture': SimpleUploadedFile('doc.pdf', b'%PDF-1.4\n')},
            follow=True,
        )
        self.assertContains(bad, 'Only JPG and PNG images are allowed for a profile picture.')

        # Rejects files larger than 5 MB
        too_large = clergy_client.post(
            profile_url,
            {'upload_profile_picture': '1', 'profile_picture': SimpleUploadedFile('huge.png', b'\x89PNG\r\n\x1a\n' + b'0' * (5 * 1024 * 1024 + 10))},
            follow=True,
        )
        self.assertContains(too_large, 'Image is too large — the maximum is 5 MB.')

        # Upload first photo, then replace it and verify old file is cleaned up
        with self.captureOnCommitCallbacks(execute=True):
            clergy_client.post(
                profile_url,
                {'upload_profile_picture': '1', 'profile_picture': SimpleUploadedFile('first.png', self.PNG)},
            )
        self.record.refresh_from_db()
        first_path = self.record.profile_picture.path
        self.assertTrue(os.path.exists(first_path))

        with self.captureOnCommitCallbacks(execute=True):
            clergy_client.post(
                profile_url,
                {'upload_profile_picture': '1', 'profile_picture': SimpleUploadedFile('second.png', self.PNG)},
            )
        self.record.refresh_from_db()
        second_path = self.record.profile_picture.path
        self.assertFalse(os.path.exists(first_path))
        self.assertTrue(os.path.exists(second_path))

        # Another clergy member cannot view this clergy member's profile picture
        other_record = ClergyRecord.objects.create(id='CLG-002', name='Fr. Pedro')
        other_user = activate(User.objects.create_user('CLG-002', password='pw12345!x'))
        ClergyProfile.objects.create(user=other_user, record=other_record)
        other_client = Client()
        other_client.force_login(other_user)
        self.assertEqual(other_client.get(photo_url).status_code, 404)

        # Removing the profile picture deletes the file and returns 404 on the photo URL
        with self.captureOnCommitCallbacks(execute=True):
            clergy_client.post(profile_url, {'remove_profile_picture': '1'})
        self.record.refresh_from_db()
        self.assertFalse(bool(self.record.profile_picture))
        self.assertFalse(os.path.exists(second_path))
        self.assertEqual(clergy_client.get(photo_url).status_code, 404)

    def test_widescreen_screenshot_is_cropped_to_standard_square_dimensions(self):
        import io
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import Client
        from PIL import Image
        from registrar.images import AVATAR_SIZE

        buf = io.BytesIO()
        Image.new('RGB', (1920, 1080), (40, 90, 160)).save(buf, 'PNG')
        clergy_client = Client()
        clergy_client.force_login(self.clergy)
        clergy_client.post(
            reverse('clergy:my_profile'),
            {'upload_profile_picture': '1', 'profile_picture': SimpleUploadedFile('screenshot.png', buf.getvalue())},
        )
        self.record.refresh_from_db()
        with self.record.profile_picture.open('rb') as stored_file:
            with Image.open(stored_file) as img:
                self.assertEqual(img.size, (AVATAR_SIZE, AVATAR_SIZE))
