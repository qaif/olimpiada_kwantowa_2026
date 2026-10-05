from django.apps import AppConfig


class ConsentGateConfig(AppConfig):
    """Bramka i ekran „Uzupełnij zgody” (CONS-01). Bez modeli – dowodem zostaje ``accounts.ConsentRecord``."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.consent_gate"
    label = "consent_gate"
    verbose_name = "uzupełnienie zgód"

    def ready(self) -> None:
        # Sygnały unieważniające cache stanu zgód – rejestrowane raz, przy starcie procesu.
        from . import signals  # noqa: F401
