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
