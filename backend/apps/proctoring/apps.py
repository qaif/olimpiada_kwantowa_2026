from django.apps import AppConfig


class ProctoringConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.proctoring"
    label = "proctoring"
    verbose_name = "nadzór zdalny (LiveKit)"

    def ready(self) -> None:
        # Sprzątanie plików w buckecie przy kasowaniu wierszy – także kaskadą z usuniętego konta –
        # i wyproszenie odwołanego opiekuna drużyny z pokoju jego delegacji (DEL-01).
        from . import signals

        signals.connect_delegation_signals()
