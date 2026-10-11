from django.apps import AppConfig


class EmailDeliveryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.email_delivery"
    label = "email_delivery"
    verbose_name = "doręczalność poczty"

    def ready(self) -> None:
        # Zmiana albo usunięcie adresu konta kasuje stan starego adresu (MAIL-02 § 2.4).
        from . import signals

        signals.connect()
