# Generated on 2026-10-24

from django.db import migrations


def add_baptismal_certificate(apps, schema_editor):
    DocumentType = apps.get_model('registrar', 'DocumentType')
    DocumentType.objects.update_or_create(
        name='Baptismal Certificate',
        defaults={
            'required_from_rank': 'none',
            'uploaded_by': 'clergy',
            'is_required': True,
            'requires_upload': True,
        }
    )


def remove_baptismal_certificate(apps, schema_editor):
    DocumentType = apps.get_model('registrar', 'DocumentType')
    DocumentType.objects.filter(name='Baptismal Certificate').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('registrar', '0018_alter_clergydeployment_options'),
    ]

    operations = [
        migrations.RunPython(add_baptismal_certificate, remove_baptismal_certificate),
    ]

