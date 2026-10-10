from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = 'accounts'

    def ready(self):
        from . import signals  # noqa: F401
        from ednp import checks  # noqa: F401  (registers the encryption startup checks)
