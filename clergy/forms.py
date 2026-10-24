from django import forms

from registrar.models import ClergyRecord, ClergyDeployment

from .models import ClergyProfile


class ClergyProfileForm(forms.ModelForm):
    """The clergy-only fields the Registrar never tracks."""

    class Meta:
        model = ClergyProfile
        fields = [
            'place_of_birth', 'civil_status',
            'baptism_place', 'confirmation_place',
            'education', 'other_information',
            'marriage_date', 'marriage_place', 'spouse_name', 'spouse_birth_date', 'children_names',
            'sss_number', 'philhealth_number', 'pagibig_number',
        ]
        widgets = {
            'marriage_date': forms.DateInput(attrs={'type': 'date'}),
            'spouse_birth_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        civil_status = cleaned_data.get('civil_status')
        if civil_status == 'Single':
            # Single clergy cannot have spouse or marriage records
            cleaned_data['marriage_date'] = None
            cleaned_data['marriage_place'] = ''
            cleaned_data['spouse_name'] = ''
            cleaned_data['spouse_birth_date'] = None
            cleaned_data['children_names'] = ''
        return cleaned_data


class ClergyRecordSelfServiceForm(forms.ModelForm):
    """The subset of ClergyRecord fields a clergy member may edit about
    themselves from My Profile — includes personal information, Holy Orders &
    Ordination history; excludes assignment, status, rank, and
    contract/retirement dates which stay Registrar-controlled."""

    class Meta:
        model = ClergyRecord
        # No 'phone': the contact number is the verified one on the account and
        # only changes through a code sent to the new number.
        fields = [
            'name', 'gender', 'date_of_birth', 'baptism_date', 'confirmation_date',
            'email', 'address',
            'diaconate_date', 'diaconate_place', 'ordination_date', 'ordination_place',
            'holy_orders_date', 'bishop_name', 'consecration_date',
        ]
        widgets = {
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
            'baptism_date': forms.DateInput(attrs={'type': 'date'}),
            'confirmation_date': forms.DateInput(attrs={'type': 'date'}),
            'diaconate_date': forms.DateInput(attrs={'type': 'date'}),
            'ordination_date': forms.DateInput(attrs={'type': 'date'}),
            'holy_orders_date': forms.DateInput(attrs={'type': 'date'}),
            'consecration_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        rank = self.instance.rank if self.instance else 'deacon'
        if rank == 'postulancy':
            cleaned_data['diaconate_date'] = None
            cleaned_data['diaconate_place'] = ''
            cleaned_data['ordination_date'] = None
            cleaned_data['ordination_place'] = ''
            cleaned_data['consecration_date'] = None
            cleaned_data['holy_orders_date'] = None
        elif rank == 'deacon':
            cleaned_data['ordination_date'] = None
            cleaned_data['ordination_place'] = ''
            cleaned_data['consecration_date'] = None
        elif rank == 'priest':
            cleaned_data['consecration_date'] = None

        diaconate = cleaned_data.get('diaconate_date')
        ordination = cleaned_data.get('ordination_date')
        consecration = cleaned_data.get('consecration_date')
        if diaconate and ordination and diaconate > ordination:
            self.add_error('ordination_date', 'Priesthood ordination date cannot be earlier than Diaconate ordination date.')
        if ordination and consecration and ordination > consecration:
            self.add_error('consecration_date', 'Bishop consecration date cannot be earlier than Priesthood ordination date.')
        if diaconate and consecration and diaconate > consecration:
            self.add_error('consecration_date', 'Bishop consecration date cannot be earlier than Diaconate ordination date.')
        return cleaned_data
