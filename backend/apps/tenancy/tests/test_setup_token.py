"""Token i limit prób kreatora ``/setup/`` — bez bazy i bez żądania.

Osobny plik od ``test_setup.py``, bo przedmiot jest inny: tam sprawdzamy bramkę widzianą przez
przeglądarkę, tutaj sam mechanizm sekretu. Te reguły muszą dać się sprawdzić także wtedy, gdy baza
nie odpowiada — bo token powstaje **przed** pierwszym zapytaniem do niej.
"""

from __future__ import annotations

import pytest

from apps.tenancy import setup


@pytest.fixture
def token_in_a_file(tmp_path, monkeypatch):
    """Świeży „kontener”: brak ``SETUP_TOKEN`` i własny plik tokenu w katalogu testu."""
    monkeypatch.delenv(setup.SETUP_TOKEN_ENV, raising=False)
    path = tmp_path / "setup-token"
    monkeypatch.setenv(setup.SETUP_TOKEN_FILE_ENV, str(path))
    return path


def test_the_configured_token_wins_and_never_touches_the_file(token_in_a_file, monkeypatch):
    """``SETUP_TOKEN`` z ``.env`` jest źródłem pierwszym — i jedynym, gdy jest ustawiony."""
    monkeypatch.setenv(setup.SETUP_TOKEN_ENV, "z-pliku-env")

    assert setup.setup_token() == "z-pliku-env"
    assert not token_in_a_file.exists()


def test_the_token_is_drawn_once_and_reused_by_every_worker(token_in_a_file):
    """Token jest jeden na kontener: drugi proces czyta plik, a nie losuje własnego.

    To jest sedno wyboru pliku zamiast zmiennej modułu: gunicorn ma trzy procesy robocze, każdy
    importuje aplikację osobno, a token wylosowany w pamięci działałby dla co trzeciego żądania.
    """
    first = setup.setup_token()
    second = setup.setup_token()

    assert first and first == second
    assert token_in_a_file.read_text(encoding="utf-8").strip() == first


def test_the_announcement_writes_exactly_one_line_with_the_address(token_in_a_file, caplog):
    """Wiersz w logu kontenera jest kontraktem: wypisuje go proces, który token wylosował."""
    with caplog.at_level("WARNING", logger="apps.tenancy.setup"):
        setup.announce_setup_token()
        setup.announce_setup_token()

    lines = [record.getMessage() for record in caplog.records]
    assert len(lines) == 1
    assert f"/setup/?token={setup.setup_token()}" in lines[0]


def test_the_announcement_is_silent_when_the_token_comes_from_the_environment(
    token_in_a_file, monkeypatch, caplog
):
    """Sekret z konfiguracji nie ma powodu wędrować do dziennika kontenera."""
    monkeypatch.setenv(setup.SETUP_TOKEN_ENV, "z-pliku-env")

    with caplog.at_level("WARNING", logger="apps.tenancy.setup"):
        setup.announce_setup_token()

    assert caplog.records == []
    assert not token_in_a_file.exists()


def test_an_empty_or_wrong_token_never_matches(token_in_a_file, monkeypatch):
    monkeypatch.setenv(setup.SETUP_TOKEN_ENV, "prawidlowy")

    assert setup.token_matches("prawidlowy")
    assert not setup.token_matches("prawidlowy ")
    assert not setup.token_matches("")


def test_nothing_matches_when_the_token_cannot_be_issued(tmp_path, monkeypatch):
    """Instalacja bez tokenu nie otwiera kreatora nikomu — także żądaniu z pustym parametrem."""
    monkeypatch.delenv(setup.SETUP_TOKEN_ENV, raising=False)
    monkeypatch.setattr(setup, "_read_or_create_token", lambda *, announce: "")

    assert setup.setup_token() == ""
    assert not setup.token_matches("cokolwiek")


def test_the_rate_comes_from_the_settings_when_the_scope_is_known(settings, monkeypatch):
    """Dopisanie scope'u ``setup`` do ustawień DRF przejmuje pierwszeństwo nad zapasową stawką."""
    monkeypatch.setenv(setup.SETUP_THROTTLE_RATE_ENV, "5/hour")
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
            setup.THROTTLE_SCOPE: "7/hour",
        },
    }

    assert setup.throttle_rate() == "7/hour"


def test_the_rate_falls_back_to_the_documented_default(monkeypatch):
    """Bez scope'u w ustawieniach i bez zmiennej środowiskowej obowiązuje 10/h z § 1.7.1."""
    monkeypatch.delenv(setup.SETUP_THROTTLE_RATE_ENV, raising=False)

    assert setup.throttle_rate() == setup.DEFAULT_THROTTLE_RATE == "10/hour"
