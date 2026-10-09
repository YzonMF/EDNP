"""First-login setup: what a user still has to do before the portal opens up.

Derived entirely from stored data (never from a session flag), so closing the
tab halfway just resumes at the right step next time, and nobody can skip a
step by crafting a request.
"""

from django.conf import settings

from .models import UserSecurity

PHONE = 'phone'        # register / re-verify a mobile number by text code
PASSWORD = 'password'  # replace the registrar-issued password with their own


def pending_stage(user):
    """The next setup step this user must complete, or None if they're done."""
    security = UserSecurity.objects.filter(user=user).first()
    if security is None or not security.phone or not security.phone_verified_at:
        return PHONE
    # A number "verified" in test mode (code shown on screen) proves nothing;
    # once real SMS is on, it has to be verified again for real.
    if settings.SMS_BACKEND != 'test' and security.phone_verified_via != 'sms':
        return PHONE
    if security.password_changed_at is None:
        return PASSWORD
    return None
