"""Konflikt interesów wyrażony na regionach (``docs/UNIWERSALNY-ETAP-2.md`` § 1.4.4).

Zadanie o **wysokim ryzyku**: pominięcie jednego z pięciu miejsc wołających regułę daje recenzenta
przydzielonego do pracy ucznia z własnego okręgu, co jest naruszeniem regulaminu, a nie usterką
techniczną. Dlatego plik sprawdza nie tylko samą regułę, ale każdą drogę, którą przydział może pójść:
automat, regułę „to zadanie recenzuje ta osoba”, przydział ręczny i trzeciego recenzenta.

Trzy warunki, które **nie zmieniają się** przy żadnej fladze i mają tu własne asercje: reguła
dotyczy wyłącznie etapu wojewódzkiego, brak wartości u członka komitetu nikogo nie wyklucza,
a ``district_verified`` nie bierze w niej udziału.

Najważniejszy test tego pliku to ``test_wynik_reguly_jest_ten_sam_dla_kazdej_pary_wojewodztw``:
dla wszystkich par szesnastu województw odpowiedź z flagą włączoną jest **identyczna** z odpowiedzią
sprzed etapu 2. Dopóki tak jest, włączenie ``custom_regions`` w Olimpiadzie Kwantowej nie zmienia
ani jednego przydziału.
"""

from __future__ import annotations

import pytest

from apps.accounts.models import Region, Voivodeship
from apps.accounts.tests.factories import ActiveReviewerFactory, CommitteeMemberFactory
from apps.competitions.models import StageKind
from apps.core.api import DomainError
from apps.grading.models import Review, ReviewStatus
from apps.grading.services import (
    add_problem_reviewer_rule,
    assign_reviewer_to_submission,
    assign_reviewers,
    assign_third_reviewer,
    conflict_by_region,
    has_district_conflict,
    participant_conflict_region,
)
from apps.submissions.models import SubmissionStatus

from .conftest import locked_submission

pytestmark = pytest.mark.django_db

FEATURE = "custom_regions"


def enable_regions(competition):
    """Włącza flagę obszaru tą samą drogą, którą zrobi to wdrożenie: wartością w bazie."""
    competition.feature_flags = {**(competition.feature_flags or {}), FEATURE: True}
    competition.save(update_fields=["feature_flags"])
    return competition


def region(competition, code: str) -> Region:
    """Region konkursu o tym kodzie – zestaw startowy wpisuje migracja ``accounts.0026``."""
    return Region.objects.for_competition(competition).get(code=code)


# --- sama reguła -------------------------------------------------------------------------------


def test_poza_etapem_wojewodzkim_konfliktu_nie_ma(stage, competition):
    """Etap eliminacyjny – i to bez czytania flagi: rodzaj etapu rozstrzyga pierwszy."""
    enable_regions(competition)
    member = ActiveReviewerFactory(district=Voivodeship.MAZOWIECKIE)

    assert stage.kind != StageKind.DISTRICT
    assert has_district_conflict(member, stage, Voivodeship.MAZOWIECKIE) is False
    assert conflict_by_region(stage) is False


def test_bez_flagi_regula_porownuje_wojewodztwa(district_stage):
    member = ActiveReviewerFactory(district=Voivodeship.MAZOWIECKIE)

    assert conflict_by_region(district_stage) is False
    assert has_district_conflict(member, district_stage, Voivodeship.MAZOWIECKIE) is True
    assert has_district_conflict(member, district_stage, Voivodeship.MALOPOLSKIE) is False


def test_z_flaga_ten_sam_region_jest_konfliktem(district_stage, competition):
    enable_regions(competition)
    mazowieckie = region(competition, Voivodeship.MAZOWIECKIE)
    member = ActiveReviewerFactory(district=Voivodeship.MAZOWIECKIE, region=mazowieckie)

    assert conflict_by_region(district_stage) is True
    assert has_district_conflict(member, district_stage, participant_region=mazowieckie) is True


def test_z_flaga_inny_region_to_brak_konfliktu(district_stage, competition):
    enable_regions(competition)
    member = ActiveReviewerFactory(region=region(competition, Voivodeship.MAZOWIECKIE))

    conflicted = has_district_conflict(
        member, district_stage, participant_region=region(competition, Voivodeship.POMORSKIE)
    )

    assert conflicted is False


def test_region_spoza_konfliktu_nie_tworzy_konfliktu(district_stage, competition):
    """„Poza Polską”: dwoje uczestników z zagranicy nie jest ze sobą w konflikcie."""
    enable_regions(competition)
    abroad = region(competition, "poza-polska")
    member = ActiveReviewerFactory(district="", region=abroad)

    assert abroad.counts_for_conflict is False
    assert has_district_conflict(member, district_stage, participant_region=abroad) is False


def test_czlonek_bez_wartosci_nie_jest_wykluczony_z_niczego(district_stage, competition):
    """Decyzja organizatora – i obowiązuje w obie strony flagi."""
    member = ActiveReviewerFactory(district="", region=None)

    assert has_district_conflict(member, district_stage, Voivodeship.MAZOWIECKIE) is False

    enable_regions(competition)
    assert (
        has_district_conflict(
            member, district_stage, participant_region=region(competition, Voivodeship.MAZOWIECKIE)
        )
        is False
    )


def test_niepotwierdzone_wojewodztwo_nadal_jest_konfliktem(district_stage, competition):
    """``district_verified`` nie bierze udziału w regule – ani przed etapem 2, ani po nim."""
    enable_regions(competition)
    member = CommitteeMemberFactory(district=Voivodeship.MAZOWIECKIE, district_verified=False)

    assert (
        has_district_conflict(
            member, district_stage, participant_region=region(competition, Voivodeship.MAZOWIECKIE)
        )
        is True
    )


def test_profil_bez_regionu_odtwarza_go_z_wojewodztwa(district_stage, competition):
    """Profil założony przed włączeniem flagi ma ``region = NULL`` – i nadal podlega regule.

    Gdyby brak kolumny znaczył „brak konfliktu”, dzień włączenia flagi byłby dniem, w którym
    recenzent dostaje pracę ucznia z własnego województwa.
    """
    enable_regions(competition)
    member = ActiveReviewerFactory(district=Voivodeship.MAZOWIECKIE, region=None)

    assert has_district_conflict(member, district_stage, Voivodeship.MAZOWIECKIE) is True
    assert has_district_conflict(member, district_stage, Voivodeship.POMORSKIE) is False


def test_wynik_reguly_jest_ten_sam_dla_kazdej_pary_wojewodztw(district_stage, competition):
    """256 par: z flagą i bez flagi reguła musi odpowiedzieć **to samo** dla Konkursu #1."""
    members = {code: ActiveReviewerFactory(district=code) for code in Voivodeship.values}

    before = {
        (member_code, participant_code): has_district_conflict(
            members[member_code], district_stage, participant_code
        )
        for member_code in Voivodeship.values
        for participant_code in Voivodeship.values
    }

    enable_regions(competition)
    district_stage.refresh_from_db()
    regions = {code: region(competition, code) for code in Voivodeship.values}
    after = {
        (member_code, participant_code): has_district_conflict(
            members[member_code],
            district_stage,
            participant_code,
            participant_region=regions[participant_code],
        )
        for member_code in Voivodeship.values
        for participant_code in Voivodeship.values
    }

    assert after == before
    assert sum(before.values()) == len(Voivodeship.values)


def test_bez_flagi_region_uczestnika_nie_jest_czytany(district_stage, django_assert_num_queries):
    """Konkurs bez regionów nie płaci za nie ani jednym zapytaniem (§ 5.6)."""
    submission = locked_submission(district_stage)
    participant = submission.entry.participant
    conflict_by_region(district_stage)  # rozgrzanie: konkurs etapu czytany raz na obiekt

    with django_assert_num_queries(0):
        assert participant_conflict_region(district_stage, participant) is None


# --- pięć dróg przydziału ----------------------------------------------------------------------


def test_automat_nie_daje_pracy_recenzentowi_z_tego_samego_regionu(district_stage, competition):
    enable_regions(competition)
    mazowieckie = region(competition, Voivodeship.MAZOWIECKIE)
    conflicted = ActiveReviewerFactory(district=Voivodeship.MAZOWIECKIE, region=mazowieckie)
    ActiveReviewerFactory(district=Voivodeship.POMORSKIE, region=region(competition, Voivodeship.POMORSKIE))
    ActiveReviewerFactory(district=Voivodeship.LUBELSKIE, region=region(competition, Voivodeship.LUBELSKIE))
    submission = locked_submission(district_stage)
    submission.entry.participant.region = mazowieckie
    submission.entry.participant.save(update_fields=["region"])

    assign_reviewers(district_stage)

    assigned = set(Review.objects.filter(submission=submission).values_list("reviewer_id", flat=True))
    assert conflicted.pk not in assigned
    assert len(assigned) == 2


def test_regula_zadania_nie_lamie_konfliktu_regionu(district_stage, competition):
    enable_regions(competition)
    mazowieckie = region(competition, Voivodeship.MAZOWIECKIE)
    conflicted = ActiveReviewerFactory(district=Voivodeship.MAZOWIECKIE, region=mazowieckie)
    submission = locked_submission(district_stage)
    submission.entry.participant.region = mazowieckie
    submission.entry.participant.save(update_fields=["region"])

    result = add_problem_reviewer_rule(submission.problem, conflicted)

    assert result["assigned"] == 0
    assert result["conflicts"] == 1


def test_przydzial_reczny_odmawia_przy_tym_samym_regionie(district_stage, competition):
    enable_regions(competition)
    mazowieckie = region(competition, Voivodeship.MAZOWIECKIE)
    conflicted = ActiveReviewerFactory(district=Voivodeship.POMORSKIE, region=mazowieckie)
    submission = locked_submission(district_stage)
    submission.entry.participant.region = mazowieckie
    submission.entry.participant.save(update_fields=["region"])

    with pytest.raises(DomainError) as exc:
        assign_reviewer_to_submission(submission, conflicted)

    assert exc.value.machine_code == "REVIEWER_CONFLICT_OF_INTEREST"


def test_trzeci_recenzent_takze_podlega_regule(district_stage, competition):
    enable_regions(competition)
    mazowieckie = region(competition, Voivodeship.MAZOWIECKIE)
    conflicted = ActiveReviewerFactory(district=Voivodeship.POMORSKIE, region=mazowieckie)
    submission = locked_submission(district_stage)
    submission.entry.participant.region = mazowieckie
    submission.entry.participant.save(update_fields=["region"])
    submission.status = SubmissionStatus.MODERATION
    submission.save(update_fields=["status"])

    with pytest.raises(DomainError) as exc:
        assign_third_reviewer(submission, conflicted)

    assert exc.value.machine_code == "REVIEWER_CONFLICT_OF_INTEREST"
    assert not Review.objects.filter(submission=submission, status=ReviewStatus.ASSIGNED).exists()
