from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts import audit
from registrar.provisioning import reset_lost_phone


class Command(BaseCommand):
    help = (
        'Reset an account after its owner has been identified in person: forgets '
        'their number, signs out every device and session, and prints a new '
        'temporary password. The way to recover the only registrar, who has '
        'nobody else in the system to reset them.'
    )

    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument('--note', default='', help='Optional note kept in the account activity log.')

    def handle(self, *args, **options):
        User = get_user_model()
        try:
            user = User.objects.get(username=options['username'])
        except User.DoesNotExist:
            raise CommandError(f"No account with the username {options['username']!r}.")

        with transaction.atomic():
            password = reset_lost_phone(user)
            audit.record(None, user, 'lost_phone_reset', options['note'])

        self.stdout.write(self.style.SUCCESS(f'Reset {user.username}.'))
        self.stdout.write(f'Temporary password: {password}')
        self.stdout.write(f'It works until {user.security.setup_expires_at:%b %d, %Y}. They will register a new number and choose their own password when they sign in.')
        self.stdout.write(self.style.WARNING('Hand this over in person. It is shown only now and is not stored.'))
