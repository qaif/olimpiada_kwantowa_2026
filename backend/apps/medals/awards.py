"""Arytmetyka nagród: progi z rankingu, remisy, wyróżnienia i ranking krajów (MED-01 § 1, § 3).

Moduł jest **czysty** – nie zna bazy, żądań ani audytu. Wejściem są wiersze ``compute_stage_results``
(słowniki z ``entry_id``, ``status``, ``total``, ``points``), wyjściem nagroda per wpis i progi.
Dzięki temu podgląd koordynatora, zamrożenie i testy liczą to samo jedną funkcją – rozjazd między
ekranem, na którym koordynator dobiera progi, a ogłoszeniem byłby najgorszym możliwym błędem.

Trzy zasady, na których stoi rachunek:

- **próg jest wartością sumy, nie miejscem** – ten sam wynik daje zawsze tę samą nagrodę. Etap
  może mieć kryteria rozstrzygania remisów (``competitions.TieBreak``) i one ustawiają miejsca
  w tabeli, ale nie dzielą medalu: dwóch uczestników z tą samą sumą nie dostanie złota i srebra
  tylko dlatego, że jeden oddał pracę minutę wcześniej,
- **pula jest zaokrąglana w górę** (``ceil(N × % / 100)``) – ta sama reguła, co ``PERCENTILE``
  w regułach przejścia (``results.services._percentile_top_n``): „8 % z pola dwudziestu” to dwa
  złote medale, a nie jeden,
- **zero nie nagradza** – jak „zero nie kwalifikuje” w progu ``TOP_N``: medal za brak rozwiązania
  w małym polu byłby nagrodą za samo przyjście.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal

from apps.competitions.models import StageEntryStatus
from apps.core.points import points_json, to_points

from .models import AWARD_RANK, MEDALS, Award, TiePolicy


@dataclass(frozen=True)
class SchemeParams:
    """Parametry schematu oderwane od modelu – żeby rachunek dało się sprawdzić bez bazy."""

    gold_percent: Decimal = Decimal("8")
    silver_percent: Decimal = Decimal("17")
    bronze_percent: Decimal = Decimal("25")
    tie_policy: str = TiePolicy.INCLUSIVE
    hm_percent_of_best: Decimal | None = Decimal("50")
    hm_full_solution: bool = True

    @classmethod
    def of(cls, scheme) -> SchemeParams:
        return cls(
            gold_percent=scheme.gold_percent,
            silver_percent=scheme.silver_percent,
            bronze_percent=scheme.bronze_percent,
            tie_policy=scheme.tie_policy,
            hm_percent_of_best=scheme.hm_percent_of_best,
            hm_full_solution=scheme.hm_full_solution,
        )

    @property
    def cumulative(self) -> tuple[Decimal, Decimal, Decimal]:
        gold = Decimal(self.gold_percent)
        silver = gold + Decimal(self.silver_percent)
        return gold, silver, silver + Decimal(self.bronze_percent)


@dataclass
class Thresholds:
    """Wynik rachunku do pokazania: pole, pule, progi punktowe i liczności każdej nagrody."""

    field_size: int = 0
    slots: dict[str, int] = field(default_factory=dict)
    cutoffs: dict[str, Decimal | None] = field(default_factory=dict)
    best: Decimal | None = None
    hm_cutoff: Decimal | None = None
    counts: dict[str, int] = field(default_factory=dict)

    def share(self, award: str) -> Decimal | None:
        """Rzeczywisty odsetek pola z tą nagrodą – przy remisach różni się od zamówionego."""
        if not self.field_size:
            return None
        return (Decimal(self.counts.get(award, 0)) * 100 / self.field_size).quantize(Decimal("0.1"))

    def as_json(self) -> dict:
        """Postać do ``MedalScheme.thresholds`` – liczby przez ``points_json`` (bez ``Decimal``)."""
        return {
            "field_size": self.field_size,
            "slots": dict(self.slots),
            "cutoffs": {key: points_json(value) for key, value in self.cutoffs.items()},
            "best": points_json(self.best),
            "hm_cutoff": points_json(self.hm_cutoff),
            "counts": dict(self.counts),
        }


def pool_size(field_size: int, percent: Decimal) -> int:
    """Ilu uczestników mieści pula ``percent`` pola – zaokrąglone **w górę** (patrz docstring modułu)."""
    if field_size <= 0 or percent <= 0:
        return 0
    return min(field_size, math.ceil(Decimal(field_size) * Decimal(percent) / 100))


def cutoff(positive_totals: list[Decimal], slots: int, policy: str) -> Decimal | None:
    """Najniższa suma, która jeszcze dostaje nagrodę z puli ``slots`` – albo ``None`` (nikt).

    ``positive_totals`` są malejąco i bez zer. ``INCLUSIVE``: progiem jest wynik zajmujący ostatnie
    miejsce puli, więc cała grupa remisowa na granicy wchodzi (jak ``_top_n_cutoff``). ``EXCLUSIVE``:
    grupa, która przekroczyłaby pulę, nie wchodzi – progiem zostaje najbliższy wyższy wynik.
    """
    if slots <= 0 or not positive_totals:
        return None
    if slots >= len(positive_totals):
        return positive_totals[-1]
    boundary = positive_totals[slots - 1]
    if policy == TiePolicy.EXCLUSIVE and positive_totals[slots] == boundary:
        higher = [value for value in positive_totals[:slots] if value > boundary]
        return min(higher) if higher else None
    return boundary


def has_full_solution(row: dict, full_scores: dict[str, Decimal]) -> bool:
    """Czy wiersz ma choć jedno zadanie za pełne maksimum. Etap testowy (bez zadań) – nigdy."""
    points = row.get("points") or {}
    for number, maximum in full_scores.items():
        value = to_points(points.get(number))
        if value is not None and maximum is not None and maximum > 0 and value >= maximum:
            return True
    return False


def compute_awards(
    rows: list[dict], params: SchemeParams, full_scores: dict[str, Decimal] | None = None
) -> tuple[dict[int, str], Thresholds]:
    """Nagroda wyliczona dla każdego wpisu i progi. Bez ręcznych zmian – te nakłada ``final_awards``.

    Zdyskwalifikowany nie jest częścią pola i nie dostaje niczego. Wyróżnienie dostaje wiersz bez
    medalu, z dodatnią sumą, jeśli spełnia **którekolwiek** z włączonych kryteriów: suma ≥ X %
    najlepszej sumy albo pełne rozwiązanie choć jednego zadania (IMO).
    """
    full_scores = full_scores or {}
    eligible = [row for row in rows if row.get("status") != StageEntryStatus.DISQUALIFIED]
    totals = {row["entry_id"]: to_points(row.get("total")) or Decimal(0) for row in rows}
    positive = sorted(
        (totals[row["entry_id"]] for row in eligible if totals[row["entry_id"]] > 0), reverse=True
    )
    gold_pct, silver_pct, bronze_pct = params.cumulative
    thresholds = Thresholds(field_size=len(eligible))
    for award, percent in ((Award.GOLD, gold_pct), (Award.SILVER, silver_pct), (Award.BRONZE, bronze_pct)):
        slots = pool_size(len(eligible), percent)
        thresholds.slots[award] = slots
        thresholds.cutoffs[award] = cutoff(positive, slots, params.tie_policy)
    thresholds.best = positive[0] if positive else None
    if thresholds.best is not None and params.hm_percent_of_best is not None:
        thresholds.hm_cutoff = thresholds.best * Decimal(params.hm_percent_of_best) / 100

    awards: dict[int, str] = {}
    eligible_ids = {row["entry_id"] for row in eligible}
    for row in rows:
        entry_id = row["entry_id"]
        total = totals[entry_id]
        award = Award.NONE
        if entry_id in eligible_ids and total > 0:
            for medal in MEDALS:
                threshold = thresholds.cutoffs.get(medal)
                if threshold is not None and total >= threshold:
                    award = medal
                    break
            else:
                by_percent = thresholds.hm_cutoff is not None and total >= thresholds.hm_cutoff
                by_solution = params.hm_full_solution and has_full_solution(row, full_scores)
                if by_percent or by_solution:
                    award = Award.HONOURABLE
        awards[entry_id] = str(award)
    thresholds.counts = count_awards(awards[row["entry_id"]] for row in eligible)
    return awards, thresholds


def count_awards(awards) -> dict[str, int]:
    """Liczności każdej nagrody (także ``NONE``) – klucze zawsze komplet, w kolejności ważności."""
    counts = {str(award): 0 for award in Award}
    for award in awards:
        counts[str(award)] = counts.get(str(award), 0) + 1
    return counts


def final_awards(computed: dict[int, str], overrides: dict[int, str]) -> dict[int, str]:
    """Nagroda ostateczna: ręczna zmiana bije wyliczenie – dokładnie jak decyzja komitetu bije próg."""
    return {entry_id: overrides.get(entry_id, award) for entry_id, award in computed.items()}


def award_sort_key(award: str) -> int:
    return AWARD_RANK.get(award, len(AWARD_RANK))


# --- ranking krajów ------------------------------------------------------------------------------


#: Najmniejsza liczba wyników kraju, przy której wolno pokazać jego sumę i średnią punktów. Suma
#: delegacji jedno- albo dwuosobowej jest de facto wynikiem konkretnej osoby (albo da się go z niej
#: odjąć, znając wynik drugiej) – ta sama myśl, co ``MIN_SCHOOL_GROUP`` przy inicjałach ze szkołą.
#: Liczby medali zostają: medal jest ogłoszeniem samym w sobie.
MIN_COUNTRY_GROUP = 3


def country_table(
    rows: list[dict],
    awards: dict[int, str],
    countries: dict[int, tuple[str, str]],
    *,
    awarded_only: bool = False,
    min_group: int = MIN_COUNTRY_GROUP,
) -> list[dict]:
    """Agregaty per kraj: uczestnicy, medale, wyróżnienia, suma i średnia punktów – z miejscem po sumie.

    ``countries`` to ``{entry_id: (kod, nazwa)}``; wpis bez kraju trafia do grupy o pustym kodzie.
    Zdyskwalifikowany liczy się do uczestników kraju (startował), ale jego punkty nie wchodzą do
    sumy – ta sama reguła, co w tabeli: dyskwalifikacja nie jest wynikiem punktowym.

    **Suma i średnia tylko dla kraju z co najmniej ``min_group`` wynikami** (``None`` → „—” na
    stronie); mniejszy kraj ma same liczby medali i nie ma miejsca. ``awarded_only`` (publikacja
    wyników „tylko awansujący”) liczy sumę wyłącznie z wyników nagrodzonych – wyników reszty pola
    wyniki tego etapu nie ogłosiły, więc ranking krajów nie może ich ujawnić w sumie.

    Miejsce jest **po sumie punktów** (nieoficjalny ranking IMO), remis = to samo miejsce. Kolejność
    wierszy: kraje z sumą po miejscu, potem kraje bez sumy; w obu grupach dalej liczba złotych,
    srebrnych i brązowych medali i nazwa – tabela ma być powtarzalna co do wiersza.
    """
    table: dict[str, dict] = {}
    scored: dict[str, list[Decimal]] = {}
    for row in rows:
        code, name = countries.get(row["entry_id"], ("", ""))
        item = table.setdefault(
            code,
            {
                "code": code,
                "name": name,
                "contestants": 0,
                str(Award.GOLD): 0,
                str(Award.SILVER): 0,
                str(Award.BRONZE): 0,
                str(Award.HONOURABLE): 0,
            },
        )
        item["contestants"] += 1
        award = awards.get(row["entry_id"], Award.NONE)
        if award != Award.NONE:
            item[str(award)] += 1
        if row.get("status") == StageEntryStatus.DISQUALIFIED:
            continue
        if awarded_only and award == Award.NONE:
            continue
        scored.setdefault(code, []).append(to_points(row.get("total")) or Decimal(0))
    for code, item in table.items():
        values = scored.get(code, [])
        shown = len(values) >= min_group
        item["total"] = sum(values, Decimal(0)) if shown else None
        item["average"] = (item["total"] / len(values)).quantize(Decimal("0.01")) if shown else None

    def medals_key(item):
        return (
            -item[str(Award.GOLD)],
            -item[str(Award.SILVER)],
            -item[str(Award.BRONZE)],
            item["name"].casefold(),
        )

    ranked = sorted(
        (item for item in table.values() if item["total"] is not None),
        key=lambda item: (-item["total"], *medals_key(item)),
    )
    unranked = sorted((item for item in table.values() if item["total"] is None), key=medals_key)
    previous = None
    rank = 0
    for index, item in enumerate(ranked, start=1):
        if item["total"] != previous:
            rank, previous = index, item["total"]
        item["rank"] = rank
    for item in unranked:
        item["rank"] = None
    ordered = ranked + unranked
    for item in ordered:
        item["total"] = points_json(item["total"])
        item["average"] = points_json(item["average"])
    return ordered


def medal_order(table: list[dict]) -> list[dict]:
    """Ta sama tabela krajów ułożona „olimpijsko”: złoto, srebro, brąz, wyróżnienia, potem nazwa."""
    return sorted(
        table,
        key=lambda item: (
            -item[str(Award.GOLD)],
            -item[str(Award.SILVER)],
            -item[str(Award.BRONZE)],
            -item[str(Award.HONOURABLE)],
            item["name"].casefold(),
        ),
    )
