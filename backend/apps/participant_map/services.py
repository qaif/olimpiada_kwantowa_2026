"""Agregat mapy uczestników (MAP-01): liczenie, próg ochrony, pamięć podręczna i odświeżanie.

**Kogo liczymy.** Uczestników bieżącej edycji konkursu – tą samą definicją, co lejek i komunikaty
(``apps.accounts.messaging.current_edition_participants``): wpis do etapu tej edycji albo konto
założone od jej powstania. Bez kont zanonimizowanych (``exclude_anonymised``). Miejscowość to gmina
szkoły z wykazu SIO (``School.city_parent`` – dzielnice dużych miast złączone) w parze
z województwem szkoły; uczestnik ze szkołą spoza wykazu wchodzi wyłącznie do liczby łącznej.

**Ochrona danych.** Strona jest publiczna, a uczestnicy to w większości małoletni. Miejscowość
z mniej niż ``min_town_count`` uczestnikami (domyślnie 3, ``models.MIN_TOWN_COUNT``) nie wychodzi
z nazwą ani z kółkiem: jej uczniowie trafiają do pozycji „pozostałe miejscowości” swojego
województwa. Agregat nie niesie nazw szkół ani niczego o pojedynczej osobie.

**Koszt.** Na zimno stała liczba zapytań niezależnie od liczby uczestników i miejscowości: edycja,
ustawienia, sumy (jedno ``aggregate``), grupowanie po (gmina, województwo) i punkty miejscowości.
Na ciepło – zero: wynik leży w pamięci podręcznej (domyślnie godzinę, ``PARTICIPANT_MAP_CACHE_SECONDS``)
pod kluczem konkursu, a przycisk koordynatora (``refresh``) przelicza go od razu i unieważnia
zapisane strony gościa (``apps.web.page_cache``).
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.utils import timezone

from apps.core.text import fold

from . import geometry
from .models import MIN_TOWN_COUNT, MapSettings

logger = logging.getLogger(__name__)

#: Flaga konkursu (``apps.tenancy.models.FEATURE_DEFAULTS``). Domyślnie wyłączona.
MAP_FLAG = "participant_map"

#: Ile miejscowości dostaje podpis na mapie (największe; reszta – w ``<title>`` kółka i na liście).
LABELLED_TOWNS = 10
#: Ile największych miejscowości liczy kafel „udział N największych”.
TOP_TOWNS = 5

CACHE_PREFIX = "participant_map"
#: Format wpisu – podbijany przy zmianie kształtu słownika, żeby nowy kod nie czytał starego.
CACHE_FORMAT = 1
#: Licznik pokolenia wspólny dla wszystkich konkursów: ``invalidate_all`` (nowe punkty miejscowości)
#: podbija go zamiast wyliczać klucze.
GENERATION_KEY = f"{CACHE_PREFIX}:generation"


def enabled(competition) -> bool:
    return competition is not None and competition.has_feature(MAP_FLAG)


def cache_seconds() -> int:
    return int(getattr(settings, "PARTICIPANT_MAP_CACHE_SECONDS", 3600))


def min_town_count(competition) -> int:
    """Próg ochrony konkursu: wiersz ustawień albo wartość domyślna – nigdy poniżej ``MIN_TOWN_COUNT``."""
    value = (
        MapSettings.objects.filter(competition=competition).values_list("min_town_count", flat=True).first()
    )
    return max(MIN_TOWN_COUNT, value or MIN_TOWN_COUNT)


def _cache(operation: str, *args):
    """Wywołanie pamięci podręcznej, które nie wywraca strony: awaria = brak wpisu, w logu ostrzeżenie.

    ``IGNORE_EXCEPTIONS`` w ``CACHES`` połyka już błędy połączenia z Redisem; to jest druga,
    niezależna warstwa (jak w ``apps.web.page_cache``) – mapa ma się policzyć na żywo, a nie 500.
    """
    try:
        return getattr(cache, operation)(*args)
    except Exception:  # noqa: BLE001
        logger.warning("Pamięć podręczna mapy uczestników niedostępna (%s).", operation, exc_info=True)
        return None


def _generation() -> int:
    return int(_cache("get", GENERATION_KEY) or 1)


def cache_key(competition) -> str:
    return f"{CACHE_PREFIX}:v{CACHE_FORMAT}:g{_generation()}:{competition.pk}"


def invalidate(competition) -> None:
    _cache("delete", cache_key(competition))


def invalidate_all() -> None:
    """Gasi agregaty **wszystkich** konkursów naraz (np. po wgraniu nowych punktów miejscowości)."""
    _cache("set", GENERATION_KEY, _generation() + 1, None)


def edition_participants(competition, edition):
    """Profile uczestników **edycji** – queryset bez duplikatów (warunek wpisu przez ``pk__in``)."""
    from apps.accounts.models import Participant
    from apps.competitions.models import StageEntry

    base = Participant.objects.filter(competition=competition).exclude_anonymised()
    if edition is None:
        return base.none()
    entries = StageEntry.objects.filter(stage__edition=edition, participant__isnull=False).values(
        "participant_id"
    )
    return base.filter(Q(pk__in=entries) | Q(user__date_joined__gte=edition.created_at))


def _empty(edition, threshold: int) -> dict:
    return {
        "generated_at": timezone.now(),
        "edition": edition.year_label if edition is not None else "",
        "min_count": threshold,
        "total": 0,
        "schools": 0,
        "outside_directory": 0,
        "towns_total": 0,
        "towns": [],
        "others": [],
        "hidden_towns": 0,
        "hidden_people": 0,
        "unmapped": 0,
        "top_share": None,
        "dots": [],
        "labels": [],
        "legend": [],
    }


def compute(competition) -> dict:
    """Agregat od nowa, z bazy – bez pamięci podręcznej. Słownik gotowy dla szablonu."""
    from apps.accounts.models import Voivodeship
    from apps.competitions.services import current_edition
    from apps.results.statistics import width_class
    from apps.schools.models import TownLocation

    edition = current_edition(competition)
    threshold = min_town_count(competition)
    result = _empty(edition, threshold)
    if edition is None:
        return result
    participants = edition_participants(competition, edition)
    sums = participants.aggregate(
        total=Count("pk"),
        outside=Count("pk", filter=Q(school_ref__isnull=True)),
        schools=Count("school_ref", distinct=True),
    )
    grouped = list(
        participants.filter(school_ref__isnull=False)
        .values("school_ref__city_parent", "school_ref__voivodeship")
        .annotate(n=Count("pk"))
    )
    labels = dict(Voivodeship.choices)
    shown, hidden = [], {}
    for row in grouped:
        name = (row["school_ref__city_parent"] or "").strip()
        voivodeship = row["school_ref__voivodeship"]
        if name and row["n"] >= threshold:
            shown.append((name, voivodeship, row["n"]))
        else:
            bucket = hidden.setdefault(voivodeship, [0, 0])
            bucket[0] += row["n"]
            bucket[1] += 1
    # Kolejność listy: liczba malejąco, potem nazwa w postaci porównawczej („Łódź” obok „Lublina”,
    # a nie za „Żyrardowem”), potem województwo – stała przy remisach.
    shown.sort(key=lambda item: (-item[2], fold(item[0]), item[1]))
    points = {
        (city, voivodeship): (lat, lon)
        for city, voivodeship, lat, lon in TownLocation.objects.filter(
            city_parent__in={name for name, _v, _n in shown}
        ).values_list("city_parent", "voivodeship", "latitude", "longitude")
    }
    largest = shown[0][2] if shown else 0
    towns = []
    rank, previous = 0, None
    for index, (name, voivodeship, count) in enumerate(shown):
        if count != previous:
            rank, previous = index + 1, count
        label = labels.get(voivodeship, voivodeship)
        point = points.get((name, voivodeship))
        town = {
            "id": f"mu-{index + 1}",
            "name": name,
            "voivodeship": voivodeship,
            "voivodeship_label": label,
            "count": count,
            "rank": rank,
            "bar": width_class(100 * count / largest) if largest else "w0",
            "search": fold(f"{name} {label}"),
            "point": None,
        }
        if point is not None:
            x, y = geometry.project(point[1], point[0])
            r = geometry.radius(count, largest)
            town["point"] = {
                "cx": geometry.fmt(x),
                "cy": geometry.fmt(y),
                "r": geometry.fmt(r),
                "x": x,
                "y": y,
                "rv": r,
            }
        towns.append(town)
    mapped = [town for town in towns if town["point"] is not None]
    # Duże kółka pod spodem, małe na wierzchu – inaczej Warszawa przykryłaby Piaseczno.
    dots = sorted(mapped, key=lambda town: -town["count"])
    labelled = [
        {
            "x": geometry.fmt(town["point"]["x"] + town["point"]["rv"] + 3),
            "y": geometry.fmt(town["point"]["y"] + 4),
            "text": town["name"],
        }
        for town in mapped[:LABELLED_TOWNS]
    ]
    # Legenda: najmniejsze możliwe kółko (próg), dwie okrągłe wartości i największe.
    legend_values = (
        sorted({value for value in (threshold, 10, 50) if value < largest} | {largest}) if largest else []
    )
    legend = []
    for value in legend_values:
        r = geometry.radius(value, largest)
        legend.append(
            {
                "value": value,
                "r": geometry.fmt(r),
                "cx": geometry.fmt(r + 2),
                "width": geometry.fmt(2 * r + 4),
            }
        )
    others = [
        {
            "voivodeship": slug,
            "voivodeship_label": labels.get(slug, slug),
            "count": people,
            "towns": number,
            "search": fold(labels.get(slug, slug)),
        }
        for slug, (people, number) in sorted(
            hidden.items(), key=lambda item: fold(labels.get(item[0], item[0]))
        )
    ]
    total = sums["total"] or 0
    top = sum(town["count"] for town in towns[:TOP_TOWNS])
    result.update(
        {
            "total": total,
            "schools": sums["schools"] or 0,
            "outside_directory": sums["outside"] or 0,
            "towns_total": len(shown) + sum(number for _people, number in hidden.values()),
            "towns": towns,
            "others": others,
            "hidden_towns": sum(number for _people, number in hidden.values()),
            "hidden_people": sum(people for people, _number in hidden.values()),
            "unmapped": len(towns) - len(mapped),
            "top_share": round(100 * top / total) if total and towns else None,
            "dots": dots,
            "labels": labelled,
            "legend": legend,
        }
    )
    return result


def aggregate(competition) -> dict:
    """Agregat z pamięci podręcznej albo – przy chybieniu – policzony i zapisany."""
    key = cache_key(competition)
    cached = _cache("get", key)
    if cached is not None:
        return cached
    result = compute(competition)
    _cache("set", key, result, cache_seconds())
    return result


def cached_aggregate(competition) -> dict | None:
    """Wpis z pamięci podręcznej bez liczenia – dla ekranu koordynatora („ostatnie przeliczenie”)."""
    return _cache("get", cache_key(competition))


def _require_coordinator(actor, competition) -> None:
    """Rola sprawdzana w serwisie, nie tylko w widoku: komenda i przyszłe API przejdą tędy samo."""
    from apps.accounts.models import CompetitionRole
    from apps.accounts.services import has_role

    if actor is None or not getattr(actor, "is_authenticated", False):
        raise PermissionDenied("Mapę uczestników odświeża wyłącznie koordynator konkursu.")
    if not has_role(actor, competition, CompetitionRole.COORDINATOR):
        raise PermissionDenied("Mapę uczestników odświeża wyłącznie koordynator konkursu.")


def _invalidate_pages(competition) -> None:
    from apps.web.page_cache import invalidate_competition_on_commit

    invalidate_competition_on_commit(competition.pk)


def recompute(competition) -> dict:
    """Agregat od nowa **teraz**: zapis w pamięci podręcznej i zgaszenie zapisanych stron gościa."""
    result = compute(competition)
    _cache("set", cache_key(competition), result, cache_seconds())
    _invalidate_pages(competition)
    return result


def refresh(competition, actor, *, request=None) -> dict:
    """Przeliczenie na żądanie koordynatora: nowy agregat, zgaszone strony gościa, wpis w audycie."""
    from apps.core.models import audit

    _require_coordinator(actor, competition)
    result = recompute(competition)
    audit(
        actor,
        "participant_map.refreshed",
        competition,
        {
            "total": result["total"],
            "towns": len(result["towns"]),
            "hidden_towns": result["hidden_towns"],
            "min_count": result["min_count"],
        },
        request=request,
    )
    return result


def save_settings(competition, actor, *, is_enabled: bool, threshold: int, request=None) -> list[str]:
    """Włączenie/wyłączenie strony i próg ochrony. Zwraca listę zmienionych pól (pusta – bez zmian)."""
    from apps.core.models import audit

    _require_coordinator(actor, competition)
    threshold = max(MIN_TOWN_COUNT, int(threshold))
    was_enabled = enabled(competition)
    previous = min_town_count(competition)
    diff: dict = {}
    if was_enabled != is_enabled:
        competition.feature_flags = {**(competition.feature_flags or {}), MAP_FLAG: bool(is_enabled)}
        # ``post_save`` konkursu unieważnia zapisane strony gościa (``apps.web.page_cache``) – menu
        # i spis dokumentów zmieniają się razem z flagą.
        competition.save(update_fields=["feature_flags"])
        diff["enabled"] = [was_enabled, bool(is_enabled)]
    if previous != threshold:
        MapSettings.objects.update_or_create(competition=competition, defaults={"min_town_count": threshold})
        diff["min_count"] = [previous, threshold]
    if not diff:
        return []
    invalidate(competition)
    _invalidate_pages(competition)
    audit(actor, "participant_map.settings_changed", competition, diff, request=request)
    return list(diff)
