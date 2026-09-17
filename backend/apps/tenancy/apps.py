from django.apps import AppConfig


class TenancyConfig(AppConfig):
    name = "apps.tenancy"
    verbose_name = "Konkursy"

    def ready(self) -> None:
        """Podpięcie sygnału pilnującego zgodności ``Competition.primary_domain`` z witryną.

        Import jest lokalny, bo ``ready`` biegnie przed pełnym załadowaniem aplikacji, a moduł
        sygnałów sięga modeli Wagtaila.
        """
        from . import signals  # noqa: F401 - rejestracja przez import, patrz docstring modułu
