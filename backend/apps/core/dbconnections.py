"""Zajętość połączeń z Postgresem: ile jest otwartych, ile wolno, kto je trzyma.

Po co: incydent z 09.09.2026 (``web`` trzymał 92 bezczynne połączenia, Postgres odpowiadał
„too many clients already”, każda strona dawała 500) dojrzewał **dobę**, a nikt go nie widział,
bo każde sprawdzenie zdrowia mówiło „baza odpowiada” – aż do ostatniego wolnego połączenia.
``SELECT 1`` w ``/healthz/`` nie ma jak zauważyć, że połączeń zostało pięć. Ten moduł ma.

Jedno zapytanie do ``pg_stat_activity`` (połączenia klientów pogrupowane po
``application_name`` i stanie) razem z ``max_connections`` i ``superuser_reserved_connections``
z ustawień serwera. Liczymy połączenia **całego serwera**, a nie tylko naszej bazy, bo limit
``max_connections`` też jest serwerowy – ``psql`` dyżurnego do bazy ``postgres`` zjada ten sam
budżet.

Trzy odbiorców, trzy poziomy szczegółu:

- ``/healthz/`` i ``/status.json`` (publiczne) – **wyłącznie** poziom ``ok|warn|critical|unknown``.
  Liczba połączeń, limit i nazwy usług mówią obcemu, ile brakuje do położenia serwisu i czym,
- watchdog (``apps.core.alerts``, list do ``ALERT_EMAILS``) – liczby i podział na usługi,
- ``manage.py db_connections`` (operator na serwerze) – to samo co list, plus stany połączeń.

Wynik jest **buforowany na 30 sekund** we wspólnym cache'u (Redis), więc cała instalacja – ile
by nie miała workerów gunicorna i jak często by nie pukano w ``/healthz/`` – pyta
``pg_stat_activity`` najwyżej raz na pół minuty. Watchdog i komenda operatora czytają świeżo
(``fresh=True``): oni pytają rzadko, a potrzebują stanu z tej chwili, nie sprzed pół minuty.

Uwaga o uprawnieniach: kolumny ``state`` i ``application_name`` cudzych sesji Postgres pokazuje
wyłącznie superużytkownikowi albo roli z ``pg_read_all_stats``. Konto aplikacji w compose jest
właścicielem klastra (``POSTGRES_USER``), więc widzi wszystko; na instalacji z kontem bez tych
uprawnień **liczba** połączeń (a więc i poziom) zostaje prawdziwa, a w podziale pojawi się
``(niewidoczne)``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.conf import settings
from django.core.cache import cache
from django.db import connection

logger = logging.getLogger(__name__)

LEVEL_OK = "ok"
LEVEL_WARN = "warn"
LEVEL_CRITICAL = "critical"
#: Nie udało się odczytać – baza nie odpowiada albo zapytanie się nie powiodło. Osobny poziom,
#: a nie „ok”: brak odczytu nie jest dowodem, że połączeń jest dość.
LEVEL_UNKNOWN = "unknown"

CACHE_KEY = "core:db-connections"
CACHE_SECONDS = 30

#: Etykiety dla wartości, których Postgres nie podał. Tekst, a nie ``None``: słownik trafia do
#: cache'u i do listu, a pusty klucz w podziale „wg usługi” byłby nieczytelny.
UNNAMED = "(bez nazwy)"
HIDDEN = "(niewidoczne)"

#: ``backend_type = 'client backend'`` – bez procesów tła Postgresa (autovacuum, walwriter,
#: checkpointer…), które nie liczą się do ``max_connections``. ``LEFT JOIN`` z jednowierszową
#: tabelą, żeby ustawienia serwera przyszły także wtedy, gdy grupowanie nie da ani jednego wiersza.
QUERY = """
SELECT
    current_setting('max_connections')::int,
    current_setting('superuser_reserved_connections')::int,
    activity.application_name,
    activity.state,
    activity.connections
FROM (SELECT 1) AS one
LEFT JOIN (
    SELECT application_name, state, count(*)::int AS connections
    FROM pg_stat_activity
    WHERE backend_type = 'client backend'
    GROUP BY application_name, state
) AS activity ON true
"""


@dataclass(frozen=True)
class ConnectionUsage:
    """Migawka zajętości. Typy proste – przechodzi przez cache (pickle) i do listu bez zmian."""

    total: int
    max_connections: int
    reserved: int = 0
    by_application: dict[str, int] = field(default_factory=dict)
    by_state: dict[str, int] = field(default_factory=dict)

    @property
    def percent(self) -> float:
        if self.max_connections <= 0:
            return 0.0
        return self.total * 100.0 / self.max_connections

    @property
    def level(self) -> str:
        return level_for(self.percent)

    def summary(self) -> str:
        """Jedno zdanie dla człowieka: liczby, próg i kto trzyma połączenia (malejąco)."""
        apps = ", ".join(f"{name}: {count}" for name, count in sorted_counts(self.by_application))
        states = ", ".join(f"{name}: {count}" for name, count in sorted_counts(self.by_state))
        return (
            f"połączenia z Postgresem: {self.total} z {self.max_connections} "
            f"({self.percent:.0f}%; ostrzeżenie od {warn_percent()}%, "
            f"krytycznie od {critical_percent()}%; "
            f"zarezerwowane dla superużytkownika: {self.reserved}). "
            f"Wg usługi: {apps or '–'}. Wg stanu: {states or '–'}."
        )


def sorted_counts(counts: dict[str, int]) -> list[tuple[str, int]]:
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def warn_percent() -> int:
    return int(getattr(settings, "DB_CONNECTIONS_WARN_PERCENT", 80))


def critical_percent() -> int:
    return int(getattr(settings, "DB_CONNECTIONS_CRITICAL_PERCENT", 95))


def level_for(percent: float) -> str:
    """Poziom dla zajętości w procentach. Granice włącznie: równo 80 % to już ostrzeżenie."""
    if percent >= critical_percent():
        return LEVEL_CRITICAL
    if percent >= warn_percent():
        return LEVEL_WARN
    return LEVEL_OK


def _fetch() -> list[tuple]:
    """Surowe wiersze zapytania. Osobna funkcja, żeby testy podmieniały wynik, a nie kursor."""
    with connection.cursor() as cursor:
        cursor.execute(QUERY)
        return list(cursor.fetchall())


def _parse(rows: list[tuple]) -> ConnectionUsage:
    """Wiersze ``QUERY`` → migawka. Pierwsze dwie kolumny są w każdym wierszu te same."""
    if not rows:
        raise ValueError("pg_stat_activity: brak wierszy")
    max_connections, reserved = int(rows[0][0]), int(rows[0][1])
    by_application: dict[str, int] = {}
    by_state: dict[str, int] = {}
    total = 0
    for _max, _reserved, application, state, count in rows:
        if count is None:  # LEFT JOIN bez ani jednego połączenia – w praktyce niemożliwe (my jesteśmy)
            continue
        count = int(count)
        total += count
        app_key = application or UNNAMED
        state_key = state or HIDDEN
        by_application[app_key] = by_application.get(app_key, 0) + count
        by_state[state_key] = by_state.get(state_key, 0) + count
    return ConnectionUsage(
        total=total,
        max_connections=max_connections,
        reserved=reserved,
        by_application=by_application,
        by_state=by_state,
    )


def usage(*, fresh: bool = False) -> ConnectionUsage | None:
    """Zajętość połączeń albo ``None``, gdy nie da się jej odczytać. Nigdy nie rzuca.

    ``fresh=False`` (``/healthz/``, ``/status.json``) czyta wpis z cache'u, jeśli ma najwyżej
    ``CACHE_SECONDS``; ``fresh=True`` (watchdog, komenda operatora) zawsze pyta bazę i odświeża
    wpis przy okazji. Awaria cache'u nie blokuje odczytu – wtedy po prostu pytamy bazę, bo
    zapytanie jest tanie, a ``/healthz/`` i tak robi ``SELECT 1`` przy każdym wywołaniu.
    """
    if not fresh:
        try:
            cached = cache.get(CACHE_KEY)
        except Exception:  # noqa: BLE001 - awarię cache'u zgłasza /status/, tu wystarczy baza
            cached = None
        if isinstance(cached, ConnectionUsage):
            return cached
    try:
        result = _parse(_fetch())
    except Exception:  # noqa: BLE001 - treść błędu bazy nie ma prawa trafić do publicznej odpowiedzi
        logger.warning("Nie udało się odczytać zajętości połączeń z Postgresem.", exc_info=True)
        return None
    try:
        cache.set(CACHE_KEY, result, CACHE_SECONDS)
    except Exception:  # noqa: BLE001 - jak wyżej
        logger.debug("Nie udało się zapisać zajętości połączeń w cache'u.", exc_info=True)
    return result


def level(*, fresh: bool = False) -> str:
    """Sam poziom – to, i tylko to, wolno oddać w publicznej odpowiedzi."""
    current = usage(fresh=fresh)
    return current.level if current is not None else LEVEL_UNKNOWN
