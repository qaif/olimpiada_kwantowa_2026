"""Zestaw startowy regionów konkursu: kraj, szesnaście województw i „poza Polską”.

Migracja ``accounts.0026_regions_from_voivodeships`` wpisała ten zestaw **konkursom stojącym
w bazie w chwili jej wykonania**. Konkurs założony później – komendą ``create_competition``
albo kreatorem ``/setup/`` – nie ma jak przez tamtą migrację przejść, a bez ani jednego regionu
ekran „Regiony” i formularz rejestracji przy włączonej fladze ``custom_regions`` pokazałyby pustą
listę wyboru. Ten moduł jest tą samą wiedzą wystawioną jako funkcja.

**Dlaczego kopia, a nie import z migracji.** Z migracji nie importuje się niczego: to jest zapis
historii schematu, który wolno zwinąć (``squashmigrations``) i który chodzi na modelach
historycznych, a nie na dzisiejszych. Kopią jest tutaj wyłącznie **układ** zestawu (kraj → 16
województw → „poza Polską”); same nazwy i kody idą z :class:`apps.accounts.models.Voivodeship`,
czyli z tego samego, jedynego źródła, z którego bierze je tamta migracja. Rozjazd listy województw
jest więc niemożliwy, a rozjazd układu wyłapuje
``apps/tenancy/tests/test_create_competition.py::test_new_competition_gets_the_starting_regions``.

**Czego ten moduł nie robi:** nie dotyka ``Participant.district`` ani ``CommitteeMember.district``
(backfill profili był jednorazową robotą migracji – nowy konkurs nie ma czego backfillować)
i nie kasuje regionów dopisanych przez organizatora.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Region, RegionLevel, Voivodeship

#: Kraj, pod którym stoi szesnaście województw. Kod, nazwa, poziom i pozycja **dokładnie** takie,
#: jak w migracji ``accounts.0026`` – region o tym samym kodzie ma w każdym konkursie znaczyć to
#: samo, bo to po kodzie poznaje go eksport, filtr panelu i reguła konfliktu interesów.
COUNTRY_CODE = "pl"
COUNTRY_NAME = "Polska"

#: Region dla uczestników spoza Polski. Powstaje **zawsze**, ale nieaktywny: konkurs, który nie
#: dopuszcza zagranicy, nie pokaże go w formularzu ani razu. ``counts_for_conflict=False``, bo
#: dwoje uczestników z zagranicy nie jest ze sobą w konflikcie z tytułu miejsca zamieszkania.
ABROAD_CODE = "poza-polska"
ABROAD_NAME = "poza Polską"
ABROAD_POSITION = 99

#: Ile wierszy ma zestaw startowy: kraj + szesnaście województw + „poza Polską”.
STARTING_SET_SIZE = len(Voivodeship.choices) + 2


def default_regions_for(competition) -> list[Region]:
    """Zestaw startowy regionów tego konkursu; zwraca wiersze w kolejności wyświetlania.

    Idempotentnie: region o danym kodzie jest w konkursie jeden (więz
    ``accounts_region_unique_code``), więc powtórzone wywołanie niczego nie mnoży. **Wiersza już
    istniejącego nie nadpisujemy** – nazwa i kolejność poprawione przez organizatora w panelu mają
    przeżyć ponowny przebieg komendy, a to jest jedyna droga, którą ta funkcja może zostać wołana
    drugi raz dla tego samego konkursu.
    """
    country, _ = Region.objects.get_or_create(
        competition=competition,
        code=COUNTRY_CODE,
        defaults={"name": COUNTRY_NAME, "level": RegionLevel.COUNTRY, "position": 0},
    )
    regions = [country]
    for position, (code, label) in enumerate(Voivodeship.choices, start=1):
        region, _ = Region.objects.get_or_create(
            competition=competition,
            code=code,
            defaults={
                "name": str(label),
                "level": RegionLevel.REGION,
                "parent": country,
                "position": position,
            },
        )
        regions.append(region)
    abroad, _ = Region.objects.get_or_create(
        competition=competition,
        code=ABROAD_CODE,
        defaults={
            "name": ABROAD_NAME,
            "level": RegionLevel.COUNTRY,
            "position": ABROAD_POSITION,
            "is_active": False,
            "counts_for_conflict": False,
        },
    )
    regions.append(abroad)
    return regions


# --- konkurs międzynarodowy: kraje zamiast województw (docs/tasks/REG-01.md) --------------------

#: Kody zestawu startowego, które w konkursie „krajowym” przestają być do wyboru: szesnaście
#: województw i „poza Polską”. Polska (``pl``) do tej listy **nie** należy – jest krajem jak każdy.
STARTING_SUBDIVISION_CODES: frozenset[str] = frozenset({*Voivodeship.values, ABROAD_CODE})

#: Angielska nazwa Polski na liście krajów. Region ``pl`` dostaje ją wyłącznie wtedy, gdy nosi
#: jeszcze nazwę startową („Polska”) – nazwy poprawionej przez organizatora nie nadpisujemy.
POLAND_NAME_EN = "Poland"


@dataclass(frozen=True)
class CountrySwitch:
    """Podsumowanie :func:`switch_to_countries` – to, co komenda wypisuje operatorowi."""

    flag_enabled: bool
    added: int
    poland_renamed: bool
    deactivated: int
    #: Uczestnicy, których region jest teraz nieaktywny – trzeba ich przypisać do kraju ręcznie.
    stranded_participants: int


def switch_to_countries(competition, *, actor=None) -> CountrySwitch:
    """Przestawia konkurs na podział **na kraje** (REG-01 § 1.3). Idempotentnie, w jednej transakcji.

    1. włącza flagę ``custom_regions`` – bez niej formularze dalej pytają o województwo,
    2. zakłada brakujące kraje z :data:`apps.accounts.countries.COUNTRIES` (poziom ``COUNTRY``,
       bez rodzica, kolejność alfabetyczna, liczą się do konfliktu interesów),
    3. **dezaktywuje** (nie kasuje) szesnaście województw i „poza Polską”: wiersze mogą wskazywać
       profile z poprzednich lat (``PROTECT``), a wycofanie z listy wyboru to dokładnie to, do czego
       służy ``Region.is_active``,
    4. region ``pl`` zostaje krajem; nazwę „Polska” zmienia na „Poland” tylko wtedy, gdy nikt jej
       nie poprawił.

    Kraj już istniejący (np. dopisany ręcznie w ekranie „Regiony”) zostaje nietknięty – drugi
    przebieg nie ma czego zmienić i niczego nie zmienia. Wpis audytu powstaje przy każdym
    przebiegu, który cokolwiek zmienił.
    """
    from django.db import transaction

    from apps.core.models import audit

    from .countries import COUNTRIES
    from .models import Participant
    from .services import CUSTOM_REGIONS_FLAG

    with transaction.atomic():
        flags = dict(competition.feature_flags or {})
        flag_enabled = flags.get(CUSTOM_REGIONS_FLAG) is not True
        if flag_enabled:
            flags[CUSTOM_REGIONS_FLAG] = True
            competition.feature_flags = flags
            competition.save(update_fields=["feature_flags"])

        existing = {region.code: region for region in Region.objects.for_competition(competition)}
        added = 0
        poland_renamed = False
        for position, (code, name) in enumerate(COUNTRIES, start=1):
            region = existing.get(code)
            if region is None:
                Region.objects.create(
                    competition=competition,
                    code=code,
                    name=name,
                    level=RegionLevel.COUNTRY,
                    position=position,
                    counts_for_conflict=True,
                )
                added += 1
            elif code == COUNTRY_CODE and region.name == COUNTRY_NAME:
                # Polska ze zbioru startowego: ta sama pozycja alfabetyczna, co reszta listy, i nazwa
                # w języku listy. Warunek na nazwie jest bramką „nikt tego nie dotykał”.
                region.name = POLAND_NAME_EN
                region.position = position
                region.is_active = True
                region.save(update_fields=["name", "position", "is_active"])
                poland_renamed = True

        deactivated = (
            Region.objects.for_competition(competition)
            .filter(code__in=STARTING_SUBDIVISION_CODES, is_active=True)
            .update(is_active=False)
        )
        stranded = Participant.objects.filter(
            region__competition=competition, region__is_active=False
        ).count()
        result = CountrySwitch(
            flag_enabled=flag_enabled,
            added=added,
            poland_renamed=poland_renamed,
            deactivated=deactivated,
            stranded_participants=stranded,
        )
        if flag_enabled or added or poland_renamed or deactivated:
            audit(
                actor,
                "regions.countries_enabled",
                competition,
                {
                    "flag_enabled": flag_enabled,
                    "added": added,
                    "poland_renamed": poland_renamed,
                    "deactivated": deactivated,
                },
            )
    return result


def region_noun(competition) -> str:
    """Słowo na podział terytorialny konkursu: „Województwo”, „Kraj” albo „Region” (REG-01 § 1.1).

    Przy wyłączonej fladze ``custom_regions`` – „Województwo”, **bez zapytania** (Olimpiada
    Kwantowa nie płaci za tę funkcję niczym). Przy włączonej: „Kraj”, gdy każdy aktywny region jest
    na poziomie ``COUNTRY``, w przeciwnym razie neutralne „Region”. Odpowiedź jest zapamiętywana na
    obiekcie konkursu – nagłówek tabeli, etykieta pola i filtr pytają o nią w jednym żądaniu kilka
    razy.

    Napis przechodzi przez gettext: uczestnik konkursu międzynarodowego czyta „Country” albo
    „国家”, a nie polskie słowo.
    """
    from django.utils.translation import gettext

    from .services import CUSTOM_REGIONS_FLAG

    if competition is None or not competition.has_feature(CUSTOM_REGIONS_FLAG):
        return gettext("Województwo")
    level = getattr(competition, "_region_level_cache", None)
    if level is None:
        levels = set(
            Region.objects.for_competition(competition).active().values_list("level", flat=True).distinct()
        )
        level = RegionLevel.COUNTRY if levels == {RegionLevel.COUNTRY} else RegionLevel.REGION
        try:
            competition._region_level_cache = level
        except AttributeError:  # pragma: no cover - obiekt bez zapisywalnych atrybutów
            pass
    return gettext("Kraj") if level == RegionLevel.COUNTRY else gettext("Region")
