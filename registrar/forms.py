import os
from datetime import date

from django import forms

from . import images
from .models import ClergyRecord, DocumentType


class ClergyRecordForm(forms.ModelForm):
    class Meta:
        model = ClergyRecord
        fields = ['name', 'deanery', 'assignment', 'status', 'leave_reason', 'rank', 'baptism_date', 'confirmation_date', 'contract_end']
        widgets = {
            'baptism_date': forms.DateInput(attrs={'type': 'date'}),
            'confirmation_date': forms.DateInput(attrs={'type': 'date'}),
            'contract_end': forms.DateInput(attrs={'type': 'date'}),
        }


class PersonalInfoForm(forms.ModelForm):
    class Meta:
        model = ClergyRecord
        # No 'phone': it is the number the clergy member verified by text code,
        # so the registrar can't overwrite it from here.
        fields = ['name', 'date_of_birth', 'gender', 'baptism_date', 'confirmation_date',
                  'email', 'address', 'deanery', 'assignment', 'status', 'leave_reason', 'rank',
                  'contract_end', 'retirement_date',
                  'diaconate_date', 'ordination_date', 'holy_orders_date', 'ordination_place',
                  'bishop_name', 'consecration_date', 'previous_assignment', 'service_history']
        widgets = {
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
            'baptism_date': forms.DateInput(attrs={'type': 'date'}),
            'confirmation_date': forms.DateInput(attrs={'type': 'date'}),
            'contract_end': forms.DateInput(attrs={'type': 'date'}),
            'retirement_date': forms.DateInput(attrs={'type': 'date'}),
            'diaconate_date': forms.DateInput(attrs={'type': 'date'}),
            'ordination_date': forms.DateInput(attrs={'type': 'date'}),
            'holy_orders_date': forms.DateInput(attrs={'type': 'date'}),
            'consecration_date': forms.DateInput(attrs={'type': 'date'}),
            'service_history': forms.Textarea(attrs={'rows': 3}),
        }


# What can be uploaded at all (a photo straight off a phone fits), and what may be stored: a PDF
# as it is, or a photo or scan once it has been shrunk (registrar/images.py), which almost
# always brings it well under.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_STORED_BYTES = 5 * 1024 * 1024   # 5 MB
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png'}

# extension -> the leading bytes a genuine file of that type starts with.
# Checking content as well as the extension stops e.g. an HTML/script file
# being uploaded as "scan.pdf". SVG is deliberately not allowed: it can carry
# script and would run in the site's origin if opened inline.
DOCUMENT_SIGNATURES = {
    '.pdf': (b'%PDF-',),
    '.jpg': (b'\xff\xd8\xff',),
    '.jpeg': (b'\xff\xd8\xff',),
    '.png': (b'\x89PNG\r\n\x1a\n',),
}


class DocumentUploadForm(forms.Form):
    document_type = forms.ModelChoiceField(queryset=DocumentType.objects.all())
    file = forms.FileField()
    notes = forms.CharField(max_length=255, required=False)

    def clean_file(self):
        upload = self.cleaned_data['file']
        if upload.size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError('File is too large — the maximum is 10 MB for a photo or scan, and 5 MB for a PDF.')
        extension = os.path.splitext(upload.name)[1].lower()
        signatures = DOCUMENT_SIGNATURES.get(extension)
        if not signatures:
            raise forms.ValidationError('Only PDF, JPG and PNG files are allowed.')
        header = upload.read(16)
        upload.seek(0)
        if not header.startswith(signatures):
            raise forms.ValidationError('That file does not look like a real PDF, JPG or PNG.')
        if extension in IMAGE_EXTENSIONS:
            upload = images.shrink(upload)
        if upload.size > MAX_STORED_BYTES:
            if extension == '.pdf':
                raise forms.ValidationError('That PDF is too large — the maximum is 5 MB. Scanning in black and white, '
                                            'or at 150–200 dpi, makes it smaller.')
            raise forms.ValidationError('That image is too large to store — the maximum is 5 MB. Please scan or '
                                        'photograph it at a lower resolution.')
        return upload


EMBER_MIN_YEAR = 1950
EMBER_MAX_BYTES = 5 * 1024 * 1024  # 5 MB


class EmberLetterUploadForm(forms.Form):
    year = forms.IntegerField()
    quarter = forms.IntegerField(min_value=1, max_value=4)
    file = forms.FileField()

    def clean_year(self):
        year = self.cleaned_data['year']
        current_year = date.today().year
        if year < EMBER_MIN_YEAR or year > current_year:
            raise forms.ValidationError(f'Year must be between {EMBER_MIN_YEAR} and {current_year}.')
        return year

    def clean_file(self):
        upload = self.cleaned_data['file']
        if upload.size > EMBER_MAX_BYTES:
            raise forms.ValidationError('File is too large — the maximum is 5 MB.')
        extension = os.path.splitext(upload.name)[1].lower()
        signatures = DOCUMENT_SIGNATURES.get(extension)
        if not signatures:
            raise forms.ValidationError('Only PDF, JPG and PNG files are allowed.')
        header = upload.read(16)
        upload.seek(0)
        if not header.startswith(signatures):
            raise forms.ValidationError('That file does not look like a real PDF, JPG or PNG.')
        if extension in IMAGE_EXTENSIONS:
            upload = images.shrink(upload)
        if upload.size > EMBER_MAX_BYTES:
            raise forms.ValidationError('File is too large — the maximum is 5 MB.')
        return upload


class ProfilePictureForm(forms.Form):
    profile_picture = forms.FileField()

    def clean_profile_picture(self):
        upload = self.cleaned_data['profile_picture']
        if upload.size > MAX_STORED_BYTES:
            raise forms.ValidationError('Image is too large — the maximum is 5 MB.')
        extension = os.path.splitext(upload.name)[1].lower()
        if extension not in IMAGE_EXTENSIONS:
            raise forms.ValidationError('Only JPG and PNG images are allowed for a profile picture.')
        signatures = DOCUMENT_SIGNATURES.get(extension)
        header = upload.read(16)
        upload.seek(0)
        if not signatures or not header.startswith(signatures):
            raise forms.ValidationError('That file does not look like a real JPG or PNG image.')
        upload = images.shrink_avatar(upload)
        if upload.size > MAX_STORED_BYTES:
            raise forms.ValidationError('That image is too large to store — the maximum is 5 MB.')
        return upload


def first_error(form):
    """The first validation message on a form, for showing in a flash message."""
    for errors in form.errors.values():
        return errors[0]
    return None
