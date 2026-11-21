"""Rzut i kształty mapy Polski dla SVG rysowanego po stronie serwera (MAP-01).

Rzut jest najprostszy, jaki daje uczciwy obraz kraju tej wielkości: równoodległościowy z długością
skróconą o cosinus 52. równoleżnika (środek Polski). Na dziesięciu stopniach długości różnica wobec
rzutu Merkatora czy Lamberta jest poniżej grubości linii granicy, a wzór mieści się w jednej
funkcji i nie potrzebuje biblioteki.

Granice województw: ``data/wojewodztwa.geojson`` – Państwowy Rejestr Granic (GUGiK) przez
repozytorium ppatrzyk/polska-geojson, uproszczone (źródło, licencja i sposób uproszczenia
w ``data/README.md``). Ścieżki SVG liczymy raz na proces (``voivodeship_paths``).

Wszystkie liczby wychodzą stąd jako **napisy** z kropką dziesiętną: szablon z lokalizacją polską
wypisałby ``12,5``, a dla atrybutu SVG przecinek jest separatorem współrzędnych, nie ułamka.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent / "data" / "wojewodztwa.geojson"

#: Lewy górny róg rzutu (długość, szerokość) i skala (jednostki SVG na stopień szerokości).
LON0, LAT0, SCALE = 14.0, 55.0, 60.0
COS_LAT = math.cos(math.radians(52.0))
#: Prawy dolny róg obszaru rysunku – za najdalej wysuniętym punktem kraju (Bieszczady, Podlasie).
LON1, LAT1 = 24.3, 49.0
#: Margines wokół rysunku w jednostkach SVG – kółko przy granicy nie może zostać ucięte.
MARGIN = 8

#: Promień kółka: największe ma ``MAX_RADIUS``, a pole każdego jest proporcjonalne do liczby
#: uczniów (promień ∝ pierwiastkowi). ``MIN_RADIUS`` – żeby najmniejsza pokazana miejscowość była
#: widoczna i klikalna, także na telefonie.
MAX_RADIUS = 16.0
MIN_RADIUS = 2.6


def project(lon: float, lat: float) -> tuple[float, float]:
    return (lon - LON0) * COS_LAT * SCALE, (LAT0 - lat) * SCALE


def fmt(value: float) -> str:
    """Liczba do atrybutu SVG: jedna cyfra po kropce, bez ``-0.0``."""
    text = f"{value:.1f}"
    return "0.0" if text == "-0.0" else text


def view_box() -> str:
    width, height = project(LON1, LAT1)
    return f"{-MARGIN} {-MARGIN} {width + 2 * MARGIN:.0f} {height + 2 * MARGIN:.0f}"


def radius(count: int, largest: int) -> float:
    if largest <= 0:
        return MIN_RADIUS
    return max(MIN_RADIUS, MAX_RADIUS * math.sqrt(count / largest))


@lru_cache(maxsize=1)
def _features() -> tuple[tuple[str, tuple[tuple[float, float], ...]], ...]:
    payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return tuple(
        (
            feature["properties"]["slug"],
            tuple((float(x), float(y)) for x, y in feature["geometry"]["coordinates"][0]),
        )
        for feature in payload["features"]
    )


@lru_cache(maxsize=1)
def voivodeship_paths() -> tuple[dict, ...]:
    """``{"slug", "d"}`` dla każdego województwa – atrybut ``d`` elementu ``<path>``."""
    paths = []
    for slug, ring in _features():
        points = "L".join(f"{fmt(x)},{fmt(y)}" for x, y in (project(lon, lat) for lon, lat in ring))
        paths.append({"slug": slug, "d": f"M{points}Z"})
    return tuple(paths)


def contains(slug: str, lon: float, lat: float) -> bool:
    """Czy punkt leży w granicach województwa ``slug`` (parzystość przecięć promienia).

    Uproszczona granica jest dokładna do ok. kilometra – wystarczy, żeby złapać punkt
    z niewłaściwego końca kraju (geokoder trafił w miejscowość o tej samej nazwie).
    """
    for name, ring in _features():
        if name != slug:
            continue
        inside = False
        for (x1, y1), (x2, y2) in zip(ring, ring[1:], strict=False):
            if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
        return inside
    return False
