from django import forms

from registrar.models import ClergyRecord

from .models import ClergyProfile


class ClergyProfileForm(forms.ModelForm):
    """The clergy-only fields the Registrar never tracks."""

    class Meta:
        model = ClergyProfile
        fields = [
            'place_of_birth', 'civil_status',
            'baptism_place', 'confirmation_place',
            'education', 'other_information',
            'marriage_date', 'spouse_name', 'children_names',
            'sss_number', 'philhealth_number', 'pagibig_number',
        ]
        widgets = {
            'marriage_date': forms.DateInput(attrs={'type': 'date'}),
        }


class ClergyRecordSelfServiceForm(forms.ModelForm):
    """The subset of ClergyRecord fields a clergy member may edit about
    themselves from My Profile — deliberately excludes assignment, status,
    rank, contract/retirement dates, and the whole Ordination/Bishop history,
    which stay Registrar-controlled (verified church events, not self-reported)."""

    class Meta:
        model = ClergyRecord
        fields = ['name', 'gender', 'date_of_birth', 'baptism_date', 'confirmation_date',
                  'phone', 'email', 'address']
        widgets = {
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
            'baptism_date': forms.DateInput(attrs={'type': 'date'}),
            'confirmation_date': forms.DateInput(attrs={'type': 'date'}),
        }
