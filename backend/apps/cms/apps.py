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
        # Drugi import z tego samego powodu: odbiorniki czyszczące pamięć podręczną aktywnych
        # komunikatów po zapisie i skasowaniu (apps/cms/announcements.py). Bez niego baner
        # pokazywałby stan sprzed minuty także zaraz po kliknięciu „Ogłoś” – a komunikat o awarii
        # ogłasza się właśnie po to, żeby był od razu.
        from . import analytics, announcements  # noqa: F401
