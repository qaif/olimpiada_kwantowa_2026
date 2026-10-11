from django.apps import AppConfig


class ProblemTranslationsConfig(AppConfig):
    """Tłumaczenia zadań przez delegacje krajowe (docs/tasks/TR-01.md)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.problem_translations"
    label = "problem_translations"
    verbose_name = "tłumaczenia zadań"

    def ready(self) -> None:
        # Sygnał pilnuje zmiany wersji oficjalnej zadania także wtedy, gdy koordynator podmienia PDF
        # na dotychczasowym ekranie „Zadania” – ten ekran o tłumaczeniach nic nie wie i nie musi.
        from . import signals  # noqa: F401
