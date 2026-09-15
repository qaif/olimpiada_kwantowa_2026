from django.apps import AppConfig


class CmsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cms"
    label = "cms"
    verbose_name = "część informacyjna"

    def ready(self):
        # Import dla efektu ubocznego: podpina odbiornik czyszczący pamięć podręczną odpowiedzi
        # „czy analityka jest włączona” (apps/cms/analytics.py). Bez tego zmiana identyfikatora
        # GA4 w /cms/ dochodziłaby do nagłówka CSP dopiero po upływie TTL.
        from . import analytics  # noqa: F401
