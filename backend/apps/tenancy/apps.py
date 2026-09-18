from django.apps import AppConfig


class TenancyConfig(AppConfig):
    name = "apps.tenancy"
    verbose_name = "Konkursy"

    def ready(self) -> None:
        """Podpięcie sygnału pilnującego zgodności ``Competition.primary_domain`` z witryną.

        Import jest lokalny, bo ``ready`` biegnie przed pełnym załadowaniem aplikacji, a moduł
        sygnałów sięga modeli Wagtaila.

        Tym samym importem rejestrują się ``CompetitionSiteAlias`` i ``DocumentTemplate``: oba
        modele stoją w osobnych modułach (``apps/tenancy/aliases.py``, ``apps/tenancy/documents.py``,
        podział własności plików etapu 2 § 4.3), a Django rejestruje modele przez wykonanie ich
        modułu. Bez tej linijki tabela nie powstałaby w migracjach, a błąd wyglądałby jak brak
        modelu, nie jak brak importu.
        """
        from . import aliases, documents, signals  # noqa: F401 - rejestracja przez import, patrz docstring
        from .setup import announce_setup_token

        # Token kreatora ``/setup/`` wypisany raz na start kontenera (§ 1.7.1, decyzja D20).
        # Funkcja **nie pyta bazy** – na tym etapie nie wolno – i nie pisze do logu nic, gdy token
        # stoi w ``.env``. W pozostałym wypadku losuje go raz na kontener (wypisuje wyłącznie ten
        # proces, który założył plik tokenu), więc administrator ma adres kreatora w logu, zanim
        # spróbuje go otworzyć.
        announce_setup_token()
