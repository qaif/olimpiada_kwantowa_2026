from django.apps import AppConfig


class WebtexConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.webtex"
    label = "webtex"
    verbose_name = "Edytor LaTeX (webTEX)"

    def ready(self) -> None:
        from . import checks  # noqa: F401 - rejestracja sprawdzenia webtex.W001
