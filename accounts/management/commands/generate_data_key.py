from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Prints a new key for DATA_ENCRYPTION_KEY, the key that encrypts uploaded documents and government ID numbers.'

    # While the key in the settings is broken, the startup check stops every command. This is the way
    # out of that, so it must not be stopped by the very check it exists to fix.
    requires_system_checks = []

    def handle(self, *args, **options):
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            raise CommandError('The "cryptography" package is not installed. Run: pip install -r requirements.txt')

        # The key alone goes to stdout (so it can be piped or copied cleanly); the advice goes to stderr.
        self.stdout.write(Fernet.generate_key().decode())
        self.stderr.write(self.style.WARNING(
            '\nThe key is 44 characters and ends with "=": copy all of it, on one line.\n'
            'Keep this key safe. Anything encrypted with it can NEVER be recovered without it, '
            'including the database and documents backups.\n'
            '  1. Save a copy somewhere that is not this server (a password manager, plus a printed copy in a safe place).\n'
            '  2. Set it as DATA_ENCRYPTION_KEY where the site runs (Railway variables, and .env locally). Never commit it.\n'
            '  3. Then run: python manage.py encrypt_existing_data --dry-run\n'
            'To replace a key later, put the new one FIRST and keep the old one after a comma until '
            '`encrypt_existing_data --rotate` has finished.'
        ))
