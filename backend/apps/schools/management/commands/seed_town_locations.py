"""Wgranie punktów miejscowości (mapa uczestników, MAP-01) z fixture'u do bazy – idempotentnie.

Komenda chodzi przy **każdym** wdrożeniu, zaraz po ``seed_schools`` (``scripts/deploy.sh``), z tego
samego powodu co tamta: to dane referencyjne, których nikt nie redaguje w panelu, a poprawiony punkt
ma trafić na produkcję razem z wydaniem.

Zasady:

- **tabela jest lustrem pliku.** Upsert po parze (gmina, województwo), a wiersz nieobecny w pliku
  znika – punkt nie ma kluczy obcych (mapa łączy go z wykazem szkół po napisach), więc skasowanie
  niczego nie osieroci, a punkt usunięty z fixture'u jako błędny nie ma prawa wisieć na mapie dalej,
- **plik jest sprawdzany, nie zakładany.** Nieznane województwo, współrzędne poza Polską albo
  zdublowana para przerywają wgranie, zanim cokolwiek zostanie zapisane,
- po zmianie gaśnie pamięć podręczna map wszystkich konkursów (``apps.participant_map.services``) –
  nowy punkt ma się pojawić od razu, a nie po godzinie.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import Voivodeship
from apps.schools.models import TownLocation

#: Domyślny plik – ten sam, który leży w repozytorium (opis źródła i licencji: fixtures/README.md).
DEFAULT_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "miejscowosci-wspolrzedne.json"

#: Prostokąt otaczający Polskę z zapasem – sito na zamienione miejscami szerokość i długość.
LAT_RANGE = (48.9, 55.0)
LON_RANGE = (14.0, 24.3)


def load_fixture(path: Path) -> list[tuple[str, str, float, float]]:
    """Wiersze ``(gmina, województwo, szerokość, długość)`` z pliku – po sprawdzeniu każdego."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CommandError(f"Nie da się wczytać {path}: {exc}") from exc
    rows = payload.get("towns") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise CommandError(f"{path}: brak listy „towns”.")
    voivodeships = set(Voivodeship.values)
    seen: set[tuple[str, str]] = set()
    out = []
    for index, row in enumerate(rows):
        if not (isinstance(row, list) and len(row) == 4):
            raise CommandError(f"{path}: wiersz {index} nie ma postaci [gmina, województwo, szer., dł.].")
        city, voivodeship, lat, lon = row
        if not isinstance(city, str) or not city.strip():
            raise CommandError(f"{path}: wiersz {index} bez nazwy gminy.")
        if voivodeship not in voivodeships:
            raise CommandError(f"{path}: wiersz {index} ({city}) – nieznane województwo {voivodeship!r}.")
        if not (
            isinstance(lat, int | float)
            and isinstance(lon, int | float)
            and LAT_RANGE[0] <= lat <= LAT_RANGE[1]
            and LON_RANGE[0] <= lon <= LON_RANGE[1]
        ):
            raise CommandError(f"{path}: wiersz {index} ({city}) – współrzędne poza Polską: {lat}, {lon}.")
        key = (city.strip(), voivodeship)
        if key in seen:
            raise CommandError(f"{path}: para {city} / {voivodeship} występuje dwa razy.")
        seen.add(key)
        out.append((key[0], voivodeship, float(lat), float(lon)))
    return out


class Command(BaseCommand):
    help = "Wgrywa punkty miejscowości mapy uczestników z fixture'u (OpenStreetMap). Idempotentne."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fixture",
            type=Path,
            default=DEFAULT_FIXTURE,
            help=f"plik JSON z punktami (domyślnie {DEFAULT_FIXTURE.name})",
        )

    def handle(self, *args, **options):
        rows = load_fixture(Path(options["fixture"]))
        wanted = {(city, voivodeship): (lat, lon) for city, voivodeship, lat, lon in rows}
        created = updated = 0
        with transaction.atomic():
            existing = {(row.city_parent, row.voivodeship): row for row in TownLocation.objects.all()}
            stale = [row.pk for key, row in existing.items() if key not in wanted]
            removed = TownLocation.objects.filter(pk__in=stale).delete()[0] if stale else 0
            changed = []
            fresh = []
            for key, (lat, lon) in wanted.items():
                row = existing.get(key)
                if row is None:
                    fresh.append(
                        TownLocation(city_parent=key[0], voivodeship=key[1], latitude=lat, longitude=lon)
                    )
                elif (row.latitude, row.longitude) != (lat, lon):
                    row.latitude, row.longitude = lat, lon
                    changed.append(row)
            TownLocation.objects.bulk_create(fresh, batch_size=500)
            TownLocation.objects.bulk_update(changed, ["latitude", "longitude"], batch_size=500)
            created, updated = len(fresh), len(changed)
        if created or updated or removed:
            from apps.participant_map.services import invalidate_all

            invalidate_all()
        self.stdout.write(
            f"Punkty miejscowości: {len(wanted)} w pliku – nowe {created}, zmienione {updated}, "
            f"usunięte {removed}."
        )
