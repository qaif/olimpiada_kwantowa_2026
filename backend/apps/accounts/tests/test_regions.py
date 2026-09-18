"""Model ``Region``: zestaw startowy, zakresowanie konkursem i droga od ``district`` do regionu.

Dwie rzeczy są tu przedmiotem testu i obie są regułami, a nie szczegółami wykonania:

- **zestaw startowy jest odwzorowaniem listy województw.** Kody, nazwy, kolejność i poziom biorą
  się z ``Voivodeship``, więc backfill profili jest złączeniem po kolumnie ``district``, a nie mapą
  przepisaną ręcznie (§ 1.4.3). Konkurs #1 w bazie testowej ma ten zestaw z migracji
  ``accounts.0026`` – tak samo, jak dostanie go produkcja;
- **podział należy do konkursu.** Region konkursu A nie jest widoczny przez manager konkursu B
  (§ 5.7), a kod jest unikalny **w konkursie**, a nie na platformie: dwie olimpiady mogą mieć
  ``mazowieckie`` i nie jest to ten sam wiersz.

Czego tu **nie** ma: reguły konfliktu interesów. Ta jest w ``apps/grading`` i należy do T19 –
tutaj sprawdzamy wyłącznie, że ``counts_for_conflict`` jest ustawione tam, gdzie ma być, bo to
migracja, a nie reguła, decyduje o regionie „poza Polską”.
"""

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.accounts.models import (
    Region,
    RegionLevel,
    Voivodeship,
    region_for_district,
)

from .factories import CommitteeMemberFactory, ParticipantFactory

#: Kody spoza listy województw, które migracja zakłada każdemu konkursowi.
COUNTRY_CODE = "pl"
ABROAD_CODE = "poza-polska"


def voivodeship_regions(competition):
    """Regiony konkursu odpowiadające województwom – w kolejności, w jakiej je pokazujemy."""
    return list(
        Region.objects.for_competition(competition)
        .filter(level=RegionLevel.REGION)
        .order_by("position", "id")
    )


@pytest.mark.django_db
def test_regions_mirror_the_voivodeship_list(competition):
    """Szesnaście regionów konkursu to szesnaście województw – co do kodu, nazwy i kolejności."""
    regions = voivodeship_regions(competition)

    assert [region.code for region in regions] == list(Voivodeship.values)
    assert [region.name for region in regions] == [str(label) for label in Voivodeship.labels]
    # Kolejność jest kolejnością deklaracji w ``Voivodeship``, a kraj stoi przed nimi (pozycja 0).
    assert [region.position for region in regions] == list(range(1, len(Voivodeship.values) + 1))


@pytest.mark.django_db
def test_the_voivodeships_stand_under_one_country(competition):
    country = Region.objects.for_competition(competition).get(code=COUNTRY_CODE)

    assert country.level == RegionLevel.COUNTRY
    assert country.parent_id is None
    assert {region.parent_id for region in voivodeship_regions(competition)} == {country.pk}


@pytest.mark.django_db
def test_the_abroad_region_exists_inactive_and_outside_the_conflict_rule(competition):
    """„poza Polską” powstaje zawsze, ale nie pokazuje się nikomu i nie tworzy konfliktu.

    Dwóch uczestników z zagranicy nie jest ze sobą w konflikcie z tytułu miejsca zamieszkania –
    to jest powód istnienia ``counts_for_conflict``, a nie ozdobnik.
    """
    abroad = Region.objects.for_competition(competition).get(code=ABROAD_CODE)

    assert abroad.is_active is False
    assert abroad.counts_for_conflict is False
    assert abroad not in Region.objects.for_competition(competition).active()


@pytest.mark.django_db
def test_active_shows_every_voivodeship_and_the_country(competition):
    """Jedno miejsce na regułę „co wolno pokazać”: formularz, komitet i filtry mają ten sam zestaw."""
    active = Region.objects.for_competition(competition).active()

    assert {region.code for region in active} == {COUNTRY_CODE, *Voivodeship.values}


@pytest.mark.django_db
def test_regions_of_one_competition_are_invisible_to_another(competition, other_competition):
    """Reguła izolacji § 5.7 na poziomie queryseta – tańsza i bliżej reguły niż test widoku."""
    assert not Region.objects.for_competition(competition).filter(competition=other_competition).exists()
    # Konkurs założony po migracjach nie ma **żadnego** regionu: podział jest danymi konkursu,
    # a nie instalacji, więc nie dziedziczy się po sąsiedzie.
    assert not Region.objects.for_competition(other_competition).exists()
    assert Region.objects.for_competition(None).count() == 0


@pytest.mark.django_db
def test_the_same_code_in_two_competitions_is_two_regions(competition, other_competition):
    own = Region.objects.create(
        competition=other_competition, code=Voivodeship.MAZOWIECKIE, name="mazowieckie"
    )

    assert own.pk != Region.objects.for_competition(competition).get(code=Voivodeship.MAZOWIECKIE).pk


@pytest.mark.django_db
def test_the_code_is_unique_within_a_competition(competition):
    with pytest.raises(IntegrityError), transaction.atomic():
        Region.objects.create(
            competition=competition, code=Voivodeship.MAZOWIECKIE, name="drugie mazowieckie"
        )


@pytest.mark.django_db
def test_a_region_in_use_cannot_be_deleted(competition):
    """``PROTECT``: skasowanie regionu zabrałoby profilom okręg, w którym wystartowały."""
    region = Region.objects.for_competition(competition).get(code=Voivodeship.MAZOWIECKIE)
    ParticipantFactory(competition=competition, district=Voivodeship.MAZOWIECKIE, region=region)

    with pytest.raises(ProtectedError):
        region.delete()


@pytest.mark.django_db
def test_the_region_is_optional_on_every_profile(competition):
    """Profil bez regionu jest poprawny: konkurs bez własnego podziału ma tę kolumnę pustą."""
    participant = ParticipantFactory(competition=competition)
    member = CommitteeMemberFactory(competition=competition)

    assert participant.region_id is None
    assert member.region_id is None
    # ``district`` zostaje i zostaje wypełnione – to jest cała reguła § 1.4.2.
    assert participant.district == Voivodeship.MAZOWIECKIE


@pytest.mark.django_db
def test_the_flag_is_off_for_the_first_competition(competition):
    """Konkurs #1 nie włącza ``custom_regions``, więc wiersze leżą nieużywane (§ 0.6)."""
    assert competition.has_feature("custom_regions") is False


@pytest.mark.django_db
@pytest.mark.parametrize(
    "district",
    ["mazowieckie", "Mazowieckie", "woj. mazowieckie", "mazowiecki", "MAZOWIECKIE"],
    ids=["slug", "label", "prefix", "adjective", "upper"],
)
def test_region_for_district_maps_every_spelling_that_the_old_data_has(competition, district):
    """Ta sama tolerancja, co ``normalize_voivodeship`` – inaczej stare profile zostałyby bez regionu."""
    region = region_for_district(competition, district)

    assert region is not None
    assert region.code == Voivodeship.MAZOWIECKIE


@pytest.mark.django_db
def test_region_for_district_maps_the_label_with_diacritics(competition):
    assert region_for_district(competition, "łódzkie").code == Voivodeship.LODZKIE


@pytest.mark.django_db
def test_region_for_district_prefers_the_code_the_competition_really_has(competition):
    """Własny podział wygrywa z domysłem: napis jest najpierw próbowany dosłownie.

    ``mazowiecki`` jest dla ``normalize_voivodeship`` formą przymiotnikową województwa, ale konkurs
    z własnym podziałem może mieć region **o takim właśnie kodzie** – i to on ma wygrać.
    """
    own = Region.objects.create(competition=competition, code="mazowiecki", name="Okręg mazowiecki")

    assert region_for_district(competition, "mazowiecki") == own


@pytest.mark.django_db
@pytest.mark.parametrize(
    "district", [None, "", "   ", "nie-ma-takiego"], ids=["none", "empty", "blank", "unknown"]
)
def test_region_for_district_never_guesses(competition, district):
    assert region_for_district(competition, district) is None


@pytest.mark.django_db
def test_region_for_district_without_a_competition_is_empty(competition):  # noqa: ARG001 - baza
    """Brak konkursu nie widzi niczego – tak samo jak ``for_competition(None)``."""
    assert region_for_district(None, Voivodeship.MAZOWIECKIE) is None


@pytest.mark.django_db
def test_region_for_district_does_not_reach_into_another_competition(competition, other_competition):
    assert region_for_district(other_competition, Voivodeship.MAZOWIECKIE) is None
