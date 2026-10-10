from django.apps import AppConfig


class ResultsConfig(AppConfig):
    name = "apps.results"
    verbose_name = "Wyniki i publikacja"

    def ready(self) -> None:
        # Rejestracja odbiorników (unieważnianie statystyk przy publikacji i jej wycofaniu).
        from . import signals  # noqa: F401
