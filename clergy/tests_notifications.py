"""The clergy Notifications page: events stored as they happen, reminders made once
when a date comes due, what needs attention right now, and the unread badge."""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import dateformat, timezone

from accounts.models import TrustedDevice
from accounts.testing import activate, trust_device
from accounts.tests_activation import ActivationTestCase
from accounts.tests_forgot import ForgotCase
from accounts.tests_mysecurity import OLD, PASSWORD, SecurityTestCase
from ednp import login_throttle
from ednp.testing import MediaFolderMixin
from registrar.models import ClergyDeployment, ClergyRecord, Document, DocumentType, EmberLetter

from . import notifications
from .models import ClergyProfile, Notification

User = get_user_model()

CHROME_ON_WINDOWS = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
NEW_PASSWORD = 'Sturdy!Pass-2026'
PDF = b'%PDF-1.4\n%fake but correctly-headed pdf\n'
PAGE = reverse('clergy:notifications')


def only(user, kind=None):
    """The one notification `user` has (of `kind`); an error if there isn't exactly one."""
    found = Notification.objects.filter(user=user)
    return (found.filter(kind=kind) if kind else found).get()


def make_clergy(username='fr', record_id='CLG-001', **record_fields):
    record = ClergyRecord.objects.create(id=record_id, name='Fr. Juan', phone=OLD, **record_fields)
    user = User.objects.create_user(username, password=PASSWORD)
    ClergyProfile.objects.create(user=user, record=record)
    activate(user, phone=OLD)
    return user, record


class AccountEventTests(SecurityTestCase):
    def setUp(self):
        super().setUp()
        self.client.defaults['HTTP_USER_AGENT'] = CHROME_ON_WINDOWS

    def change_password(self, current=PASSWORD):
        return self.post(action='change_password', current_password=current,
                         new_password=NEW_PASSWORD, confirm_password=NEW_PASSWORD)

    def test_changing_the_password_notifies_and_lights_up_the_bell(self):
        self.change_password()
        note = only(self.user)
        self.assertEqual((note.kind, note.title), ('password_changed', 'Password changed'))
        self.assertIn('on Chrome on Windows', note.message)
        self.assertIsNone(note.read_at)
        self.assertContains(self.client.get(reverse('clergy:dashboard')), 'title="1 unread">1</span>')

    def test_a_refused_change_notifies_nothing(self):
        self.change_password(current='wrong')
        self.post(action='change_username', username='has space', current_password=PASSWORD)
        self.assertFalse(Notification.objects.exists())

    def test_changing_the_username_names_the_old_and_the_new(self):
        self.post(action='change_username', username='fr.juan', current_password=PASSWORD)
        self.assertIn('from fr to fr.juan', only(self.user, 'username_changed').message)

    def test_changing_the_number_shows_only_the_new_number_masked(self):
        self.send()
        self.post(action='verify_change_code', code=self.code())
        message = only(self.user, 'phone_changed').message
        self.assertIn('09•••••••43', message)
        self.assertNotIn('9189876543', message)

    def test_removing_trusted_devices_notifies(self):
        soon = timezone.now() + timedelta(days=5)
        phone = TrustedDevice.objects.create(user=self.user, token_hash='p' * 64, label='Safari on iPhone', expires_at=soon)
        self.post(action='revoke_device', device_id=phone.pk)
        self.assertIn('Safari on iPhone is no longer trusted', only(self.user, 'device_removed').message)

        for label in 'AB':
            TrustedDevice.objects.create(user=self.user, token_hash=label * 64, label=label, expires_at=soon)
        self.post(action='revoke_others')
        self.assertTrue(Notification.objects.filter(
            user=self.user, title='Other devices removed', message__startswith='2 other trusted devices were removed').exists())

    def test_removing_a_device_that_is_already_gone_notifies_nothing(self):
        self.post(action='revoke_device', device_id='99999')
        self.post(action='revoke_others')   # this browser is the only one, so nothing to remove
        self.assertFalse(Notification.objects.exists())

    def test_too_many_wrong_current_passwords_warn_the_owner_once(self):
        for _ in range(login_throttle.MAX_ATTEMPTS + 2):
            self.change_password(current='wrong')
        self.assertIn('Chrome on Windows', only(self.user, 'sign_in_locked').message)


class SignInEventTests(TestCase):
    def setUp(self):
        self.user, self.record = make_clergy()
        self.client = Client(HTTP_USER_AGENT=CHROME_ON_WINDOWS)

    def sign_in_with_code(self, remember=True):
        self.client.post('/', {'username': 'fr', 'password': PASSWORD})
        data = {'action': 'verify_code', 'code': self.client.session['device_test_code']}
        if remember:
            data['remember'] = '1'
        return self.client.post(reverse('verify_device'), data)

    def test_a_new_device_signing_in_is_notified(self):
        self.sign_in_with_code()
        note = only(self.user, 'new_sign_in')
        self.assertEqual(note.title, 'New sign-in on Chrome on Windows')
        self.assertIn('with a code sent to 09•••••••67', note.message)
        self.assertIn("It's now a trusted device", note.message)

    def test_a_device_that_isnt_remembered_isnt_called_trusted(self):
        self.sign_in_with_code(remember=False)
        self.assertNotIn('trusted device', only(self.user, 'new_sign_in').message)

    def test_signing_in_on_a_trusted_device_is_not_notified(self):
        trust_device(self.client, self.user)
        self.client.post('/', {'username': 'fr', 'password': PASSWORD})
        self.assertEqual(self.client.get(reverse('clergy:dashboard')).status_code, 200)
        self.assertFalse(Notification.objects.exists())

    def test_a_lockout_warns_the_owner_once(self):
        for _ in range(login_throttle.MAX_ATTEMPTS + 2):
            self.client.post('/', {'username': 'fr', 'password': 'wrong'})
        message = only(self.user, 'sign_in_locked').message
        self.assertIn(f'{login_throttle.MAX_ATTEMPTS} times', message)
        self.assertIn('The last try came from Chrome on Windows', message)

    def test_another_lockout_adds_nothing_while_the_last_notice_is_unread(self):
        request = RequestFactory().get('/', HTTP_USER_AGENT=CHROME_ON_WINDOWS)
        notifications.sign_in_locked(request, self.user)
        notifications.sign_in_locked(request, self.user)
        self.assertEqual(Notification.objects.count(), 1)
        Notification.objects.update(read_at=timezone.now())
        notifications.sign_in_locked(request, self.user)
        self.assertEqual(Notification.objects.count(), 2)

    def test_wrong_passwords_for_a_name_nobody_has_notify_nobody(self):
        for _ in range(login_throttle.MAX_ATTEMPTS):
            self.client.post('/', {'username': 'nobody', 'password': 'wrong'})
        self.assertFalse(Notification.objects.exists())

    def test_registrar_accounts_get_no_notifications(self):
        registrar = activate(User.objects.create_user('reg', password=PASSWORD, is_staff=True))
        for _ in range(login_throttle.MAX_ATTEMPTS):
            self.client.post('/', {'username': 'reg', 'password': 'wrong'})
        self.assertIsNone(notifications.notify(registrar, 'password_changed', 'Password changed'))
        self.assertFalse(Notification.objects.exists())


class ForgotPasswordEventTests(ForgotCase):
    def test_a_reset_is_notified_with_the_number_masked(self):
        self.send()
        self.verify()
        self.reset()
        message = only(self.user, 'password_reset').message
        self.assertIn('with a code sent to 09•••••••67', message)
        self.assertIn('every other device was signed out', message)


class ActivationEventTests(ActivationTestCase):
    def test_finishing_setup_is_notified(self):
        self.finish_phone_step()
        self.assertFalse(Notification.objects.exists())   # not until the account is ready
        self.set_password()
        self.assertEqual(only(self.user).kind, 'account_activated')


class RecordEventTests(MediaFolderMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user, self.record = make_clergy(rank='priest')
        self.clergy = Client()
        self.clergy.force_login(self.user)
        self.registrar = activate(User.objects.create_user('reg', password=PASSWORD, is_staff=True, first_name='Maria Santos'))
        self.staff = Client()
        self.staff.force_login(self.registrar)

    def save_profile(self, **changes):
        return self.clergy.post(reverse('clergy:my_profile'), {
            'name': 'Fr. Juan', 'gender': 'Male', 'date_of_birth': '1980-01-01', 'baptism_date': '1981-01-01',
            'confirmation_date': '1995-01-01', 'email': 'juan@example.org', 'address': 'Baguio City', **changes})

    def test_saving_the_profile_names_what_changed_but_never_the_values(self):
        self.save_profile(sss_number='34-1234567-8')
        message = only(self.user, 'profile_updated').message
        for label in ('Gender', 'Date of birth', 'Address', 'SSS Number'):
            self.assertIn(label, message)
        self.assertNotIn('34-1234567-8', message)
        self.assertNotIn('Baguio', message)

    def test_saving_without_changing_anything_notifies_nothing(self):
        self.save_profile()
        Notification.objects.all().delete()
        self.save_profile()
        self.assertFalse(Notification.objects.exists())

    def test_a_registrar_edit_spells_out_only_the_official_changes(self):
        self.staff.post(reverse('registrar:record_detail', args=[self.record.id]), {
            'name': 'Fr. Juan', 'rank': 'priest', 'status': 'On Leave', 'leave_reason': 'medical treatment',
            'contract_end': '2027-06-30', 'address': '12 Secret St.'})
        note = only(self.user, 'record_updated')
        self.assertEqual(note.title, 'Your record was updated')
        for phrase in ('Maria Santos (Registrar) changed:', 'Status (now On Leave)', 'Contract end (now Jun 30, 2027)',
                       'Leave reason', 'Address'):
            self.assertIn(phrase, note.message)
        self.assertNotIn('medical', note.message)
        self.assertNotIn('Secret', note.message)

    def test_a_record_without_an_account_can_still_be_edited(self):
        loner = ClergyRecord.objects.create(id='CLG-002', name='Fr. Pedro')
        response = self.staff.post(reverse('registrar:record_detail', args=[loner.id]),
                                   {'name': 'Fr. Pedro', 'rank': 'priest', 'status': 'Retired'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Notification.objects.exists())

    def test_documents_added_by_either_side_tell_the_owner(self):
        medical = DocumentType.objects.create(name='Medical Exam Result', validity_period_months=12)
        ordination = DocumentType.objects.create(name='Certificate of Ordination', uploaded_by='registrar')
        self.staff.post(reverse('registrar:record_detail', args=[self.record.id]),
                        {'document_type': ordination.pk, 'file': SimpleUploadedFile('ord.pdf', PDF)})
        self.clergy.post(reverse('clergy:my_requirements'),
                         {'document_type': medical.pk, 'file': SimpleUploadedFile('med.pdf', PDF)})

        by_registrar = Notification.objects.get(title='Certificate of Ordination added by the Registrar')
        self.assertEqual(by_registrar.message, 'Maria Santos (Registrar) uploaded your Certificate of Ordination.')
        mine = Notification.objects.get(title='Medical Exam Result uploaded')
        expiry = Document.objects.get(document_type=medical).expiry_date()
        self.assertIn(f'It is valid until {dateformat.format(expiry, "M j, Y")}.', mine.message)
        self.assertEqual({by_registrar.user, mine.user}, {self.user})

    def test_a_registrar_reset_is_explained_to_the_owner(self):
        response = self.staff.post(reverse('registrar:reset_account', args=[self.record.id]),
                                   {'kind': 'lost_phone', 'note': 'ID checked in person'})
        self.assertEqual(response['Content-Type'], 'application/pdf')
        message = only(self.user, 'account_reset').message
        self.assertIn('Maria Santos (Registrar) reset your account', message)
        self.assertNotIn('ID checked', message)   # the registrar's note is for the activity log only


    def test_registrar_adding_and_removing_deployment_notifies_clergy(self):
        self.staff.post(reverse('registrar:record_detail', args=[self.record.id]), {
            'action': 'add_deployment',
            'station_name': 'St. Barnabas Parish',
            'place_name': 'Alab, Bontoc',
            'role': 'Priest-in-Charge',
        })
        note = only(self.user, 'deployment_added')
        self.assertEqual(note.title, 'Deployment added')
        self.assertIn('Maria Santos (Registrar) added a deployment for you at St. Barnabas Parish in Alab, Bontoc (Priest-in-Charge).', note.message)

        dep = ClergyDeployment.objects.get(record=self.record)
        self.staff.post(reverse('registrar:record_detail', args=[self.record.id]), {
            'action': 'delete_deployment',
            'deployment_id': dep.id,
        })
        removed = only(self.user, 'deployment_removed')
        self.assertEqual(removed.title, 'Deployment removed')
        self.assertIn('Maria Santos (Registrar) removed your deployment at St. Barnabas Parish.', removed.message)

    def test_registrar_unmarking_noted_requirement_notifies_clergy(self):
        doc_type = DocumentType.objects.create(name='Psychiatric Evaluation', requires_upload=False)
        self.staff.post(reverse('registrar:record_detail', args=[self.record.id]), {
            'action': 'toggle_noted_requirement',
            'document_type_id': doc_type.id,
            'set_submitted': '0',
        })
        note = only(self.user, 'document_removed')
        self.assertEqual(note.title, 'Psychiatric Evaluation marked not submitted')
        self.assertIn('Maria Santos (Registrar) marked your Psychiatric Evaluation as Not Submitted.', note.message)

    def test_registrar_deleting_ember_letter_notifies_clergy(self):
        ember = EmberLetter.objects.create(record=self.record, year=2026, quarter=1,
                                           file=SimpleUploadedFile('ember.pdf', PDF),
                                           submitted_date=date.today())
        self.staff.post(reverse('registrar:delete_ember_letter', args=[ember.id]))
        note = only(self.user, 'document_removed')
        self.assertEqual(note.title, 'Ember Letter (2026 Q1) removed')
        self.assertIn('Maria Santos (Registrar) removed your Ember Letter for 2026 Q1.', note.message)

    def test_registrar_archiving_and_restoring_record_notifies_clergy(self):
        ClergyRecord.objects.filter(id=self.record.id).update(status='Retired')
        self.staff.post(reverse('registrar:archive_record', args=[self.record.id]))
        note = only(self.user, 'record_archived')
        self.assertEqual(note.title, 'Record archived')
        self.assertIn('Maria Santos (Registrar) moved your clergy record to the archive.', note.message)

        self.staff.post(reverse('registrar:restore_record', args=[self.record.id]))
        restored = only(self.user, 'record_restored')
        self.assertEqual(restored.title, 'Record restored')
        self.assertIn('Maria Santos (Registrar) restored your clergy record from the archive.', restored.message)


class ReminderTests(TestCase):
    TODAY = date(2026, 10, 10)

    def setUp(self):
        self.user, self.record = make_clergy(status='Active')

    def sync(self, today=None):
        notifications.sync_reminders(self.user, self.record, today=today or self.TODAY)

    def keys(self):
        return sorted(Notification.objects.values_list('dedupe_key', flat=True))

    def add_document(self, name='Medical Exam Result', submitted=date(2025, 11, 1), **type_fields):
        document_type = DocumentType.objects.create(name=name, validity_period_months=12, **type_fields)
        return Document.objects.create(record=self.record, document_type=document_type, file='x.pdf', submitted_date=submitted)

    def test_a_contract_ending_soon_is_reminded_once(self):
        self.record.contract_end = self.TODAY + timedelta(days=20)
        self.sync()
        self.sync()
        note = only(self.user)
        self.assertEqual((note.kind, note.dedupe_key), ('contract_reminder', 'contract:2026-10-30:30'))
        self.assertIn('ends in 20 days, on Oct 30, 2026', note.message)

    def test_only_the_most_urgent_reminder_is_made_when_several_are_due(self):
        self.record.contract_end = self.TODAY + timedelta(days=5)
        self.sync()
        self.assertEqual(self.keys(), ['contract:2026-10-15:7'])

    def test_each_milestone_gets_its_own_reminder(self):
        self.record.contract_end = date(2026, 12, 31)
        for today in (date(2026, 10, 10), date(2026, 12, 10), date(2026, 12, 28), date(2027, 1, 2)):
            self.sync(today)
        self.assertEqual(self.keys(), ['contract:2026-12-31:30', 'contract:2026-12-31:7',
                                       'contract:2026-12-31:90', 'contract:2026-12-31:ended'])
        self.assertEqual(Notification.objects.get(dedupe_key__endswith='ended').kind, 'contract_ended')

    def test_a_new_contract_date_gets_reminders_of_its_own(self):
        self.record.contract_end = self.TODAY + timedelta(days=20)
        self.sync()
        self.record.contract_end = self.TODAY + timedelta(days=25)
        self.sync()
        self.assertEqual(len(self.keys()), 2)

    def test_no_contract_reminders_without_an_end_date_or_once_retired(self):
        self.sync()
        self.record.status, self.record.contract_end = 'Retired', self.TODAY - timedelta(days=3)
        self.sync()
        self.assertFalse(Notification.objects.exists())

    def test_documents_are_reminded_before_and_after_they_expire(self):
        self.add_document()                 # valid until Nov 1, 2026
        self.sync(date(2026, 9, 1))         # two months out: nothing yet
        self.assertFalse(Notification.objects.exists())
        self.sync(date(2026, 10, 22))
        self.sync(date(2026, 10, 25))       # still the same reminder
        self.sync(date(2026, 11, 5))
        soon, expired = Notification.objects.order_by('created_at', 'id')
        self.assertEqual((soon.kind, soon.title), ('document_expiring', 'Medical Exam Result expires soon'))
        self.assertIn('expires in 10 days, on Nov 1, 2026. Upload a renewed copy', soon.message)
        self.assertEqual((expired.kind, expired.title), ('document_expired', 'Medical Exam Result expired'))

    def test_a_renewed_document_is_reminded_again(self):
        document = self.add_document()
        self.sync(date(2026, 10, 22))
        document.submitted_date = date(2026, 10, 30)
        document.save()
        self.sync(date(2027, 10, 20))
        self.assertEqual(Notification.objects.filter(kind='document_expiring').count(), 2)

    def test_registrar_provided_documents_say_who_renews_them(self):
        self.add_document(name='Police Clearance', uploaded_by='registrar')
        self.sync(date(2026, 10, 22))
        self.assertIn('The Registrar provides this document.', only(self.user).message)

    def test_nothing_for_an_unlinked_or_registrar_account(self):
        self.record.contract_end = self.TODAY
        notifications.sync_reminders(self.user, None, today=self.TODAY)
        staff = User.objects.create_user('reg', is_staff=True)
        notifications.sync_reminders(staff, self.record, today=self.TODAY)
        self.assertFalse(Notification.objects.exists())


class PageTests(SecurityTestCase):
    def setUp(self):
        super().setUp()
        DocumentType.objects.all().delete()

    def add(self, title, kind='password_changed', user=None, read=False):
        return Notification.objects.create(user=user or self.user, kind=kind, title=title,
                                           read_at=timezone.now() if read else None)

    def test_new_ones_are_flagged_once_and_then_marked_read(self):
        self.add('Old news', read=True)
        self.add('Fresh news')
        page = self.client.get(PAGE)
        self.assertContains(page, 'Old news')
        self.assertContains(page, 'Fresh news')
        self.assertContains(page, '>New</span>', count=1)
        self.assertNotContains(page, 'unread">')                 # the badge is gone while you look
        self.assertFalse(Notification.objects.filter(read_at__isnull=True).exists())
        self.assertNotContains(self.client.get(PAGE), '>New</span>')

    def test_only_your_own_are_shown_or_marked_read(self):
        other = User.objects.create_user('other', password=PASSWORD)
        self.add("Someone else's news", user=other)
        self.assertNotContains(self.client.get(PAGE), 'Someone else')
        self.assertIsNone(Notification.objects.get(user=other).read_at)

    def test_tabs_show_one_kind_and_leave_the_rest_unread(self):
        self.add('A sign-in', kind='new_sign_in')
        self.add('A document', kind='document_expired')
        page = self.client.get(PAGE + '?tab=documents')
        self.assertContains(page, 'A document')
        self.assertNotContains(page, 'A sign-in')
        self.assertIsNone(Notification.objects.get(kind='new_sign_in').read_at)
        self.assertContains(page, 'Mark all as read')
        self.assertContains(self.client.get(PAGE + '?tab=nonsense'), 'A sign-in')   # falls back to All

    def test_twenty_to_a_page_and_mark_all_as_read(self):
        Notification.objects.bulk_create(
            [Notification(user=self.user, kind='password_changed', title=f'Item {i:02d}') for i in range(25)])
        page = self.client.get(PAGE)
        self.assertContains(page, 'Page 1 of 2')
        self.assertContains(page, 'Item 24')
        self.assertNotContains(page, 'Item 04')
        self.assertEqual(Notification.objects.filter(read_at__isnull=True).count(), 5)
        self.assertContains(page, 'Mark all as read')
        self.client.post(PAGE, {'action': 'mark_all_read'})
        self.assertFalse(Notification.objects.filter(read_at__isnull=True).exists())

    def test_the_badge_counts_unread_and_stops_at_99(self):
        Notification.objects.bulk_create([Notification(user=self.user, kind='password_changed', title='x') for _ in range(3)])
        self.assertContains(self.client.get(reverse('clergy:dashboard')), 'title="3 unread">3</span>')
        Notification.objects.bulk_create([Notification(user=self.user, kind='password_changed', title='x') for _ in range(100)])
        self.assertContains(self.client.get(reverse('clergy:dashboard')), '>99+</span>')

    def test_opening_the_dashboard_makes_any_reminder_that_is_due(self):
        ClergyRecord.objects.filter(pk=self.record.pk).update(contract_end=date.today() + timedelta(days=3))
        page = self.client.get(reverse('clergy:dashboard'))
        self.assertEqual(only(self.user).kind, 'contract_reminder')
        self.assertContains(page, 'title="1 unread"')

    def test_needs_attention_lists_what_needs_doing_now(self):
        medical = DocumentType.objects.create(name='Medical Exam Result', validity_period_months=12)
        DocumentType.objects.create(name='Baptismal Certificate')
        Document.objects.create(record=self.record, document_type=medical, file='m.pdf',
                                submitted_date=date.today() - timedelta(days=400))
        ClergyRecord.objects.filter(pk=self.record.pk).update(contract_end=date.today() + timedelta(days=10))
        page = self.client.get(PAGE)
        self.assertContains(page, '1 document expired: Medical Exam Result.')
        self.assertContains(page, '1 required document still to submit.')
        self.assertContains(page, 'Your contract ends in 10 days')
        self.assertNotContains(page, "You're all caught up")

    def test_all_caught_up_with_the_true_verification_status(self):
        page = self.client.get(PAGE)
        self.assertContains(page, "You're all caught up")
        self.assertNotContains(page, 'Awaiting verification')
        self.assertNotContains(page, 'Record verified')

    def test_an_unlinked_account_is_told_so(self):
        ClergyProfile.objects.filter(user=self.user).update(record=None)
        page = self.client.get(PAGE)
        self.assertContains(page, 'not linked to an official clergy record')
        self.assertNotContains(page, 'Awaiting verification')

    def test_registrar_accounts_cant_open_it(self):
        registrar = activate(User.objects.create_user('reg', password=PASSWORD, is_staff=True))
        client = Client()
        client.force_login(registrar)
        self.assertEqual(client.get(PAGE).status_code, 302)


class HelperTests(TestCase):
    def test_times_are_short_and_relative(self):
        now = timezone.now()
        self.assertEqual(notifications.ago(now - timedelta(seconds=20), now), 'Just now')
        self.assertEqual(notifications.ago(now - timedelta(hours=2, minutes=5), now), '2\xa0hours ago')
        self.assertEqual(notifications.ago(now - timedelta(days=3), now), '3\xa0days ago')

    def test_long_text_is_cut_to_fit(self):
        note = notifications.notify(User.objects.create_user('fr'), 'record_updated', 'T' * 200, 'm' * 900)
        note.refresh_from_db()
        self.assertEqual(len(note.title), 120)
        self.assertEqual(len(note.message), 500)
        self.assertTrue(note.message.endswith('…'))
