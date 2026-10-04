"""Kraje zamiast województw (docs/tasks/REG-01.md).

Pytania tego modułu:

- **lista krajów** – kody alfa-2 małymi literami, bez powtórzeń, Polska jest krajem jak inne,
- **przestawienie konkursu** (``switch_to_countries`` i komenda ``regions_countries``): flaga,
  kraje, dezaktywacja (nie kasowanie) zestawu startowego, idempotencja, próba na sucho, audyt,
- **nazwa regionu** (``get_district_display``): konkurs z województwami nie zmienia ani bajtu,
  konkurs z krajami pokazuje „Germany”, a nie „de”,
- **słowo na podział** (``region_noun``): „Województwo” bez zapytania, „Kraj” po przestawieniu,
- ``create_competition --regions countries``.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.countries import COUNTRIES, COUNTRY_NAMES, country_name
from apps.accounts.models import Participant, Region, RegionLevel, Voivodeship, region_display_name
from apps.accounts.regions import (
    ABROAD_CODE,
    STARTING_SUBDIVISION_CODES,
    region_noun,
    switch_to_countries,
)
from apps.accounts.services import CUSTOM_REGIONS_FLAG
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


# --- lista krajów --------------------------------------------------------------------------------


def test_country_list_is_iso_alpha2_lowercase_and_unique():
    codes = [code for code, _name in COUNTRIES]
    assert len(codes) == len(set(codes))
    assert all(len(code) == 2 and code.isascii() and code.islower() for code in codes)
    # 193 państwa ONZ + 2 obserwatorów + Tajwan, Kosowo, Hongkong, Makau.
    assert len(codes) == 199
    assert {"pl", "de", "us", "cn", "in", "tw", "xk", "hk", "mo", "va", "ps"} <= set(codes)


def test_country_codes_never_collide_with_the_starting_set():
    assert not set(COUNTRY_NAMES) & STARTING_SUBDIVISION_CODES


def test_country_name_lookup():
    assert country_name("DE") == "Germany"
    assert country_name("zz") == ""


# --- przestawienie konkursu ---------------------------------------------------------------------


def test_switch_enables_the_flag_adds_countries_and_deactivates_voivodeships(competition):
    result = switch_to_countries(competition)

    competition.refresh_from_db()
    assert competition.has_feature(CUSTOM_REGIONS_FLAG)
    assert result.flag_enabled
    active = Region.objects.for_competition(competition).active()
    assert set(active.values_list("code", flat=True)) == set(COUNTRY_NAMES)
    assert set(active.values_list("level", flat=True)) == {RegionLevel.COUNTRY}
    # Polska zostaje (z angielską nazwą), województwa i „poza Polską” – nieaktywne, ale są.
    poland = Region.objects.for_competition(competition).get(code="pl")
    assert poland.name == "Poland" and poland.is_active
    assert result.poland_renamed
    starting = Region.objects.for_competition(competition).filter(code__in=STARTING_SUBDIVISION_CODES)
    assert starting.count() == len(Voivodeship.values) + 1
    assert not starting.filter(is_active=True).exists()
    assert result.deactivated == len(Voivodeship.values)  # „poza Polską” był nieaktywny od początku
    assert result.added == len(COUNTRIES) - 1
    assert AuditLog.objects.filter(action="regions.countries_enabled").count() == 1


def test_switch_is_idempotent(competition):
    switch_to_countries(competition)
    second = switch_to_countries(competition)

    assert (second.flag_enabled, second.added, second.poland_renamed, second.deactivated) == (
        False,
        0,
        False,
        0,
    )
    assert AuditLog.objects.filter(action="regions.countries_enabled").count() == 1


def test_switch_keeps_a_name_the_organizer_changed(competition):
    Region.objects.for_competition(competition).filter(code="pl").update(name="Rzeczpospolita Polska")

    switch_to_countries(competition)

    assert Region.objects.for_competition(competition).get(code="pl").name == "Rzeczpospolita Polska"


def test_switch_reports_participants_left_on_an_inactive_region(competition):
    from apps.accounts.tests.factories import ParticipantFactory

    participant = ParticipantFactory(district="mazowieckie")
    participant.region = Region.objects.for_competition(competition).get(code="mazowieckie")
    participant.save(update_fields=["region"])

    result = switch_to_countries(competition)

    assert result.stranded_participants == 1
    # Profil nietknięty – przypisanie kraju jest decyzją koordynatora.
    assert Participant.objects.get(pk=participant.pk).district == "mazowieckie"


def test_switch_does_not_touch_another_competition(competition, other_competition):
    switch_to_countries(other_competition)

    assert not competition.has_feature(CUSTOM_REGIONS_FLAG)
    assert Region.objects.for_competition(competition).filter(code="de").count() == 0
    assert Region.objects.for_competition(competition).get(code="mazowieckie").is_active


def test_command_dry_run_changes_nothing(competition, capsys):
    call_command("regions_countries", "--competition", competition.slug, "--dry-run")

    competition.refresh_from_db()
    assert not competition.has_feature(CUSTOM_REGIONS_FLAG)
    assert not Region.objects.for_competition(competition).filter(code="de").exists()
    out = capsys.readouterr().out
    assert "[próba – nic nie zapisano]" in out
    assert f"dodane kraje: {len(COUNTRIES) - 1}" in out


def test_command_applies_and_reports(competition, capsys):
    call_command("regions_countries", "--competition", competition.slug)

    competition.refresh_from_db()
    assert competition.has_feature(CUSTOM_REGIONS_FLAG)
    assert "dezaktywowane regiony startowe: 16" in capsys.readouterr().out


def test_command_refuses_an_unknown_competition():
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call_command("regions_countries", "--competition", "nie-ma-takiego")


# --- nazwa regionu ------------------------------------------------------------------------------


def test_voivodeship_label_is_unchanged_and_costs_no_query(competition):
    participant = Participant(
        district="slaskie", region=Region.objects.for_competition(competition).get(code="slaskie")
    )

    with CaptureQueriesContext(connection) as queries:
        assert participant.get_district_display() == Voivodeship("slaskie").label
    assert len(queries) == 0


def test_country_name_replaces_the_raw_code(competition):
    switch_to_countries(competition)
    germany = Region.objects.for_competition(competition).get(code="de")

    assert Participant(district="de", region=germany).get_district_display() == "Germany"
    assert region_display_name(Participant(district="de", region=germany)) == "Germany"


def test_unknown_legacy_value_stays_raw(competition):
    """Stary zapis niekanoniczny nie zamienia się w nazwę regionu z backfillu."""
    region = Region.objects.for_competition(competition).get(code="mazowieckie")

    assert (
        Participant(district="woj. Mazowieckie", region=region).get_district_display() == "woj. Mazowieckie"
    )


def test_empty_district_keeps_its_value():
    from apps.accounts.models import CommitteeMember

    assert CommitteeMember(district=None).get_district_display() is None


# --- słowo na podział ---------------------------------------------------------------------------


def test_region_noun_without_the_flag_needs_no_query(competition):
    with CaptureQueriesContext(connection) as queries:
        assert region_noun(competition) == "Województwo"
    assert len(queries) == 0


def test_region_noun_says_country_after_the_switch(competition):
    switch_to_countries(competition)
    competition.refresh_from_db()

    assert region_noun(competition) == "Kraj"


def test_region_noun_is_neutral_for_mixed_levels(competition):
    flags = dict(competition.feature_flags or {})
    flags[CUSTOM_REGIONS_FLAG] = True
    competition.feature_flags = flags
    competition.save(update_fields=["feature_flags"])

    assert region_noun(competition) == "Region"


# --- create_competition --regions countries -----------------------------------------------------


def test_create_competition_with_countries(competition):  # noqa: ARG001 - Konkurs #1 obok
    from apps.tenancy.models import Competition
    from apps.tenancy.tests.factories import enforce_memberships_everywhere

    enforce_memberships_everywhere()
    call_command(
        "create_competition",
        slug="iqo",
        name="International Quantum Olympiad",
        domain="iqo.invalid",
        from_template="pusty",
        regions="countries",
    )

    created = Competition.objects.get(slug="iqo")
    assert created.has_feature(CUSTOM_REGIONS_FLAG)
    active = Region.objects.for_competition(created).active()
    assert active.count() == len(COUNTRIES)
    assert not active.filter(code=ABROAD_CODE).exists()


def test_create_competition_defaults_to_voivodeships(competition):  # noqa: ARG001
    from apps.tenancy.models import Competition
    from apps.tenancy.tests.factories import enforce_memberships_everywhere

    enforce_memberships_everywhere()
    call_command(
        "create_competition",
        slug="fizyczna",
        name="Olimpiada Fizyczna",
        domain="fizyczna.invalid",
        from_template="pusty",
    )

    created = Competition.objects.get(slug="fizyczna")
    assert not created.has_feature(CUSTOM_REGIONS_FLAG)
    assert Region.objects.for_competition(created).active().filter(code="mazowieckie").exists()


# --- konflikt interesów na krajach (REG-01 § 1.5) ------------------------------------------------


def test_conflict_of_interest_compares_countries(competition):
    from apps.accounts.models import CommitteeMember, CommitteeStatus
    from apps.accounts.tests.factories import UserFactory
    from apps.competitions.models import StageKind
    from apps.competitions.tests.factories import StageFactory
    from apps.grading.services import has_district_conflict

    switch_to_countries(competition)
    competition.refresh_from_db()
    germany = Region.objects.for_competition(competition).get(code="de")
    france = Region.objects.for_competition(competition).get(code="fr")
    member = CommitteeMember.objects.create(
        user=UserFactory(),
        competition=competition,
        status=CommitteeStatus.ACTIVE,
        district="de",
        region=germany,
    )
    stage = StageFactory(kind=StageKind.DISTRICT)

    assert has_district_conflict(member, stage, "de", participant_region=germany)
    assert not has_district_conflict(member, stage, "fr", participant_region=france)
