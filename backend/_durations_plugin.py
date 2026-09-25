"""Pomiar czasów testów dla pytest-split (format pliku ``.test_durations``) – docs/TESTY.md.

Użycie: ``python -m pytest -p _durations_plugin …`` z katalogu ``backend/``. Czas testu to suma
przygotowania, wykonania i sprzątania – czyli także fikstury modułu (przewinięta baza testów
migracji liczy się do pierwszego testu modułu, i tak ma być: tyle kosztuje shard, który go dostał).

Działa także pod xdist: raporty workerów spływają do procesu sterującego, więc plik zapisuje
**tylko** on – worker ma w nim wyłącznie swoją część zbioru i nadpisałby wynik.
"""

import json
from collections import defaultdict

_d = defaultdict(float)


def pytest_runtest_logreport(report):
    _d[report.nodeid] += report.duration


def pytest_sessionfinish(session):
    if hasattr(session.config, "workerinput"):
        return
    with open(".test_durations", "w", encoding="utf-8") as fh:
        json.dump(dict(sorted(_d.items())), fh, indent=0, ensure_ascii=False)
