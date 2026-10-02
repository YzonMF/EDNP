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
