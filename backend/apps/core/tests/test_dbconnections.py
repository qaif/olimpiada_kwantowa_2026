"""Zajętość połączeń z Postgresem: odczyt, progi, bufor i to, kto ile z tego widzi.

Odczyt z ``pg_stat_activity`` jest podmieniany (``dbconnections._fetch``) wszędzie poza jednym
testem dymnym: serwer testowej bazy bywa wspólny z innymi przebiegami suity, a jego prawdziwa
zajętość nie jest przedmiotem żadnej reguły. Podmieniamy **wiersze zapytania**, a nie wynik –
dzięki temu parsowanie (sumy, podział na usługi i stany, puste nazwy) jest sprawdzane naprawdę.

Reguły, których pilnują te testy:

- **progi** – 80 % to ostrzeżenie, 95 % stan krytyczny (granice włącznie), a brak odczytu to
  ``unknown``, nie ``ok``,
- **publiczne odpowiedzi niosą wyłącznie poziom** – ani liczby, ani nazwy usług nie wychodzą przez
  ``/healthz/`` i ``/status.json``,
- **bufor 30 s** – dowolnie częste pukanie w ``/healthz/`` to jedno zapytanie na pół minuty,
- **watchdog** pisze z liczbami i podziałem na usługi, a ostrzeżenie i stan krytyczny mają osobne
  klucze wyciszenia.
"""

from __future__ import annotations

import json

import pytest
from django.core import mail
from django.core.management import call_command

from apps.core import alerts, backup, dbconnections
from apps.core.tasks import heartbeat

pytestmark = pytest.mark.django_db

ALERT_ADDRESS = "dyzurny@example.test"


def rows(max_connections: int = 100, reserved: int = 3, **by_app_state) -> list[tuple]:
    """Wiersze w kształcie ``dbconnections.QUERY``: ``aplikacja__stan=liczba``."""
    result = []
    for key, count in by_app_state.items():
        application, _, state = key.partition("__")
        result.append(
            (
                max_connections,
                reserved,
                application.replace("_", "-") or None,
                state.replace("_", " ") or None,
                count,
            )
        )
    return result or [(max_connections, reserved, None, None, None)]


@pytest.fixture
def fetched(monkeypatch):
    """Podmienia odczyt i liczy wywołania – żeby sprawdzić bufor."""
    state = {"rows": rows(olimpiada_web__idle=5), "calls": 0}

    def fake_fetch():
        state["calls"] += 1
        return state["rows"]

    monkeypatch.setattr(dbconnections, "_fetch", fake_fetch)
    return state


# --- odczyt i progi ------------------------------------------------------------------------------


def test_rows_are_summed_per_service_and_per_state(fetched):
    fetched["rows"] = rows(
        olimpiada_web__idle=10,
        olimpiada_web__active=2,
        olimpiada_worker__idle=2,
        olimpiada_beat__idle=1,
        __active=1,  # psql dyżurnego bez application_name
    )

    usage = dbconnections.usage(fresh=True)

    assert usage.total == 16
    assert usage.max_connections == 100
    assert usage.reserved == 3
    assert usage.by_application == {
        "olimpiada-web": 12,
        "olimpiada-worker": 2,
        "olimpiada-beat": 1,
        dbconnections.UNNAMED: 1,
    }
    assert usage.by_state == {"idle": 13, "active": 3}
    assert usage.level == dbconnections.LEVEL_OK


def test_a_session_we_may_not_inspect_is_counted_but_labelled_hidden(fetched):
    """Bez ``pg_read_all_stats`` Postgres oddaje ``state = NULL`` – liczba musi zostać prawdziwa."""
    fetched["rows"] = [(100, 3, "olimpiada-web", "idle", 4), (100, 3, None, None, 2)]

    usage = dbconnections.usage(fresh=True)

    assert usage.total == 6
    assert usage.by_state == {"idle": 4, dbconnections.HIDDEN: 2}


@pytest.mark.parametrize(
    ("total", "expected"),
    [
        (0, dbconnections.LEVEL_OK),
        (79, dbconnections.LEVEL_OK),
        (80, dbconnections.LEVEL_WARN),
        (94, dbconnections.LEVEL_WARN),
        (95, dbconnections.LEVEL_CRITICAL),
        (100, dbconnections.LEVEL_CRITICAL),
    ],
)
def test_thresholds_are_inclusive(fetched, total, expected):
    fetched["rows"] = rows(olimpiada_web__idle=total) if total else rows()

    assert dbconnections.level(fresh=True) == expected


def test_thresholds_come_from_settings(fetched, settings):
    settings.DB_CONNECTIONS_WARN_PERCENT = 50
    settings.DB_CONNECTIONS_CRITICAL_PERCENT = 60
    fetched["rows"] = rows(max_connections=20, olimpiada_web__idle=11)  # 55 %

    assert dbconnections.level(fresh=True) == dbconnections.LEVEL_WARN


def test_a_failed_read_is_unknown_not_ok(monkeypatch):
    def broken():
        raise RuntimeError("could not connect to server: db:5432 user=olimpiada")

    monkeypatch.setattr(dbconnections, "_fetch", broken)

    assert dbconnections.usage(fresh=True) is None
    assert dbconnections.level(fresh=True) == dbconnections.LEVEL_UNKNOWN


def test_the_result_is_cached_for_half_a_minute(fetched):
    dbconnections.level()
    dbconnections.level()
    dbconnections.level()

    assert fetched["calls"] == 1


def test_a_fresh_read_bypasses_and_refreshes_the_cache(fetched):
    dbconnections.level()
    fetched["rows"] = rows(olimpiada_web__idle=90)

    assert dbconnections.level() == dbconnections.LEVEL_OK  # jeszcze z bufora
    assert dbconnections.level(fresh=True) == dbconnections.LEVEL_WARN
    assert dbconnections.level() == dbconnections.LEVEL_WARN  # bufor odświeżony świeżym odczytem
    assert fetched["calls"] == 2


def test_the_real_query_runs_against_postgres():
    """Test dymny bez podmiany: zapytanie jest poprawnym SQL-em i widzi co najmniej nas samych."""
    usage = dbconnections.usage(fresh=True)

    assert usage is not None
    assert usage.total >= 1
    assert usage.max_connections > 0
    assert usage.level in {dbconnections.LEVEL_OK, dbconnections.LEVEL_WARN, dbconnections.LEVEL_CRITICAL}


# --- publiczne odpowiedzi: tylko poziom ----------------------------------------------------------


def test_healthz_exposes_only_the_level(client, fetched):
    fetched["rows"] = rows(olimpiada_web__idle=85)

    response = client.get("/healthz/")

    assert response.status_code == 200  # ostrzeżenie nie jest chorobą kontenera – bez 503
    assert response.json()["db_connections"] == dbconnections.LEVEL_WARN
    body = response.content.decode()
    assert "olimpiada-web" not in body
    assert "85" not in body
    assert "100" not in body


def test_healthz_stays_200_even_when_connections_are_critical(client, fetched):
    fetched["rows"] = rows(olimpiada_web__idle=99)

    response = client.get("/healthz/")

    assert response.status_code == 200
    assert response.json()["db_connections"] == dbconnections.LEVEL_CRITICAL


def test_status_json_carries_the_level_after_the_older_keys(client, fetched):
    heartbeat()
    fetched["rows"] = rows(olimpiada_web__idle=96, olimpiada_worker__idle=1)

    payload = json.loads(client.get("/status.json").content)

    # Dołożony na końcu kontraktu w swoim wydaniu – za nim jest już tylko ``backup_offsite``
    # (kopia poza serwerem, wydanie „kopie zapasowe na Dysku Google”).
    assert list(payload)[-2:] == ["db_connections", "backup_offsite"]
    assert payload["db_connections"] == dbconnections.LEVEL_CRITICAL
    # Poziom nie gasi całej strony: przy braku połączeń zgaśnie i tak ``services.database``.
    assert payload["status"] == "ok"
    assert "olimpiada-worker" not in json.dumps(payload)


# --- watchdog ------------------------------------------------------------------------------------


@pytest.fixture
def on_duty(settings):
    settings.ALERT_EMAILS = [ALERT_ADDRESS]
    heartbeat()
    backup.record(ok=True)
    backup.record(verified=True)


def test_low_usage_is_not_an_alert(on_duty, fetched):
    assert alerts.evaluate() == []


def test_high_usage_is_a_warning_with_numbers_and_services(on_duty, fetched):
    fetched["rows"] = rows(olimpiada_web__idle=80, olimpiada_worker__active=2)

    found = alerts.evaluate()

    assert [alert.key for alert in found] == ["db-connections:warn"]
    detail = found[0].detail
    assert "82 z 100" in detail
    assert "olimpiada-web: 80" in detail
    assert "olimpiada-worker: 2" in detail
    assert "idle: 80" in detail


def test_the_watchdog_reads_fresh_not_from_the_cache(on_duty, fetched):
    dbconnections.level()  # bufor: 5 połączeń
    fetched["rows"] = rows(olimpiada_web__idle=97)

    assert [alert.key for alert in alerts.evaluate()] == ["db-connections:critical"]


def test_escalation_to_critical_is_not_silenced_by_the_warning(on_duty, fetched):
    """Osobne klucze: list o 95 % wychodzi mimo trwającego wyciszenia listu o 80 %."""
    fetched["rows"] = rows(olimpiada_web__idle=85)
    assert alerts.run() == 1

    fetched["rows"] = rows(olimpiada_web__idle=97)
    assert alerts.run() == 1

    subjects = [message.subject for message in mail.outbox]
    assert len(subjects) == 2
    assert "wysoka liczba połączeń" in subjects[0]
    assert "kończą się połączenia" in subjects[1]


def test_an_unreadable_count_is_not_a_second_alert_about_the_database(on_duty, monkeypatch):
    """Niedziałającą bazę zgłasza ``service:database`` – drugi list o tym samym byłby szumem."""

    def broken():
        raise RuntimeError("baza nie odpowiada")

    monkeypatch.setattr(dbconnections, "_fetch", broken)

    assert not [alert for alert in alerts.evaluate() if alert.key.startswith("db-connections")]


# --- komenda operatora ---------------------------------------------------------------------------


def test_the_command_prints_numbers_and_breakdown(fetched, capsys):
    fetched["rows"] = rows(olimpiada_web__idle=10, olimpiada_web__active=1, olimpiada_beat__idle=1)

    call_command("db_connections")

    out = capsys.readouterr().out
    assert "poziom: ok" in out
    assert "12 z 100" in out
    assert "olimpiada-web" in out and "olimpiada-beat" in out
    assert "idle" in out and "active" in out


@pytest.mark.parametrize(
    ("total", "code"),
    [(85, 1), (97, 2)],
)
def test_the_command_exit_code_carries_the_level(fetched, total, code):
    fetched["rows"] = rows(olimpiada_web__idle=total)

    with pytest.raises(SystemExit) as exit_info:
        call_command("db_connections")

    assert exit_info.value.code == code


def test_the_command_reports_an_unreadable_count(monkeypatch):
    def broken():
        raise RuntimeError("baza nie odpowiada")

    monkeypatch.setattr(dbconnections, "_fetch", broken)

    with pytest.raises(SystemExit) as exit_info:
        call_command("db_connections")

    assert exit_info.value.code == 3
