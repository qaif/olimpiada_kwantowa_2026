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
