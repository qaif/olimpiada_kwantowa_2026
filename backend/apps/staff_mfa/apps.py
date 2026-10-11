from django.apps import AppConfig


class StaffMfaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.staff_mfa"
    label = "staff_mfa"
    verbose_name = "logowanie dwuskładnikowe personelu"

    def ready(self) -> None:
        # Ostrzeżenia startowe (rejestracja przez import) i sygnały wersji polityki – przegląd SEC-01.
        from . import checks, signals  # noqa: F401

        signals.connect()
