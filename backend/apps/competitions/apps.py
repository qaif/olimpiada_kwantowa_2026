from django.apps import AppConfig


class CompetitionsConfig(AppConfig):
    name = "apps.competitions"
    verbose_name = "Zawody: edycje, etapy, zadania"

    def ready(self) -> None:
        # Rejestracja kontroli ``manage.py check`` (``competitions.W001`` – sekret przepustek Jitsi).
        # ``checks.register`` działa przez wykonanie modułu, tak samo jak w ``apps.cms``.
        # Sygnały automatycznego zapisu nowych kont do etapu treningowego (REG-UX-01 § 3).
        from . import (
            auto_registration,  # noqa: F401
            checks,  # noqa: F401
        )
