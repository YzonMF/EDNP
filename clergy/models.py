from datetime import date
from django.conf import settings
from django.db import models

from ednp.fields import EncryptedCharField
from registrar.models import ClergyRecord


class ClergyProfile(models.Model):
    """The clergy member's self-service profile. Login credentials live on the
    linked Django User (auth_user) — this table only holds data the registrar
    never tracks (place/civil-status details, education, marriage/family,
    government IDs). Name, gender, birth/baptism/confirmation dates, contact
    info, address, ordination history, bishop/consecration and previous
    assignment are NOT duplicated here — both this portal and the Registrar
    read and write those directly on the linked ClergyRecord (`record`), so
    an edit from either side is immediately visible to the other instead of
    the two views silently drifting apart. Medical/psychiatric clearance
    status isn't stored here either — it's tracked as ordinary DocumentType
    entries on the record, so expiry is computed automatically instead of
    needing a separate manually-maintained "valid until" field."""

    CIVIL_STATUS_CHOICES = [('Single', 'Single'), ('Married', 'Married'), ('Widowed', 'Widowed')]
    VERIFICATION_CHOICES = [('Verified', 'Verified'), ('For Verification', 'For Verification')]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='clergy_profile')
    record = models.OneToOneField(ClergyRecord, on_delete=models.SET_NULL, blank=True, null=True, related_name='profile')

    place_of_birth = models.CharField(max_length=150, blank=True)
    civil_status = models.CharField(max_length=10, choices=CIVIL_STATUS_CHOICES, blank=True)

    baptism_place = models.CharField(max_length=150, blank=True)
    confirmation_place = models.CharField(max_length=150, blank=True)

    education = models.TextField(blank=True)
    other_information = models.TextField(blank=True)

    marriage_date = models.DateField(blank=True, null=True)
    marriage_place = models.CharField(max_length=150, blank=True)
    spouse_name = models.CharField(max_length=150, blank=True)
    spouse_birth_date = models.DateField(blank=True, null=True)
    children_names = models.TextField(blank=True)

    # Government ID numbers: stored encrypted once DATA_ENCRYPTION_KEY is set (ednp/fields.py).
    sss_number = EncryptedCharField('SSS Number', plain_max_length=30, blank=True)
    philhealth_number = EncryptedCharField('PhilHealth Number', plain_max_length=30, blank=True)
    pagibig_number = EncryptedCharField('Pag-IBIG Number', plain_max_length=30, blank=True)

    diocese = models.CharField(max_length=150, blank=True, default='Episcopal Diocese of Northern Philippines')
    verification = models.CharField(max_length=20, choices=VERIFICATION_CHOICES, default='For Verification')
    verified_at = models.DateField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'clergy_profiles'

    @property
    def full_name(self):
        return self.record.name if self.record else self.user.username

    @property
    def initials(self):
        words = [w for w in self.full_name.split() if w]
        return "".join(w[0] for w in words[:2]).upper() or "CU"

    def __str__(self):
        return self.full_name


# kind -> (filter tab it appears under, Font Awesome icon, colour). The tabs are
# 'security' (sign-ins and account changes), 'record' (profile, record and
# contract) and 'documents'. Kept here rather than as field choices so a new
# kind doesn't need a migration.
class ClergyChild(models.Model):
    profile = models.ForeignKey(ClergyProfile, on_delete=models.CASCADE, related_name='children')
    name = models.CharField(max_length=150)
    birth_date = models.DateField(blank=True, null=True)
    gender = models.CharField(max_length=10, choices=[('Male', 'Male'), ('Female', 'Female')], blank=True)

    class Meta:
        db_table = 'clergy_children'
        ordering = ['birth_date', 'id']

    @property
    def age(self):
        if not self.birth_date:
            return None
        today = date.today()
        return today.year - self.birth_date.year - ((today.month, today.day) < (self.birth_date.month, self.birth_date.day))

    def __str__(self):
        return self.name


class ClergyEducation(models.Model):
    profile = models.ForeignKey(ClergyProfile, on_delete=models.CASCADE, related_name='educations')
    degree = models.CharField('Degree / Program', max_length=150)
    institution = models.CharField('Institution / Seminary / School', max_length=150)
    place = models.CharField('Place / Location', max_length=150, blank=True)
    year_graduated = models.PositiveSmallIntegerField('Year Graduated', blank=True, null=True)

    class Meta:
        db_table = 'clergy_educations'
        ordering = ['-year_graduated', '-id']

    def __str__(self):
        return f"{self.degree} - {self.institution}"


NOTIFICATION_KINDS = {
    'account_activated': ('security', 'fa-circle-check', 'green'),
    'password_changed': ('security', 'fa-key', 'blue'),
    'password_reset': ('security', 'fa-key', 'amber'),
    'username_changed': ('security', 'fa-user-pen', 'blue'),
    'phone_changed': ('security', 'fa-mobile-screen', 'blue'),
    'new_sign_in': ('security', 'fa-right-to-bracket', 'blue'),
    'device_removed': ('security', 'fa-laptop', 'blue'),
    'sign_in_locked': ('security', 'fa-lock', 'red'),
    'account_reset': ('security', 'fa-rotate-left', 'amber'),
    'profile_updated': ('record', 'fa-user', 'blue'),
    'record_updated': ('record', 'fa-id-badge', 'blue'),
    'deployment_added': ('record', 'fa-map-location-dot', 'blue'),
    'deployment_removed': ('record', 'fa-map-location-dot', 'amber'),
    'record_archived': ('record', 'fa-box-archive', 'amber'),
    'record_restored': ('record', 'fa-rotate-left', 'green'),
    'contract_reminder': ('record', 'fa-calendar-days', 'amber'),
    'contract_ended': ('record', 'fa-calendar-xmark', 'red'),
    'document_uploaded': ('documents', 'fa-file-arrow-up', 'green'),
    'ember_uploaded': ('documents', 'fa-file-arrow-up', 'green'),
    'document_removed': ('documents', 'fa-file-circle-xmark', 'amber'),
    'document_expiring': ('documents', 'fa-hourglass-half', 'amber'),
    'document_expired': ('documents', 'fa-file-circle-exclamation', 'red'),
}
_UNKNOWN_KIND = ('security', 'fa-bell', 'blue')


class Notification(models.Model):
    """One entry on a clergy member's Notifications page: something that happened
    to their account or record, or a reminder about a document or their contract.
    The wording is written in when it happens, as in the account audit log, so an
    entry stays true even if names or numbers change later. Only clergy accounts
    get these (see clergy/notifications.py); the registrar portal has no such page.

    `dedupe_key` is set only on reminders, so the same reminder is never made
    twice; MySQL lets any number of rows leave it NULL under the unique index."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    kind = models.CharField(max_length=30)
    title = models.CharField(max_length=120)
    message = models.CharField(max_length=500, blank=True)
    dedupe_key = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'clergy_notifications'
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['user', 'read_at'])]
        constraints = [models.UniqueConstraint(fields=['user', 'dedupe_key'], name='clergy_notification_once')]

    @classmethod
    def kinds_under(cls, tab):
        return [kind for kind, (kind_tab, _, _) in NOTIFICATION_KINDS.items() if kind_tab == tab]

    @property
    def tab(self):
        return NOTIFICATION_KINDS.get(self.kind, _UNKNOWN_KIND)[0]

    @property
    def icon(self):
        return NOTIFICATION_KINDS.get(self.kind, _UNKNOWN_KIND)[1]

    @property
    def tone(self):
        return NOTIFICATION_KINDS.get(self.kind, _UNKNOWN_KIND)[2]

    def __str__(self):
        return f'{self.user} - {self.title}'
