from django.apps import AppConfig


class ThemesConfig(AppConfig):
    """Motywy wizualne wgrywane paczkami (THEME-01)."""

    name = "apps.themes"
    verbose_name = "Motywy"
    default_auto_field = "django.db.models.BigAutoField"
