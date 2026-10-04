from django.apps import AppConfig


class MonitoringConfig(AppConfig):
    """Śledzenie błędów (GlitchTip) i sprawdzanie dostępności – OPS-02, docs/OPERACJE.md § 44.

    Bez modeli i bez migracji. ``ready()`` uruchamia klienta błędów wyłącznie przy niepustym
    ``SENTRY_DSN`` – bez niego nie importuje nawet ``sentry_sdk`` (``sentry.py``).
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.monitoring"
    label = "monitoring"
    verbose_name = "monitoring błędów"

    def ready(self):
        from .sentry import init_from_settings

        init_from_settings()
