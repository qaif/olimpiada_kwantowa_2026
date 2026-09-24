"""``manage.py db_connections`` – zajętość połączeń z Postgresem dla operatora.

Publiczne ``/healthz/`` i ``/status.json`` oddają wyłącznie poziom (``ok|warn|critical``), bo
liczby mówią obcemu, ile brakuje do położenia serwisu. Ta komenda jest drugą połową: pełne liczby,
podział na usługi (``application_name`` – ``olimpiada-web``, ``olimpiada-worker``…) i na stany
(``active``, ``idle``, ``idle in transaction``…), odczyt świeży, z pominięciem 30-sekundowego bufora.
Opis poziomów i reakcji: ``docs/OPERACJE.md`` § 3.2 i § 11.2.

Kod wyjścia niesie poziom (0 = ok, 1 = warn, 2 = critical, 3 = brak odczytu), żeby komendę dało
się podpiąć pod dowolny zewnętrzny sprawdzacz bez parsowania tekstu.
"""

from __future__ import annotations

import sys

from django.core.management.base import BaseCommand

from apps.core import dbconnections

EXIT_CODES = {
    dbconnections.LEVEL_OK: 0,
    dbconnections.LEVEL_WARN: 1,
    dbconnections.LEVEL_CRITICAL: 2,
    dbconnections.LEVEL_UNKNOWN: 3,
}


class Command(BaseCommand):
    help = "Pokazuje zajętość połączeń z Postgresem: ile z ilu, wg usługi i wg stanu."

    def handle(self, *args, **options) -> None:
        current = dbconnections.usage(fresh=True)
        if current is None:
            self.stderr.write(self.style.ERROR("Nie udało się odczytać pg_stat_activity (szczegóły w logu)."))
            sys.exit(EXIT_CODES[dbconnections.LEVEL_UNKNOWN])

        style = {
            dbconnections.LEVEL_OK: self.style.SUCCESS,
            dbconnections.LEVEL_WARN: self.style.WARNING,
            dbconnections.LEVEL_CRITICAL: self.style.ERROR,
        }[current.level]
        self.stdout.write(
            style(
                f"poziom: {current.level}  "
                f"połączenia: {current.total} z {current.max_connections} ({current.percent:.0f}%)  "
                f"progi: {dbconnections.warn_percent()}% / {dbconnections.critical_percent()}%  "
                f"zarezerwowane dla superużytkownika: {current.reserved}"
            )
        )
        for title, counts in (("wg usługi", current.by_application), ("wg stanu", current.by_state)):
            self.stdout.write(f"\n{title}:")
            for name, count in dbconnections.sorted_counts(counts):
                self.stdout.write(f"  {name:32} {count:5}")
        code = EXIT_CODES[current.level]
        if code:
            sys.exit(code)
