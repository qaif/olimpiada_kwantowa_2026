from django.apps import AppConfig


class ProctoringConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.proctoring"
    label = "proctoring"
    verbose_name = "nadzór zdalny (LiveKit)"

    def ready(self) -> None:
        # Sprzątanie plików w buckecie przy kasowaniu wierszy – także kaskadą z usuniętego konta.
        from . import signals  # noqa: F401
