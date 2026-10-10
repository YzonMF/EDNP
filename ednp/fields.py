"""A text field whose value is stored encrypted (see ednp/encryption.py).

Used like a CharField: forms, templates and the ORM see the plain value. Only the stored
form is encrypted, and only when DATA_ENCRYPTION_KEY is set; with no key it stores plain
text exactly as a CharField did. A value stored before encryption was switched on is read
as it is until DATA_ENCRYPTION_REQUIRED is turned on.

Because the stored form is different every time, an encrypted field can't be searched or
sorted by its value. Looking one up with a filter raises an error rather than quietly
finding nothing.
"""

from django import forms
from django.core.exceptions import FieldError, ValidationError
from django.db import models

from . import encryption


class EncryptedCharField(models.TextField):
    description = 'Text, stored encrypted'

    def __init__(self, *args, plain_max_length=None, **kwargs):
        # The stored token is much longer than the value, so the column is a TextField; the
        # limit people see is applied by the form field instead.
        self.plain_max_length = plain_max_length
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        if self.plain_max_length is not None:
            kwargs['plain_max_length'] = self.plain_max_length
        return name, path, args, kwargs

    def from_db_value(self, value, expression, connection):
        if not value:
            return value
        if encryption.is_encrypted_text(value):
            return encryption.decrypt_text(value)
        if encryption.required():
            raise encryption.EncryptionError('A stored value is unencrypted, which DATA_ENCRYPTION_REQUIRED does not allow.')
        return value

    def get_prep_value(self, value):
        if value is None:
            return None
        value = str(value)
        # An empty value is stored as empty: nothing to hide, and a blank stays easy to tell.
        if not value or not encryption.configured() or encryption.is_encrypted_text(value):
            return value
        return encryption.encrypt_text(value)

    def to_python(self, value):
        value = super().to_python(value)
        if encryption.is_encrypted_text(value):
            # What a backup file holds: turn it back into the real value (and refuse a made-up one).
            try:
                return encryption.decrypt_text(value)
            except encryption.EncryptionError as exc:
                raise ValidationError('Enter a valid value.') from exc
        return value

    def value_to_string(self, obj):
        # Used when the database is dumped to a backup file: the backup holds the encrypted form.
        return self.get_prep_value(self.value_from_object(obj))

    def formfield(self, **kwargs):
        defaults = {'form_class': forms.CharField, 'max_length': self.plain_max_length, 'widget': forms.TextInput}
        defaults.update(kwargs)
        return super().formfield(**defaults)

    def get_lookup(self, lookup_name):
        if lookup_name != 'isnull':
            raise FieldError(f'{self.name} is stored encrypted, so it can only be filtered with __isnull '
                             f'(not __{lookup_name}).')
        return super().get_lookup(lookup_name)
