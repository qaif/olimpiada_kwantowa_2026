"""Statystyki ogłoszonych wyników – liczby o etapie, nigdy o człowieku.

Po co osobny moduł, skoro tabela wyników już jest publiczna: tabela odpowiada na pytanie „jak
wypadłem ja”, a te liczby na pytanie „jak wypadła olimpiada”. To drugie pytanie zadają uczniowie
przed zgłoszeniem („czy mam szanse?”), nauczyciele („ile punktów zwykle wystarcza?”) i media.
Dotąd każda z tych osób musiała przeliczyć kilkaset wierszy tabeli ręcznie albo zapytać
organizatora – a odpowiedź i tak siedzi w danych, które już są jawne.

Skąd dane: **wyłącznie z zamrożonego snapshotu** (``ResultsPublication.snapshot``), z tego samego
JSON-a, który czyta publiczna tabela. Nie z ``FinalGrade``, nie z ``StageEntry``. Trzy powody:

- snapshot jest już zanonimizowany i **z definicji** nie zawiera nic, czego nie wolno pokazać.
  Statystyka licząca z żywych tabel musiałaby sama pilnować, czego nie wypisać – a pierwszy błąd
  w takim filtrze jest wyciekiem, nie pomyłką w liczbie,
- liczby muszą zgadzać się z tabelą co do sztuki. Gdyby jedno źródło było zamrożone, a drugie
  żywe, po pierwszej decyzji komisji strona statystyk twierdziłaby coś innego niż strona wyników,
- etap bez publikacji nie ma tu żadnego wiersza. Dla publiczności taka tabela nie istnieje, więc
  nie istnieje też jej średnia.

Czego w wyniku **nie ma**: liczebności mniejszych niż same wiersze tabeli (żadnego „ilu uczniów
z Twojej szkoły”), żadnych identyfikatorów, żadnego rozbicia, które schodziłoby poniżej
województwa. Rozkład punktów jest histogramem po wartościach skali, a nie listą wyników.

Województwa są w snapshocie **tylko** przy anonimizacji ``CODE`` (patrz ``build_snapshot``): przy
inicjałach ze szkołą i przy pełnych nazwiskach okręg celowo z tabeli wypada, bo mnożyłby cechy
quasi-identyfikujące. Statystyka nie ma prawa odtwarzać go z innych tabel, więc dla takich etapów
sekcji „województwa” po prostu nie ma – i to jest poprawna odpowiedź, nie brak funkcji.
"""

from __future__ import annotations

from collections import Counter
from statistics import mean, median

from django.core.cache import cache

from apps.competitions.models import Stage
from apps.core.text import fold

from .models import ResultsPublication

#: Ważność wpisu w pamięci podręcznej (sekundy). Dziesięć minut: strona jest publiczna i bywa
#: linkowana z mediów społecznościowych, a liczy się ją z JSON-a kilkuset wierszy na etap.
#: Dłużej trzymać nie warto – po ponownej publikacji wyników liczby mają się zgodzić z tabelą.
CACHE_TTL_SECONDS = 600

CACHE_KEY = "results.statistics.v1"

#: Krok szerokości słupka w procentach. Słupki są ``<div>``-ami z klasą, a nie ``style="width:…"``:
#: polityka bezpieczeństwa serwisu nie dopuszcza stylu wpisanego w dokument. Ten sam zabieg, co
#: przy pasku postępu koordynatora (``static/css/coordinator-tools.css``) – i ta sama uwaga:
#: słupek pokazuje proporcję, a dokładna liczba stoi obok niego.
WIDTH_STEP = 5


def width_class(share: float) -> str:
    """Nazwa klasy szerokości dla udziału w procentach, zaokrąglona do ``WIDTH_STEP``.

    Wartość niezerowa nigdy nie schodzi do ``w0``: jeden uczestnik na tysiąc to 0,1 %, a słupek
    zaokrąglony do zera znaczyłby „nikt”, czyli nieprawdę. Podłogą jest więc najmniejszy widoczny
    krok, a prawdziwa liczba i tak stoi w podpisie.
    """
    if share <= 0:
        return "w0"
    steps = max(1, min(round(share / WIDTH_STEP), 100 // WIDTH_STEP))
    return f"w{steps * WIDTH_STEP}"


def _share(count: int, total: int) -> float:
    return (count * 100 / total) if total else 0.0


def _points_of(row: dict) -> dict:
    points = row.get("points")
    return points if isinstance(points, dict) else {}


def _problem_numbers(rows: list[dict]) -> list[str]:
    """Numery zadań w kolejności naturalnej („2” przed „10”) – tak samo jak w publicznej tabeli."""
    return sorted(
        {str(key) for row in rows for key in _points_of(row)},
        key=lambda value: (len(value), value),
    )


def _distribution(rows: list[dict], number: str) -> dict:
    """Histogram punktów jednego zadania: ile prac dostało którą wartość skali.

    Wartości bierzemy z danych, a nie ze ``ScoringScale``: snapshot jest zamrożony, a skala etapu
    bywa później poprawiana – histogram ma opisywać ogłoszoną tabelę, a nie dzisiejszą definicję
    punktacji. Braki (praca bez oceny tego zadania) po prostu nie wchodzą do rozkładu.
    """
    counts = Counter()
    for row in rows:
        value = _points_of(row).get(number)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        counts[int(value)] += 1
    total = sum(counts.values())
    return {
        "number": number,
        "total": total,
        "bars": [
            {
                "value": value,
                "count": counts[value],
                "share": round(_share(counts[value], total), 1),
                "width": width_class(_share(counts[value], total)),
            }
            for value in sorted(counts)
        ],
    }


def _districts(rows: list[dict]) -> list[dict]:
    """Liczba uczestników w województwie – wyłącznie wtedy, gdy snapshot w ogóle je niesie.

    W snapshocie stoi **etykieta** województwa, a nie slug z bazy (``build_snapshot`` przepisuje
    ``get_district_display()``), więc nie ma tu czego tłumaczyć na czytelną postać – i dobrze,
    bo statystyka ma opisywać ogłoszoną tabelę dokładnie taką, jaka jest.

    Porządek: najpierw po liczbie malejąco, a przy remisie alfabetycznie **po złożonych
    diakrytykach** (``apps.core.text.fold``). Bez tego „małopolskie” trafiałoby za „mazowieckie”,
    bo ``ł`` ma wyższy punkt kodowy niż ``z`` – porządek maszynowy, którego nikt tak nie czyta.
    """
    counts = Counter(row["district"] for row in rows if row.get("district"))
    total = sum(counts.values())
    return [
        {
            "code": name,
            "label": name,
            "count": count,
            "share": round(_share(count, total), 1),
            "width": width_class(_share(count, total)),
        }
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], fold(item[0])))
    ]


def _totals(rows: list[dict]) -> list[int]:
    return [int(row["total"]) for row in rows if isinstance(row.get("total"), (int, float))]


def _threshold(rows: list[dict]) -> int | None:
    """Próg kwalifikacji **faktycznie osiągnięty**: najniższa suma wśród zakwalifikowanych.

    Liczymy go z tabeli, a nie z ``QualificationRule``, i to jest świadome. Reguła bywa hybrydowa
    („min. 20 pkt **oraz** najlepszych 200”), bywa zmieniana po przeliczeniu, a przy trybie „N na
    województwo” nie ma jednej liczby. Z ogłoszonej tabeli wynika natomiast jedno zdanie, które
    czytelnik rozumie bez znajomości regulaminu: „najsłabszy zakwalifikowany miał tyle punktów”.
    """
    qualified = [int(row["total"]) for row in rows if row.get("qualified") and "total" in row]
    return min(qualified) if qualified else None


def stage_statistics(publication: ResultsPublication) -> dict:
    """Komplet liczb jednego ogłoszonego etapu. Wejściem jest publikacja, nie etap."""
    rows = publication.rows
    totals = _totals(rows)
    numbers = _problem_numbers(rows)
    return {
        "stage_id": publication.stage_id,
        "stage_name": publication.stage.display_name,
        "edition": publication.stage.edition.year_label,
        "is_training": publication.stage.is_training,
        "published_at": publication.published_at,
        "participants": len(rows),
        "problems": [_distribution(rows, number) for number in numbers],
        # ``None`` zamiast zera dla pustej tabeli: „średnia 0 punktów” i „nie ma z czego liczyć”
        # to dwa różne zdania, a tylko drugie jest tu prawdziwe.
        "mean": round(mean(totals), 1) if totals else None,
        "median": round(median(totals), 1) if totals else None,
        "max_total": max(totals) if totals else None,
        "qualified": sum(1 for row in rows if row.get("qualified")),
        "threshold": _threshold(rows),
        "districts": _districts(rows),
    }


def build_statistics() -> list[dict]:
    """Statystyki wszystkich ogłoszonych etapów, od najnowszej publikacji. Bez pamięci podręcznej."""
    publications = ResultsPublication.objects.select_related("stage", "stage__edition").order_by(
        "-published_at", "-id"
    )
    return [stage_statistics(publication) for publication in publications]


def statistics() -> list[dict]:
    """To samo, co ``build_statistics``, ale przez pamięć podręczną (10 minut).

    ``get_or_set`` zamiast pary ``get``/``set``: przy pustym wyniku (żaden etap nie ma jeszcze
    ogłoszonych wyników) zwykłe ``if cached is None`` liczyłoby wszystko od nowa na każde wejście,
    bo pusta lista jest fałszywa, ale ``None`` nie jest.
    """
    return cache.get_or_set(CACHE_KEY, build_statistics, CACHE_TTL_SECONDS)


def invalidate() -> None:
    """Zrzuca podręczny wynik. Dla komend i testów – publikacja i tak przeterminuje go w 10 minut."""
    cache.delete(CACHE_KEY)


def published_stages() -> list[Stage]:
    """Etapy z ogłoszonymi wynikami – do odnośników „pełna tabela” obok statystyki."""
    return [publication.stage for publication in ResultsPublication.objects.select_related("stage")]
