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


from datetime import date
from .models import ClergyChild, ClergyEducation
from registrar.models import ClergyDeployment


class EnhancedClergyProfileTests(TestCase):
    def setUp(self):
        self.record = ClergyRecord.objects.create(
            id='CLG-201',
            name='Fr. Luke Skywalker',
            rank='priest',
            date_of_birth=date(1980, 5, 15),
            diaconate_date=date(2005, 6, 1),
            diaconate_place='All Saints Cathedral, Bontoc',
            ordination_date=date(2006, 6, 1),
            ordination_place='St. Andrew Cathedral',
        )
        self.user = activate(User.objects.create_user('CLG-201', password='pw12345!x'))
        self.profile = ClergyProfile.objects.create(user=self.user, record=self.record)
        self.client.force_login(self.user)

    def test_save_family_and_education_details(self):
        post_data = {
            'name': 'Fr. Luke Skywalker Updated',
            'date_of_birth': '1980-05-15',
            'place_of_birth': 'Sagada, Mountain Province',
            'gender': 'Male',
            'civil_status': 'Married',
            'baptism_date': '1980-06-01',
            'confirmation_date': '1992-06-01',
            'address': 'Bontoc, Mountain Province',
            'email': 'luke@example.com',
            'marriage_date': '2008-10-12',
            'marriage_place': 'Epiphany Church, La Trinidad',
            'spouse_name': 'Mara Jade',
            'spouse_birth_date': '1982-03-21',
            'child_name': ['Ben Skywalker', 'Joy Skywalker'],
            'child_dob': ['2010-04-10', '2015-08-25'],
            'child_gender': ['Male', 'Female'],
            'edu_degree': ['Bachelor of Theology', 'Master of Divinity'],
            'edu_institution': ["St. Andrew's Seminary", 'Trinity College'],
            'edu_place': ['Quezon City', 'Quezon City'],
            'edu_year': ['2004', '2007'],
            'education': 'Continuing pastoral leadership training',
        }
        res = self.client.post(reverse('clergy:my_profile'), post_data, follow=True)
        self.assertEqual(res.status_code, 200)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.place_of_birth, 'Sagada, Mountain Province')
        self.assertEqual(self.profile.marriage_place, 'Epiphany Church, La Trinidad')
        self.assertEqual(self.profile.spouse_name, 'Mara Jade')
        self.assertEqual(self.profile.spouse_birth_date, date(1982, 3, 21))

        # Check structured children
        children = list(self.profile.children.order_by('birth_date'))
        self.assertEqual(len(children), 2)
        self.assertEqual(children[0].name, 'Ben Skywalker')
        self.assertEqual(children[0].gender, 'Male')
        self.assertTrue(children[0].age >= 10)

        self.assertEqual(children[1].name, 'Joy Skywalker')
        self.assertEqual(children[1].gender, 'Female')

        # Check structured educations
        edus = list(self.profile.educations.order_by('year_graduated'))
        self.assertEqual(len(edus), 2)
        self.assertEqual(edus[0].degree, 'Bachelor of Theology')
        self.assertEqual(edus[0].year_graduated, 2004)
        self.assertEqual(edus[1].degree, 'Master of Divinity')
        self.assertEqual(edus[1].year_graduated, 2007)

    def test_clergy_can_edit_holy_orders_and_ordination_history(self):
        self.record.rank = 'bishop'
        self.record.save()
        post_data = {
            'name': 'Fr. Luke Skywalker Updated',
            'date_of_birth': '1980-05-15',
            'place_of_birth': 'Sagada, Mountain Province',
            'gender': 'Male',
            'civil_status': 'Single',
            'baptism_date': '1980-06-01',
            'confirmation_date': '1992-06-01',
            'address': 'Bontoc, Mountain Province',
            'email': 'luke@example.com',
            'diaconate_date': '2005-07-15',
            'diaconate_place': 'Holy Trinity Church, Sagada',
            'ordination_date': '2006-08-20',
            'ordination_place': 'All Saints Cathedral, Bontoc',
            'holy_orders_date': '2004-12-01',
            'bishop_name': 'Bishop Brent',
            'consecration_date': '2020-01-15',
        }
        res = self.client.post(reverse('clergy:my_profile'), post_data, follow=True)
        self.assertEqual(res.status_code, 200)

        self.record.refresh_from_db()
        self.assertEqual(self.record.diaconate_date, date(2005, 7, 15))
        self.assertEqual(self.record.diaconate_place, 'Holy Trinity Church, Sagada')
        self.assertEqual(self.record.ordination_date, date(2006, 8, 20))
        self.assertEqual(self.record.ordination_place, 'All Saints Cathedral, Bontoc')
        self.assertEqual(self.record.holy_orders_date, date(2004, 12, 1))
        self.assertEqual(self.record.bishop_name, 'Bishop Brent')
        self.assertEqual(self.record.consecration_date, date(2020, 1, 15))

        # Also verify GET page renders these fields
        get_res = self.client.get(reverse('clergy:my_profile'))
        self.assertEqual(get_res.status_code, 200)
        self.assertContains(get_res, 'Holy Trinity Church, Sagada')
        self.assertContains(get_res, 'All Saints Cathedral, Bontoc')
        self.assertContains(get_res, 'Bishop Brent')

    def test_years_of_service_adds_up_from_deployments(self):
        # Without deployments, falls back to ordination / diaconate date
        # diaconate_date is 2005-06-01
        self.assertGreaterEqual(self.record.years_of_service, 20)

        # When structured deployments exist, years_of_service adds up from deployments
        ClergyDeployment.objects.create(
            record=self.record,
            station_name='St. Paul Mission',
            place_name='Balbalan',
            start_date=date(2010, 1, 1),
            end_date=date(2013, 1, 1),  # 3 years
            is_current=False
        )
        ClergyDeployment.objects.create(
            record=self.record,
            station_name='St. Luke Mission',
            place_name='Bontoc',
            start_date=date(2015, 1, 1),
            end_date=date(2017, 1, 1),  # 2 years
            is_current=False
        )
        self.assertEqual(self.record.years_of_service, 5)
        self.assertEqual(self.record.years_of_service_display, '5 years')

    def test_single_clergy_cannot_have_spouse_or_children(self):
        post_data = {
            'name': 'Fr. Luke Skywalker Updated',
            'date_of_birth': '1980-05-15',
            'place_of_birth': 'Sagada, Mountain Province',
            'gender': 'Male',
            'civil_status': 'Single',
            'baptism_date': '1980-06-01',
            'confirmation_date': '1992-06-01',
            'address': 'Bontoc, Mountain Province',
            'email': 'luke@example.com',
            'marriage_date': '2008-10-12',
            'marriage_place': 'Epiphany Church, La Trinidad',
            'spouse_name': 'Mara Jade',
            'spouse_birth_date': '1982-03-21',
            'children_names': 'Ben Skywalker',
            'child_name': ['Ben Skywalker'],
            'child_dob': ['2010-04-10'],
            'child_gender': ['Male'],
        }
        res = self.client.post(reverse('clergy:my_profile'), post_data, follow=True)
        self.assertEqual(res.status_code, 200)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.civil_status, 'Single')
        self.assertIsNone(self.profile.marriage_date)
        self.assertEqual(self.profile.marriage_place, '')
        self.assertEqual(self.profile.spouse_name, '')
        self.assertIsNone(self.profile.spouse_birth_date)
        self.assertEqual(self.profile.children.count(), 0)
        self.assertEqual(self.profile.children_names, '')

    def test_transition_from_married_to_single_clears_family(self):
        self.profile.civil_status = 'Married'
        self.profile.spouse_name = 'Mara Jade'
        self.profile.marriage_date = date(2008, 10, 12)
        self.profile.marriage_place = 'Bontoc'
        self.profile.save()
        ClergyChild.objects.create(profile=self.profile, name='Ben Skywalker', birth_date=date(2010, 4, 10))

        post_data = {
            'name': 'Fr. Luke Skywalker Updated',
            'date_of_birth': '1980-05-15',
            'place_of_birth': 'Sagada, Mountain Province',
            'gender': 'Male',
            'civil_status': 'Single',
            'baptism_date': '1980-06-01',
            'confirmation_date': '1992-06-01',
            'address': 'Bontoc, Mountain Province',
            'email': 'luke@example.com',
        }
        res = self.client.post(reverse('clergy:my_profile'), post_data, follow=True)
        self.assertEqual(res.status_code, 200)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.civil_status, 'Single')
        self.assertIsNone(self.profile.marriage_date)
        self.assertEqual(self.profile.spouse_name, '')
        self.assertEqual(self.profile.children.count(), 0)

    def test_clergy_can_add_and_delete_deployments(self):
        # Add deployment
        add_res = self.client.post(reverse('clergy:my_profile'), {
            'action': 'add_deployment',
            'station_name': 'St. Matthew Mission',
            'place_name': 'Sagada',
            'role': 'Vicar',
            'start_date': '2015-01-01',
            'is_current': '1',
            'notes': 'Parish mission',
        }, follow=True)
        self.assertEqual(add_res.status_code, 200)

        dep = ClergyDeployment.objects.get(record=self.record, station_name='St. Matthew Mission')
        self.assertEqual(dep.place_name, 'Sagada')
        self.assertTrue(dep.is_current)

        # Delete deployment
        del_res = self.client.post(reverse('clergy:my_profile'), {
            'action': 'delete_deployment',
            'deployment_id': dep.id,
        }, follow=True)
        self.assertEqual(del_res.status_code, 200)
        self.assertFalse(ClergyDeployment.objects.filter(id=dep.id).exists())

    def test_print_own_record_renders_enhancements(self):
        ClergyDeployment.objects.create(
            record=self.record, station_name='Holy Trinity Mission',
            place_name='Bontoc', role='Rector', is_current=True
        )
        ClergyChild.objects.create(
            profile=self.profile, name='Ben Skywalker', birth_date=date(2012, 1, 1), gender='Male'
        )
        ClergyEducation.objects.create(
            profile=self.profile, degree='B.Th.', institution='SAS', place='QC', year_graduated=2004
        )
        res = self.client.get(reverse('clergy:print_own_record'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'All Saints Cathedral, Bontoc')
        self.assertContains(res, 'Holy Trinity Mission')
        self.assertContains(res, 'Ben Skywalker')
        self.assertContains(res, 'B.Th.')

    def test_date_attachments_map_and_quick_upload_flow(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from registrar.models import Document, DocumentType

        birth_type, _ = DocumentType.objects.get_or_create(
            name='Birth Certificate',
            defaults={'uploaded_by': 'clergy', 'is_required': True}
        )
        orders_type, _ = DocumentType.objects.get_or_create(
            name='Ordination Entry / Holy Orders Document',
            defaults={'uploaded_by': 'registrar', 'is_required': True}
        )

        # 1. Initial state: map reflects missing documents
        att_map = self.record.date_attachments_map
        self.assertIn('date_of_birth', att_map)
        self.assertIn('diaconate_date', att_map)
        self.assertFalse(att_map['date_of_birth']['is_uploaded'])
        self.assertTrue(att_map['date_of_birth']['can_clergy_upload'])
        self.assertFalse(att_map['diaconate_date']['can_clergy_upload'])

        # GET my_profile displays quick upload trigger
        page = self.client.get(reverse('clergy:my_profile'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'No Birth Certificate attached')
        self.assertContains(page, 'data-quick-upload')

        # 2. Clergy quick-uploads Birth Certificate
        pdf_file = SimpleUploadedFile('birth.pdf', b'%PDF-1.4\ncontent', content_type='application/pdf')
        res = self.client.post(reverse('clergy:my_profile'), {
            'action': 'upload_attachment',
            'document_type': birth_type.id,
            'file': pdf_file,
            'notes': 'Uploaded via date field',
        }, follow=True)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Birth Certificate uploaded successfully.')

        # Verify document created and map updated
        fresh_record = ClergyRecord.objects.get(id=self.record.id)
        fresh_map = fresh_record.date_attachments_map
        self.assertTrue(fresh_map['date_of_birth']['is_uploaded'])
        self.assertIsNotNone(fresh_map['date_of_birth']['doc_id'])

        # 3. Clergy cannot upload registrar-only document
        res_disallowed = self.client.post(reverse('clergy:my_profile'), {
            'action': 'upload_attachment',
            'document_type': orders_type.id,
            'file': SimpleUploadedFile('orders.pdf', b'%PDF-1.4\ncontent', content_type='application/pdf'),
        }, follow=True)
        self.assertContains(res_disallowed, 'That document is provided by the Registrar')

        # 4. Registrar quick-uploads Holy Orders document
        reg_user = activate(User.objects.create_user('reg_test', password='pw12345!x', is_staff=True))
        reg_client = self.client_class()
        reg_client.force_login(reg_user)

        res_reg = reg_client.post(reverse('registrar:record_detail', args=[self.record.id]), {
            'action': 'upload_attachment',
            'document_type': orders_type.id,
            'file': SimpleUploadedFile('holy_orders.pdf', b'%PDF-1.4\ncontent', content_type='application/pdf'),
            'notes': 'Registrar upload',
        })
        self.assertRedirects(res_reg, f"{reverse('registrar:record_detail', args=[self.record.id])}?tab=personal")

        # Map now reflects uploaded for holy orders dates
        fresh_record_2 = ClergyRecord.objects.get(id=self.record.id)
        self.assertTrue(fresh_record_2.date_attachments_map['diaconate_date']['is_uploaded'])
        self.assertIsNotNone(fresh_record_2.date_attachments_map['diaconate_date']['doc_id'])

    def test_sequential_ordination_progression_by_rank(self):
        # 1. Postulancy cannot hold any ordination dates
        rec_post = ClergyRecord(
            id='CLG-POST', name='Postulant Mark', rank='postulancy',
            diaconate_date=date(2020, 1, 1), ordination_date=date(2021, 1, 1)
        )
        rec_post.save()
        self.assertIsNone(rec_post.diaconate_date)
        self.assertIsNone(rec_post.ordination_date)

        # 2. Deacon can hold diaconate, but not priesthood or consecration
        rec_deacon = ClergyRecord(
            id='CLG-DEAC', name='Deacon Paul', rank='deacon',
            diaconate_date=date(2020, 1, 1), diaconate_place='Bontoc',
            ordination_date=date(2021, 1, 1), consecration_date=date(2025, 1, 1)
        )
        rec_deacon.save()
        self.assertEqual(rec_deacon.diaconate_date, date(2020, 1, 1))
        self.assertEqual(rec_deacon.diaconate_place, 'Bontoc')
        self.assertIsNone(rec_deacon.ordination_date)
        self.assertIsNone(rec_deacon.consecration_date)

        # 3. Priest can hold diaconate and priesthood, but not consecration
        rec_priest = ClergyRecord(
            id='CLG-PRST', name='Fr. Peter', rank='priest',
            diaconate_date=date(2020, 1, 1), ordination_date=date(2021, 1, 1),
            consecration_date=date(2025, 1, 1)
        )
        rec_priest.save()
        self.assertEqual(rec_priest.diaconate_date, date(2020, 1, 1))
        self.assertEqual(rec_priest.ordination_date, date(2021, 1, 1))
        self.assertIsNone(rec_priest.consecration_date)

        # 4. Bishop can hold all three ordinations
        rec_bishop = ClergyRecord(
            id='CLG-BSHP', name='Bp. James', rank='bishop',
            diaconate_date=date(2010, 1, 1), ordination_date=date(2012, 1, 1),
            consecration_date=date(2020, 1, 1)
        )
        rec_bishop.save()
        self.assertEqual(rec_bishop.diaconate_date, date(2010, 1, 1))
        self.assertEqual(rec_bishop.ordination_date, date(2012, 1, 1))
        self.assertEqual(rec_bishop.consecration_date, date(2020, 1, 1))

    def test_chronological_ordination_validation(self):
        from django.core.exceptions import ValidationError
        # Diaconate after Priesthood is invalid
        rec_bad_priest = ClergyRecord(
            id='CLG-ERR1', name='Fr. Backward', rank='priest',
            diaconate_date=date(2022, 1, 1), ordination_date=date(2020, 1, 1)
        )
        with self.assertRaises(ValidationError) as ctx:
            rec_bad_priest.save()
        self.assertIn('ordination_date', ctx.exception.message_dict)

        # Priesthood after Bishop Consecration is invalid
        rec_bad_bishop = ClergyRecord(
            id='CLG-ERR2', name='Bp. Backward', rank='bishop',
            diaconate_date=date(2010, 1, 1), ordination_date=date(2022, 1, 1),
            consecration_date=date(2020, 1, 1)
        )
        with self.assertRaises(ValidationError) as ctx:
            rec_bad_bishop.save()
        self.assertIn('consecration_date', ctx.exception.message_dict)

    def test_date_attachments_map_filtered_by_rank(self):
        rec_deacon = ClergyRecord.objects.create(
            id='CLG-MAP-D', name='Deacon Timothy', rank='deacon',
            diaconate_date=date(2020, 1, 1)
        )
        deacon_map = rec_deacon.date_attachments_map
        self.assertIn('diaconate_date', deacon_map)
        self.assertNotIn('ordination_date', deacon_map)
        self.assertNotIn('consecration_date', deacon_map)

        rec_priest = ClergyRecord.objects.create(
            id='CLG-MAP-P', name='Fr. Timothy', rank='priest',
            diaconate_date=date(2018, 1, 1), ordination_date=date(2019, 1, 1)
        )
        priest_map = rec_priest.date_attachments_map
        self.assertIn('diaconate_date', priest_map)
        self.assertIn('ordination_date', priest_map)
        self.assertNotIn('consecration_date', priest_map)

        rec_bishop = ClergyRecord.objects.create(
            id='CLG-MAP-B', name='Bp. Timothy', rank='bishop',
            diaconate_date=date(2010, 1, 1), ordination_date=date(2012, 1, 1),
            consecration_date=date(2020, 1, 1)
        )
        bishop_map = rec_bishop.date_attachments_map
        self.assertIn('diaconate_date', bishop_map)
        self.assertIn('ordination_date', bishop_map)
        self.assertIn('consecration_date', bishop_map)

