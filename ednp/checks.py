"""Startup checks for encryption at rest and for the bucket, so a bad setting stops the site
starting (and `manage.py migrate` in the container) instead of surfacing the first time someone
uploads or opens a document. Registered from accounts/apps.py."""

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


BUCKET_VARIABLES = {
    'endpoint_url': 'BUCKET_ENDPOINT',
    'access_key': 'BUCKET_ACCESS_KEY_ID',
    'secret_key': 'BUCKET_SECRET_ACCESS_KEY',
}


@register(Tags.security)
def bucket_settings(app_configs, **kwargs):
    if not settings.BUCKET_NAME:
        return []
    errors = []
    missing = [variable for option, variable in BUCKET_VARIABLES.items() if not settings.BUCKET_OPTIONS.get(option)]
    if missing:
        errors.append(Error(f'BUCKET_NAME is set but {", ".join(missing)} {"is" if len(missing) == 1 else "are"} not, '
                            f'so stored files could not be saved or read. See resources/storage-bucket.md.', id='ednp.E003'))
    if settings.BUCKET_OPTIONS.get('addressing_style') not in ('virtual', 'path'):
        errors.append(Error("BUCKET_URL_STYLE must be 'virtual' or 'path'.", id='ednp.E004'))
    try:
        import boto3  # noqa: F401
        import storages  # noqa: F401
    except ImportError:
        errors.append(Error('BUCKET_NAME is set but the bucket libraries are not installed '
                            '(pip install -r requirements.txt).', id='ednp.E005'))
    return errors


@register(Tags.security, deploy=True)
def files_on_local_disk(app_configs, **kwargs):
    if settings.BUCKET_NAME:
        return []
    return [Warning(f"Stored files are kept on this server's own disk ({settings.MEDIA_ROOT}). On a host that "
                    'replaces the disk at each redeploy (Railway, without a volume there) they are lost at the next '
                    'one. Set BUCKET_NAME to keep them in a bucket (see resources/storage-bucket.md).', id='ednp.W002')]
