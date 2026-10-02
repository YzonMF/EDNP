from django.conf import settings
from django.db import models

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
    spouse_name = models.CharField(max_length=150, blank=True)
    children_names = models.TextField(blank=True)

    sss_number = models.CharField('SSS Number', max_length=30, blank=True)
    philhealth_number = models.CharField('PhilHealth Number', max_length=30, blank=True)
    pagibig_number = models.CharField('Pag-IBIG Number', max_length=30, blank=True)

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
