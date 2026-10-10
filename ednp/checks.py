"""Startup checks for encryption at rest, so a bad setting stops the site starting (and
`manage.py migrate` in the container) instead of surfacing the first time someone opens a
document. Registered from accounts/apps.py."""

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

from . import encryption


@register(Tags.security)
def encryption_settings(app_configs, **kwargs):
    if encryption.configured():
        try:
            encryption._fernets(encryption.keys())
        except encryption.EncryptionError as exc:
            return [Error(str(exc), id='ednp.E001')]
    elif settings.DATA_ENCRYPTION_REQUIRED:
        return [Error('DATA_ENCRYPTION_REQUIRED is on but DATA_ENCRYPTION_KEY is not set, so nothing '
                      'already encrypted could be read.', id='ednp.E002')]
    return []


@register(Tags.security, deploy=True)
def encryption_inactive(app_configs, **kwargs):
    if encryption.configured():
        return []
    return [Warning('Uploaded documents and government ID numbers are stored unencrypted. '
                    'Set DATA_ENCRYPTION_KEY to encrypt them (see .env.example).', id='ednp.W001')]
