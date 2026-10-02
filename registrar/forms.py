import os

from django import forms

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
        fields = ['name', 'date_of_birth', 'gender', 'baptism_date', 'confirmation_date',
                  'phone', 'email', 'address', 'deanery', 'assignment', 'status', 'leave_reason', 'rank',
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


MAX_DOCUMENT_BYTES = 10 * 1024 * 1024  # 10 MB

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
        if upload.size > MAX_DOCUMENT_BYTES:
            raise forms.ValidationError('File is too large — the maximum is 10 MB.')
        extension = os.path.splitext(upload.name)[1].lower()
        signatures = DOCUMENT_SIGNATURES.get(extension)
        if not signatures:
            raise forms.ValidationError('Only PDF, JPG and PNG files are allowed.')
        header = upload.read(16)
        upload.seek(0)
        if not header.startswith(signatures):
            raise forms.ValidationError('That file does not look like a real PDF, JPG or PNG.')
        return upload


def first_error(form):
    """The first validation message on a form, for showing in a flash message."""
    for errors in form.errors.values():
        return errors[0]
    return None
