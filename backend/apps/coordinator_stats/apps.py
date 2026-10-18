from django.apps import AppConfig


class CoordinatorStatsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.coordinator_stats"
    label = "coordinator_stats"
    verbose_name = "lejek uczestnika i raporty wysyłek"

    def ready(self) -> None:
        # Odbiornik odbić z ``apps.email_delivery`` (przypisanie do komunikatu) i reset adresów przy
        # zmianie albo usunięciu konta – ``signals.py``.
        from . import signals

        signals.connect()
