"""Wgranie słownika szkół z fixture'u do bazy (idempotentnie).

Komenda jest uruchamiana przy **każdym** wdrożeniu, tuż po migracjach – w odróżnieniu od seedów
treści, które chodzą wyłącznie przy pierwszym. Uzasadnienie: to nie jest treść redakcyjna, tylko
rejestr, którego nikt nie edytuje w panelu. Nadpisanie nazwy szkoły danymi z ministerstwa niczyjej
pracy nie kasuje, a brak aktualizacji zostawiłby uczestnikom listę sprzed roku.

Zasady:

- **upsert po ``rspo``** – numer z rejestru jest stały mimo zmian nazwy i adresu,
- **nic nie jest kasowane.** Szkoła nieobecna w fixturze dostaje ``is_active=False`` i przestaje
  się pokazywać w podpowiedziach; wiersz zostaje, bo może być wskazany przez ``school_ref``
  uczestnika sprzed roku (``on_delete=PROTECT``),
- **plik jest sprawdzany, nie zakładany.** Nieznane województwo albo typ szkoły przerywa wgranie:
  cichy import połowy słownika byłby gorszy niż brak importu, bo objawiłby się dopiero uczniowi,
  który nie znajduje swojej szkoły.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import Voivodeship
from apps.schools.models import DEFAULT_SOURCE_YEAR, School, SchoolKind, search_text_for

#: Domyślny słownik – ten sam plik, który leży w repozytorium (patrz fixtures/README.md).
DEFAULT_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "szkoly-srednie-sio-2025.json"

#: Pola aktualizowane przy ponownym wgraniu. ``is_active`` jest tu celowo: szkoła, która wróciła
#: do wykazu (np. po przekształceniu), ma znów być wybieralna.
UPDATED_FIELDS = (
    "name",
    "kind",
    "voivodeship",
    "city",
    "postal_code",
    "address",
    "is_public",
    "search_text",
    "is_active",
    "source_year",
)

#: Rozmiar porcji przy zapisie. Fixture ma ponad osiem tysięcy wierszy – jedno ``bulk_create``
#: na całość zbudowałoby zapytanie, którego sterownik nie musi udźwignąć.
BATCH_SIZE = 500


class Command(BaseCommand):
    help = "Wgrywa słownik szkół ponadpodstawowych (SIO/RSPO) z fixture'u. Idempotentne."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fixture",
            type=Path,
            default=DEFAULT_FIXTURE,
            help=f"plik JSON ze słownikiem (domyślnie {DEFAULT_FIXTURE.name})",
        )
        parser.add_argument(
            "--source-year",
            default=DEFAULT_SOURCE_YEAR,
            help=f"rok szkolny wykazu zapisywany przy wierszach (domyślnie {DEFAULT_SOURCE_YEAR})",
        )

    def handle(self, *args, **options):
        # ``Path(...)`` mimo ``type=Path`` w argumencie: ``call_command("seed_schools", fixture=…)``
        # omija argparse, więc z testu i z innej komendy przychodzi tu goły string.
        path = Path(options["fixture"])
        source_year: str = options["source_year"]
        if not path.is_file():
            raise CommandError(f"Nie ma pliku ze słownikiem: {path}")
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise CommandError(f"{path}: nieprawidłowy JSON ({exc})") from exc
        if not isinstance(rows, list) or not rows:
            raise CommandError(f"{path}: oczekiwano niepustej listy szkół")

        parsed = [self._school_fields(row, index, source_year) for index, row in enumerate(rows, start=1)]
        created, updated, deactivated = self._apply(parsed)
        self.stdout.write(
            self.style.SUCCESS(
                f"Słownik szkół: {len(parsed)} w pliku, {created} nowych, "
                f"{updated} zaktualizowanych, {deactivated} wygaszonych."
            )
        )

    def _school_fields(self, row, index: int, source_year: str) -> dict:
        """Jeden wiersz fixture'u sprowadzony do pól modelu. Każdy błąd jest twardy."""
        if not isinstance(row, dict):
            raise CommandError(f"Wiersz {index}: oczekiwano obiektu JSON")
        try:
            rspo = int(row["rspo"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CommandError(f"Wiersz {index}: brak poprawnego numeru RSPO") from exc
        name = str(row.get("name") or "").strip()
        city = str(row.get("city") or "").strip()
        if not name:
            raise CommandError(f"Wiersz {index} (RSPO {rspo}): pusta nazwa szkoły")
        kind = str(row.get("kind") or "")
        if kind not in SchoolKind.values:
            raise CommandError(f"Wiersz {index} (RSPO {rspo}): nieznany typ szkoły {kind!r}")
        voivodeship = str(row.get("voivodeship") or "")
        if voivodeship not in Voivodeship.values:
            raise CommandError(f"Wiersz {index} (RSPO {rspo}): nieznane województwo {voivodeship!r}")
        return {
            "rspo": rspo,
            "name": name[:255],
            "kind": kind,
            "voivodeship": voivodeship,
            "city": city[:120],
            "postal_code": str(row.get("postal_code") or "")[:12],
            "address": str(row.get("address") or "")[:255],
            "is_public": bool(row.get("is_public", True)),
            "search_text": search_text_for(name, city)[:400],
            "is_active": True,
            "source_year": source_year,
        }

    @transaction.atomic
    def _apply(self, parsed: list[dict]) -> tuple[int, int, int]:
        """Wgrywa porcję wierszy i wygasza te, których w niej nie ma. Zwraca liczniki."""
        existing = dict(School.objects.values_list("rspo", "id"))
        to_create = [School(**fields) for fields in parsed if fields["rspo"] not in existing]
        to_update = [
            School(id=existing[fields["rspo"]], **fields) for fields in parsed if fields["rspo"] in existing
        ]
        # ``bulk_create``/``bulk_update`` omijają ``School.save()``, więc ``search_text`` jest tu
        # policzony jawnie w ``_school_fields`` – inaczej kolumna wyszukiwania zostałaby pusta
        # i podpowiedzi nie znajdowałyby niczego.
        School.objects.bulk_create(to_create, batch_size=BATCH_SIZE)
        School.objects.bulk_update(to_update, list(UPDATED_FIELDS), batch_size=BATCH_SIZE)
        deactivated = (
            School.objects.filter(is_active=True)
            .exclude(rspo__in=[fields["rspo"] for fields in parsed])
            .update(is_active=False)
        )
        return len(to_create), len(to_update), deactivated
