from django.apps import AppConfig


class WorkshopCertificatesConfig(AppConfig):
    """Automatyczne zaświadczenia z warsztatów na podstawie obecności (WS-CERT-02)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.workshop_certificates"
    label = "workshop_certificates"
    verbose_name = "zaświadczenia z warsztatów (automatyczne)"

    def ready(self) -> None:
        # Zależności idą od tej aplikacji do CMS-u i wyników, nigdy odwrotnie: CMS wysyła sygnał
        # o zapisie obecności, wyniki mają rejestr składów – żadne z nich tej aplikacji nie importuje.
        from apps.results.certificates import register_composer

        from . import triggers
        from .documents import compose, handles

        triggers.connect()
        register_composer(handles, compose)
