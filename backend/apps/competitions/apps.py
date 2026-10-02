from django.apps import AppConfig


class CompetitionsConfig(AppConfig):
    name = "apps.competitions"
    verbose_name = "Zawody: edycje, etapy, zadania"

    def ready(self) -> None:
        # Rejestracja kontroli ``manage.py check`` (``competitions.W001`` – sekret przepustek Jitsi).
        # ``checks.register`` działa przez wykonanie modułu, tak samo jak w ``apps.cms``.
        from . import checks  # noqa: F401
