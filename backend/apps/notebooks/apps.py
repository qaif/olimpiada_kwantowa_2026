from django.apps import AppConfig


class NotebooksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.notebooks"
    label = "notebooks"
    verbose_name = "Notatniki kwantowe"

    def ready(self) -> None:
        from . import checks  # noqa: F401 - rejestracja sprawdzenia notebooks.E001
