from django.db import migrations

import ednp.fields

ID_COLUMNS = ('sss_number', 'philhealth_number', 'pagibig_number')


def refuse_to_go_back_while_encrypted(apps, schema_editor):
    """Runs first when this migration is reversed. Going back shrinks the three columns to 30
    characters, which would cut off every ID number that is stored encrypted (MySQL does it
    silently unless strict mode is on), so it is refused while any exist."""
    with schema_editor.connection.cursor() as cursor:
        for column in ID_COLUMNS:
            cursor.execute(f'SELECT COUNT(*) FROM clergy_profiles WHERE {column} LIKE %s', ['enc1:%'])
            if cursor.fetchone()[0]:
                raise RuntimeError(
                    'Some government ID numbers are stored encrypted, and going back to the old 30-character '
                    'columns would destroy them. Do not reverse this migration while DATA_ENCRYPTION_KEY has '
                    'been used; restore from a backup instead if you really must.')


class Migration(migrations.Migration):

    dependencies = [
        ('clergy', '0004_remove_clergyprofile_ordination_place_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='clergyprofile',
            name='pagibig_number',
            field=ednp.fields.EncryptedCharField(blank=True, plain_max_length=30, verbose_name='Pag-IBIG Number'),
        ),
        migrations.AlterField(
            model_name='clergyprofile',
            name='philhealth_number',
            field=ednp.fields.EncryptedCharField(blank=True, plain_max_length=30, verbose_name='PhilHealth Number'),
        ),
        migrations.AlterField(
            model_name='clergyprofile',
            name='sss_number',
            field=ednp.fields.EncryptedCharField(blank=True, plain_max_length=30, verbose_name='SSS Number'),
        ),
        # Last going forward, so first going back: a guard that does nothing when applied.
        migrations.RunPython(migrations.RunPython.noop, reverse_code=refuse_to_go_back_while_encrypted),
    ]
