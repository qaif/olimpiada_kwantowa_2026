"""Pomiar czasów testów dla pytest-split (format pliku ``.test_durations``) – docs/TESTY.md § 5.

Użycie: ``python -m pytest -p _durations_plugin …`` z katalogu ``backend/``. Czas testu to suma
przygotowania, wykonania i sprzątania – czyli także fikstury modułu (przewinięta baza testów
migracji liczy się do pierwszego testu modułu, i tak ma być: tyle kosztuje shard, który go dostał).

Dwie poprawki względem surowego czasu, obie po to, żeby pomiar spod xdist (CI, ``-n``) dało się
wprost użyć do podziału shardów:

- **bez kosztów sesji workera.** Zakładanie bazy testowej i migawka po migracjach
  (``django_db_setup``) liczą się do przygotowania pierwszego testu z bazą w każdym workerze –
  w CI to 1–2,5 min doklejone do przypadkowego testu. Shard płaci za to raz niezależnie od tego,
  które testy dostanie, więc z czasu testu to odejmujemy (worker zapisuje ten koszt w
  ``user_properties`` raportu, proces sterujący go odejmuje),
- **identyfikator bez grupy xdist.** Pod ``loadgroup`` worker dokleja do testu migracji
  ``@<moduł>``; plik ma klucze bez tego dopisku, żeby był ten sam z xdist i bez
  (``conftest._alias_split_durations`` dokłada alias przy podziale).

Plik zapisuje **tylko** proces sterujący (worker ma w nim wyłącznie swoją część zbioru i nadpisałby
wynik) – domyślnie ``.test_durations``, a pod inną ścieżkę przez ``TEST_DURATIONS_FILE`` (CI zapisuje
czasy każdego sharda osobno i wystawia je jako artefakt: ``scripts/refresh_test_durations.sh``).
"""

import json
import os
import time
from collections import defaultdict

import pytest

#: Fikstury sesji, których koszt ponosi worker, a nie test, który akurat poszedł pierwszy.
SESSION_FIXTURES = frozenset({"django_db_setup"})
#: Klucz w ``report.user_properties``.
SESSION_SETUP_KEY = "session_setup_seconds"

_d = defaultdict(float)
#: Koszt fikstur sesji czekający na raport najbliższego testu (worker) i głębokość zagnieżdżenia –
#: ``conftest.django_db_setup`` nadpisuje fiksturę pytest-django o tej samej nazwie.
_pending = {"seconds": 0.0, "depth": 0}


@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(fixturedef, request):  # noqa: ARG001 - sygnatura haka
    if fixturedef.argname not in SESSION_FIXTURES:
        return (yield)
    outermost = _pending["depth"] == 0
    _pending["depth"] += 1
    start = time.perf_counter()
    try:
        return (yield)
    finally:
        _pending["depth"] -= 1
        if outermost:
            _pending["seconds"] += time.perf_counter() - start


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):  # noqa: ARG001 - sygnatura haka
    report = yield
    if call.when == "setup" and _pending["seconds"]:
        report.user_properties.append((SESSION_SETUP_KEY, _pending["seconds"]))
        _pending["seconds"] = 0.0
    return report


def plain_nodeid(nodeid: str) -> str:
    """``…::test_x[a]@apps.y.tests.test_z`` → ``…::test_x[a]`` (ta sama reguła, co w xdist)."""
    if nodeid.rfind("@") > nodeid.rfind("]"):
        return nodeid.rsplit("@", 1)[0]
    return nodeid


def pytest_runtest_logreport(report):
    session = sum(value for key, value in report.user_properties if key == SESSION_SETUP_KEY)
    _d[plain_nodeid(report.nodeid)] += max(0.0, report.duration - session)


def pytest_sessionfinish(session):
    if hasattr(session.config, "workerinput"):
        return
    path = os.environ.get("TEST_DURATIONS_FILE") or ".test_durations"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(
            {key: round(value, 4) for key, value in sorted(_d.items())}, fh, indent=0, ensure_ascii=False
        )
