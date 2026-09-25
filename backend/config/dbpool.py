"""Pula połączeń z Postgresem (``psycopg_pool``) – dobór wartości dla ``DATABASES``.

Osobny moduł, a nie kod wprost w ``config/settings/base.py``, z jednego powodu: reguły doboru
(kto dostaje pulę, jaki rozmiar, co przy niespójnych wartościach) mają testy, a moduł ustawień
da się sprawdzić wyłącznie w całości, razem ze wszystkimi zmiennymi środowiskowymi naraz.
Tu są czyste funkcje: te same argumenty dają ten sam słownik.

Pełne uzasadnienie (incydent z 09.09.2026, budżet połączeń, dlaczego Celery bez puli) stoi przy
``DATABASES`` w ``config/settings/base.py`` i w ``docs/OPERACJE.md`` § 11.2.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

#: Domyślny limit czekania na wolne połączenie z puli, w sekundach. Krócej niż domyślne 30 s
#: ``psycopg_pool``: przy rozmiarze puli równym liczbie wątków workera wątek nigdy nie powinien
#: czekać, więc czekanie oznacza awarię (połączenia zajęte przez coś, co ich nie oddaje), a nie
#: chwilowy tłok. Dziesięć sekund to jeszcze „wolna strona”, a nie „zawieszony wątek gunicorna”
#: – i błąd 500 po nich trafia do licznika 5xx watchdoga (``apps.core.alerts``) zamiast wisieć.
DEFAULT_TIMEOUT_SECONDS = 10.0

#: Ile połączeń pula trzyma otwartych także bez ruchu. Jedno na proces: pierwsze żądanie po
#: przerwie nie płaci za nowe połączenie, a bezczynna instancja nie trzyma w bazie więcej, niż
#: potrzebuje. Nadwyżkę ponad to minimum ``psycopg_pool`` zamyka sama, **stopniowo**: jedno
#: połączenie na każde ``max_idle`` (domyślnie 10 min), w którym nadwyżka nie była potrzebna – po
#: szczycie ruchu proces wraca z 4 do 1 w ok. pół godziny. Szybciej nie trzeba: górną granicę
#: (``max_size``) pula trzyma zawsze, a to ona chroni ``max_connections``.
DEFAULT_MIN_SIZE = 1


def running_under_celery(argv: list[str] | None = None) -> bool:
    """Czy bieżący proces to Celery (``celery -A config worker|beat|inspect …``).

    Siatka bezpieczeństwa pod jawnym ``DB_POOL=0`` w ``docker-compose.yml``: ktoś, kto uruchomi
    workera poza compose'em (albo dopisze nową usługę Celery i zapomni o zmiennej), nie powinien
    przez to dostać puli w procesach forkowanych – patrz ``pool_enabled_by_default``.
    """
    args = sys.argv if argv is None else argv
    if not args:
        return False
    if Path(args[0]).name == "celery":
        return True
    # ``python -m celery …`` – ``sys.argv[0]`` jest wtedy ścieżką do ``celery/__main__.py``.
    return Path(args[0]).name == "__main__.py" and Path(args[0]).parent.name == "celery"


def pool_available() -> bool:
    """Czy ``psycopg_pool`` jest zainstalowane (ekstra ``pool`` przy ``psycopg``).

    Obraz sprzed tej zmiany (albo deweloperski, nieprzebudowany po ``git pull``) go nie ma, a pula
    włączona bez pakietu to ``ImproperlyConfigured`` przy **pierwszym zapytaniu** – czyli każda
    strona 500, choć kontener wstaje zdrowy. Dlatego domyślna wartość ``DB_POOL`` bez pakietu jest
    ``False`` i ``web`` chodzi wtedy tak jak przed pulą (``DB_CONN_MAX_AGE``). Jawne ``DB_POOL=1``
    bez pakietu nadal kończy się tym błędem – kto włącza pulę ręcznie, ma się o tym dowiedzieć.
    Że obraz produkcyjny pakiet ma, sprawdza test (``apps/core/tests/test_dbpool.py``) i krok po
    wdrożeniu w ``docs/OPERACJE.md`` § 11.2.
    """
    return importlib.util.find_spec("psycopg_pool") is not None


def pool_enabled_by_default(argv: list[str] | None = None) -> bool:
    """Domyślna wartość ``DB_POOL``: pula tak, chyba że proces jest Celery albo brak pakietu.

    Celery (worker ``prefork``) i pula się nie lubią, i nie jest to kwestia gustu: pula ma wątki
    tła (uzupełnianie, sprawdzanie, zamykanie bezczynnych), a wątki nie przeżywają ``fork()``.
    Celery 5.6 wie o tym i w ``DjangoWorkerFixup._close_database`` **zamyka całą pulę** przed
    i po każdym zadaniu – czyli pula w workerze to otwarcie i zamknięcie ``min_size`` połączeń
    na każde zadanie, drożej niż jedno zwykłe połączenie. ``beat`` jest jednym wątkiem
    z jednym połączeniem, więc pula nie ma tam czego współdzielić.
    """
    return pool_available() and not running_under_celery(argv)


def pool_options(*, min_size: int, max_size: int, timeout: float) -> dict:
    """Słownik ``OPTIONS["pool"]`` dla Django (przekazywany do ``psycopg_pool.ConnectionPool``).

    Wartości niespójne są **naprawiane**, a nie odrzucane: ``max_size < 1`` daje 1, a ``min_size``
    większe od ``max_size`` zostaje obcięte do ``max_size``. Wyjątek w ustawieniach zatrzymałby
    start ``web`` po literówce w ``.env`` – a obcięcie daje pulę, która działa i robi to, co
    z dwóch sprzecznych liczb da się zrobić bezpiecznie (górna granica wygrywa, bo to ona chroni
    ``max_connections``). ``timeout`` niedodatni wraca do wartości domyślnej z tego samego powodu.

    ``check`` celowo **nie** jest tu ustawiane: Django dokłada ``ConnectionPool.check_connection``
    samo, gdy ``CONN_HEALTH_CHECKS`` jest włączone (``django/db/backends/postgresql/base.py``),
    czyli połączenie zerwane np. restartem Postgresa pula wymienia, zanim trafi do widoku.
    """
    max_size = max(1, int(max_size))
    min_size = min(max(0, int(min_size)), max_size)
    timeout = float(timeout) if timeout and float(timeout) > 0 else DEFAULT_TIMEOUT_SECONDS
    return {"min_size": min_size, "max_size": max_size, "timeout": timeout}
