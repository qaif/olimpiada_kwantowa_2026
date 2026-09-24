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
        # Trzeci import: odbiorniki czyszczące pamięć podręczną slidera sponsorów po publikacji
        # strony „Partnerzy” i po zapisie ustawień serwisu (apps/cms/sponsor_slider.py).
        # Czwarty: członkostwo w grupach ``cms:<slug>`` idzie za rolą koordynatora
        # (apps/cms/signals.py). Bez tego koordynator nadany po ``scope_cms_access`` nie miałby
        # ``/cms/`` do najbliższego ręcznego przebiegu komendy.
        from . import (  # noqa: F401
            analytics,
            announcements,
            signals,  # noqa: F401
            sponsor_slider,
        )

        # Dziennik zdarzeń ``/cms/`` zawężony do obiektów z zasięgu redaktora (apps/cms/scope.py).
        # Tak samo listy wyboru w filtrach listy stron (witryny, właściciele, edytujący).
        from .scope import install_log_entry_scope, install_page_filter_scope

        install_log_entry_scope()
        install_page_filter_scope()
