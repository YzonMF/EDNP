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
