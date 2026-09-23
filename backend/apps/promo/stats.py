"""Statystyki pobrań plakatów dla ekranu koordynatora i eksportu CSV.

Każda liczba jest **podwójna** (uwaga organizatora z 23.09.2026): pobrania łącznie i pobrania
z unikalnych adresów IP – w trzech oknach: ostatnie 7 dni, ostatnie 30 dni i od początku.

**Unikalność obowiązuje w całym oknie**, a nie w obrębie doby: „30 dni · unikalne IP” to
``COUNT(DISTINCT ip_hash)`` po wszystkich pobraniach z trzydziestu dni. Stąd wiersz sumy nie jest
sumą kolumny: szkoła, która pobrała trzy różne plakaty z jednego adresu, to w sumie **jeden**
unikalny adres, a nie trzy. Suma unikalnych liczy się więc osobnym zapytaniem po wszystkich
pobraniach konkursu (``totals``), a nie dodawaniem wierszy.

Wszystko liczy baza: liczniki per plakat to jedno zapytanie ``COUNT … FILTER`` pogrupowane po
plakacie, suma – jedno ``aggregate``, wykres dzienny – jedno ``COUNT`` pogrupowane po dniu. Liczba
zapytań ekranu nie rośnie ani z liczbą plakatów, ani z liczbą pobrań.

Pobrania, którym zadanie retencji wyzerowało pseudonim IP (``apps.promo.tasks``), liczą się do
pobrań łącznie, ale nie do unikalnych – ``COUNT(DISTINCT …)`` pomija ``NULL``. W oknach 7 i 30 dni
nie ma to znaczenia (retencja to dwanaście miesięcy); w kolumnie „od początku” znaczy „unikalne
adresy z ostatnich dwunastu miesięcy” i ekran mówi to wprost.

**Dni** liczymy w czasie organizatora (``TIME_ZONE``), a nie w UTC: pobranie o 00:30 w nocy
z niedzieli na poniedziałek jest dla koordynatora poniedziałkowe, a w UTC byłoby niedzielne.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.db.models import Count, Max, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.results.statistics import width_class

from .models import PromoDownload, PromoMaterial

#: Okna „ostatnie N dni” w tabeli. Dzisiejszy dzień wlicza się do okna.
SHORT_WINDOW_DAYS = 7
LONG_WINDOW_DAYS = 30
#: Ile dni pokazuje wykres dzienny.
CHART_DAYS = 30


@dataclass(frozen=True)
class Counts:
    """Sześć liczb jednego wiersza: (łącznie, unikalne IP) × (7 dni, 30 dni, od początku)."""

    total_7: int = 0
    unique_7: int = 0
    total_30: int = 0
    unique_30: int = 0
    total: int = 0
    unique: int = 0
    last_at: datetime | None = None


@dataclass(frozen=True)
class MaterialStats:
    material: PromoMaterial
    counts: Counts


def _window_start(days: int, today: date | None = None) -> datetime:
    """Początek okna ``days`` dni (włącznie z dzisiejszym) jako chwila w czasie organizatora."""
    today = today or timezone.localdate()
    first_day = today - timedelta(days=days - 1)
    return timezone.make_aware(datetime.combine(first_day, time.min))


def _aggregates(today: date | None = None) -> dict:
    """Wyrażenia sześciu liczb i ostatniego pobrania – te same dla wiersza plakatu i dla sumy."""
    recent_7 = Q(downloaded_at__gte=_window_start(SHORT_WINDOW_DAYS, today))
    recent_30 = Q(downloaded_at__gte=_window_start(LONG_WINDOW_DAYS, today))
    return {
        "total_7": Count("id", filter=recent_7),
        "unique_7": Count("ip_hash", distinct=True, filter=recent_7),
        "total_30": Count("id", filter=recent_30),
        "unique_30": Count("ip_hash", distinct=True, filter=recent_30),
        "total": Count("id"),
        "unique": Count("ip_hash", distinct=True),
        "last_at": Max("downloaded_at"),
    }


#: Pola liczbowe ``Counts`` – ``aggregate`` na pustym zbiorze oddaje w nich ``0``, ale wiersz
#: plakatu bez pobrań w ogóle nie istnieje w wyniku grupowania, stąd domyślne zera niżej.
_NUMBER_FIELDS = ("total_7", "unique_7", "total_30", "unique_30", "total", "unique")


def _counts(row: dict | None) -> Counts:
    row = row or {}
    return Counts(**{name: row.get(name) or 0 for name in _NUMBER_FIELDS}, last_at=row.get("last_at"))


def material_stats(competition, materials=None, *, today: date | None = None) -> list[MaterialStats]:
    """Liczniki każdego plakatu konkursu – także tych bez pobrań (z zerami) i zarchiwizowanych.

    ``materials`` pozwala podać listę już pobraną przez widok (ta sama kolejność, bez drugiego
    zapytania o plakaty); bez niego bierzemy wszystkie plakaty konkursu.
    """
    if materials is None:
        materials = list(PromoMaterial.objects.for_competition(competition))
    rows = (
        PromoDownload.objects.for_competition(competition)
        .values("material_id")
        .annotate(**_aggregates(today))
        .order_by()
    )
    by_material = {row["material_id"]: row for row in rows}
    return [
        MaterialStats(material=material, counts=_counts(by_material.get(material.pk)))
        for material in materials
    ]


def totals(competition, *, today: date | None = None) -> Counts:
    """Wiersz sumy – **osobnym zapytaniem**, bo unikalne adresy nie sumują się (docstring modułu)."""
    return _counts(PromoDownload.objects.for_competition(competition).aggregate(**_aggregates(today)))


def daily_series(
    competition, *, material=None, days: int = CHART_DAYS, today: date | None = None
) -> list[dict]:
    """Pobrania dzień po dniu za ostatnie ``days`` dni – z zerami tam, gdzie nic się nie działo.

    Wiersz: ``{"day", "count", "unique", "count_width", "unique_width"}``. „Unikalne” w wierszu
    dnia to różne adresy **tego dnia**. Szerokości słupków to klasy z zamkniętej listy
    (``apps.results.statistics.width_class``, ta sama, co na ``/statystyki/``), liczone względem
    **najlepszego dnia** okna, a nie sumy – wykres ma pokazać, który dzień był szczytem. Oba słupki
    dzielą tę samą skalę (szczyt pobrań łącznie), więc słupek unikalnych nigdy nie jest dłuższy od
    słupka wszystkich pobrań tego samego dnia – tak jak liczby, które pokazują.

    Najnowszy dzień jest na górze: koordynator patrzy tu po to, żeby zobaczyć, co się działo
    wczoraj i dziś, a nie żeby czytać miesiąc od początku.
    """
    today = today or timezone.localdate()
    queryset = PromoDownload.objects.for_competition(competition).filter(
        downloaded_at__gte=_window_start(days, today)
    )
    if material is not None:
        queryset = queryset.filter(material=material)
    rows = (
        queryset.annotate(day=TruncDate("downloaded_at", tzinfo=timezone.get_current_timezone()))
        .values("day")
        .annotate(count=Count("id"), unique=Count("ip_hash", distinct=True))
        .order_by()
    )
    by_day = {row["day"]: row for row in rows}
    peak = max((row["count"] for row in by_day.values()), default=0)
    series = []
    for offset in range(days):
        day = today - timedelta(days=offset)
        row = by_day.get(day, {})
        count = row.get("count", 0)
        unique = row.get("unique", 0)
        series.append(
            {
                "day": day,
                "count": count,
                "unique": unique,
                "count_width": width_class(count * 100 / peak if peak else 0),
                "unique_width": width_class(unique * 100 / peak if peak else 0),
            }
        )
    return series


#: Nagłówki eksportu CSV – kolejność kolumn taka, jak w tabeli na ekranie: każde okno ma parę
#: „pobrania / unikalne IP”, żeby w arkuszu dało się je porównać bez szukania kolumny.
CSV_HEADER = [
    "plakat",
    "opis",
    "format",
    "stan",
    f"pobrania – {SHORT_WINDOW_DAYS} dni",
    f"unikalne IP – {SHORT_WINDOW_DAYS} dni",
    f"pobrania – {LONG_WINDOW_DAYS} dni",
    f"unikalne IP – {LONG_WINDOW_DAYS} dni",
    "pobrania – od początku",
    "unikalne IP – od początku",
    "ostatnie pobranie",
]


def state_label(material: PromoMaterial) -> str:
    if material.is_archived:
        return "w archiwum"
    return "opublikowany" if material.is_published else "szkic"


def _count_cells(counts: Counts) -> list:
    return [
        counts.total_7,
        counts.unique_7,
        counts.total_30,
        counts.unique_30,
        counts.total,
        counts.unique,
        counts.last_at,
    ]


def csv_rows(stats: list[MaterialStats], total: Counts) -> list[list]:
    """Wiersze eksportu: plakat po plakacie, na końcu suma (tak jak w tabeli na ekranie)."""
    rows = [
        [
            item.material.title,
            item.material.description,
            item.material.format_label,
            state_label(item.material),
            *_count_cells(item.counts),
        ]
        for item in stats
    ]
    rows.append(["RAZEM (wszystkie plakaty)", "", "", "", *_count_cells(total)])
    return rows
