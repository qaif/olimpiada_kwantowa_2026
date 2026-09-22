from django.apps import AppConfig


class WebConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.web"
    label = "web"
    verbose_name = "interfejs WWW"

    def ready(self):
        # Import dla efektu ubocznego: podpina odbiorniki unieważniające cache całych stron
        # publicznych (apps/web/page_cache.py) po publikacji/wycofaniu/przeniesieniu/skasowaniu
        # strony Wagtaila, zapisie ustawień witryny, komunikacie organizatora, zmianie osi czasu
        # i ogłoszeniu wyników. Bez niego strona cache'owana na 120 s pokazywałaby stan sprzed
        # publikacji jeszcze przez dwie minuty po kliknięciu „Opublikuj”.
        from . import page_cache  # noqa: F401
