from django.conf import settings
from django.db import models


class ActiveSession(models.Model):
    """The one session allowed to be signed in as a given user. A new login
    overwrites session_key, which makes every older session for that user
    stale — SingleSessionMiddleware signs it out on its next request. One row
    per user, looked up by the user_id unique index, so checking and updating
    stay O(1) no matter how many users or sessions exist."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='active_session')
    session_key = models.CharField(max_length=40)
    updated_at = models.DateTimeField(auto_now=True)


class UserSecurity(models.Model):
    """Per-user security state that Django's User doesn't carry: whether the
    account has finished first-login setup, the verified phone number, and the
    window in which a registrar-issued temporary password still works.

    Rows are created when an account is provisioned. A user with no row (an
    account that predates this feature) is treated as not yet activated."""

    VERIFIED_VIA_CHOICES = [('sms', 'SMS'), ('test', 'Test mode')]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='security')

    # A temporary password stops working after this moment unless the account
    # has been activated by then. Null once activated (it no longer applies).
    setup_expires_at = models.DateTimeField(blank=True, null=True)
    activated_at = models.DateTimeField(blank=True, null=True)
    # Set when the user replaces the registrar-issued (or pre-feature) password
    # with one of their own. Activation needs both this and a verified phone.
    password_changed_at = models.DateTimeField(blank=True, null=True)

    # Stored normalized as +639XXXXXXXXX. Unique so one number can never
    # belong to two accounts (a shared number would let one person reset
    # another's password through the forgot-password OTP). NULL is allowed
    # many times over; only real numbers collide.
    phone = models.CharField(max_length=13, unique=True, blank=True, null=True)
    phone_verified_at = models.DateTimeField(blank=True, null=True)
    # 'test' numbers were "verified" by a code shown on screen, which proves
    # nothing — they must be re-verified once real SMS is switched on.
    phone_verified_via = models.CharField(max_length=4, choices=VERIFIED_VIA_CHOICES, blank=True)

    class Meta:
        verbose_name_plural = 'user security'

    @property
    def is_activated(self):
        return self.activated_at is not None


class OtpChallenge(models.Model):
    """One text-message code sent to one phone number for one purpose. Only a
    keyed hash of the code is stored, so a database leak doesn't hand out
    live codes. A code stops working when it expires, when it is used, when a
    newer one replaces it, or after too many wrong guesses."""

    PURPOSE_CHOICES = [
        ('phone_verify', 'Verify phone number'),
        ('device', 'Sign in on a new device'),
        ('phone_change', 'Change phone number'),
        ('password_reset', 'Reset password'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='otp_challenges')
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES)
    target = models.CharField(max_length=13)  # the +639… number the code was sent to
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'purpose', 'created_at']),
            models.Index(fields=['target', 'created_at']),
        ]


class TrustedDevice(models.Model):
    """A browser the user has already proven they control (by receiving a code
    on their phone), so it isn't asked again on each sign-in. The browser holds
    a random token in a cookie; only its hash is stored here. Revoking a device
    is deleting its row."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='trusted_devices')
    token_hash = models.CharField(max_length=64, unique=True)
    label = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ['-last_seen_at']


class AccountAuditLog(models.Model):
    """Who did what to whose account, for accountability. Names are copied in
    at the time (and the user links are SET_NULL) so an entry stays readable
    and truthful even if either account is later renamed or deleted. Taking a
    backup or archiving a report isn't done to any account, so those entries
    have no target (the report is named in the note)."""

    ACTION_CHOICES = [
        ('account_created', 'Account created'),
        ('password_reprinted', 'Temporary password reissued'),
        ('lost_phone_reset', 'Account reset (lost phone)'),
        ('phone_changed', 'Mobile number changed'),
        ('username_changed', 'Username changed'),
        ('name_changed', 'Name changed'),
        ('password_reset', 'Password reset (forgot password)'),
        ('database_backup', 'Database backup downloaded'),
        ('archive_downloaded', 'Documents archive downloaded'),
        ('archive_deleted', 'Saved documents archive deleted'),
        ('report_archived', 'Report archived'),
        ('report_restored', 'Report restored'),
        ('report_deleted', 'Report deleted'),
        ('report_expired', 'Report deleted after 30 days'),
        ('record_archived', 'Clergy record archived'),
        ('record_restored', 'Clergy record restored'),
        ('ember_uploaded', 'Ember letter uploaded'),
        ('ember_replaced', 'Ember letter replaced'),
        ('ember_deleted', 'Ember letter removed'),
    ]

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name='+')
    actor_name = models.CharField(max_length=150)
    target = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name='+')
    target_name = models.CharField(max_length=150)
    action = models.CharField(max_length=24, choices=ACTION_CHOICES)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']


class ResetRequest(models.Model):
    """One Forgot-password request, for rate-limiting only. Written for every
    number typed, registered or not, so that a limit looks the same either way;
    only a hash of the number is kept."""

    number_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['number_hash', 'created_at']), models.Index(fields=['created_at'])]


class LoginThrottle(models.Model):
    """The wrong-password count behind the sign-in lockout (ednp/login_throttle.py):
    one row per account, or per unknown name, being counted. In the database rather
    than the cache so every gunicorn worker sees the same count and a restart doesn't
    wipe it. Only a keyed hash of what is being counted is kept, so this table is not
    a list of the usernames people have typed (some type their password there)."""

    key_hash = models.CharField(max_length=64, unique=True)
    failures = models.PositiveIntegerField(default=0)
    # The latest wrong password. The count is forgotten once LOCKOUT_SECONDS have passed since it.
    updated_at = models.DateTimeField()

    class Meta:
        indexes = [models.Index(fields=['updated_at'])]
