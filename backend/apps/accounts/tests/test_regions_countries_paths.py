"""Kraje we **wszystkich** drogach zapisu (poprawki po przeglądzie REG-01).

Przegląd wykazał, że po przestawieniu konkursu na kraje formularze proponowały kraje, a serwisy
dalej sprawdzały listę województw – profil uczestnika, edycja komitetu, kody zaproszeń, rejestracja
komitetu i przyjęcie zaproszenia z importu kończyły się „Nieznane województwo”, a API rejestracji
przyjmowało „mazowieckie” i dopinało wycofany region. Każda droga ma tu własny test; wspólnym
mianownikiem jest ``apps.accounts.services.resolve_district``.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import (
    CommitteeMember,
    CommitteeStatus,
    InvitationCode,
    Participant,
    Region,
)
from apps.accounts.regions import switch_to_countries
from apps.core.api import DomainError

from .conftest import api_captcha_fields
from .factories import ParticipantFactory, UserFactory

pytestmark = pytest.mark.django_db

PASSWORD = "Poprawne-Haslo-2026"


@pytest.fixture
def countries(competition):
    switch_to_countries(competition)
    competition.refresh_from_db()
    return competition


def _region(competition, code):
    return Region.objects.for_competition(competition).get(code=code)


# --- helper -------------------------------------------------------------------------------------


def test_resolve_district_without_the_flag_is_the_voivodeship_rule(competition):
    from apps.accounts.services import resolve_district

    assert resolve_district(competition, "mazowieckie", required=True) == ("mazowieckie", None)
    with pytest.raises(DomainError):
        resolve_district(competition, "de", required=True)


def test_resolve_district_with_countries_accepts_only_active_regions(countries):
    from apps.accounts.services import resolve_district

    code, region = resolve_district(countries, "de", required=True)
    assert (code, region.code) == ("de", "de")
    for refused in ("mazowieckie", "poza-polska", "zz"):
        with pytest.raises(DomainError):
            resolve_district(countries, refused, required=True)
    with pytest.raises(DomainError):
        resolve_district(countries, "", required=True)
    assert resolve_district(countries, "", required=False) == (None, None)


# --- profil uczestnika --------------------------------------------------------------------------


def test_participant_profile_saves_a_country(countries):
    from apps.accounts.profile import update_participant_profile

    participant = ParticipantFactory(district="mazowieckie")

    update_participant_profile(participant, actor=participant.user, district="fr")

    participant.refresh_from_db()
    assert participant.district == "fr"
    assert participant.region == _region(countries, "fr")


def test_participant_profile_refuses_a_voivodeship_in_a_country_competition(countries):
    from apps.accounts.profile import update_participant_profile

    participant = ParticipantFactory(district="mazowieckie")

    with pytest.raises(DomainError):
        update_participant_profile(participant, actor=participant.user, district="slaskie")


def test_participant_profile_without_the_flag_is_unchanged(competition):
    from apps.accounts.profile import update_participant_profile

    participant = ParticipantFactory(district="mazowieckie", region=None)

    update_participant_profile(participant, actor=participant.user, district="slaskie")

    participant.refresh_from_db()
    assert participant.district == "slaskie"
    assert participant.region is None


def test_api_me_patch_accepts_a_country(countries):
    participant = ParticipantFactory(district="mazowieckie")
    api = APIClient()
    api.force_authenticate(participant.user)

    response = api.patch("/api/auth/me/", {"district": "jp"}, format="json")

    assert response.status_code == 200, response.content
    participant.refresh_from_db()
    assert participant.region == _region(countries, "jp")


def test_coordinator_edits_participant_and_committee_country(countries):
    from apps.accounts.profile import update_account_by_coordinator

    participant = ParticipantFactory(district="mazowieckie")
    update_account_by_coordinator(
        participant.user, actor=UserFactory(), account={}, participant={"district": "it"}
    )
    participant.refresh_from_db()
    assert participant.region == _region(countries, "it")

    member = CommitteeMember.objects.create(
        user=UserFactory(), competition=countries, status=CommitteeStatus.ACTIVE
    )
    update_account_by_coordinator(member.user, actor=UserFactory(), account={}, committee={"district": "es"})
    member.refresh_from_db()
    assert (member.district, member.region) == ("es", _region(countries, "es"))


# --- zaproszenia i komitet ----------------------------------------------------------------------


def test_invitation_code_carries_a_country(countries):
    from apps.accounts.services import create_invitation

    invitation, _code = create_invitation(UserFactory(), district="de")

    assert (invitation.district, invitation.region) == ("de", _region(countries, "de"))


def test_bulk_invitations_carry_a_country(countries):
    from apps.accounts.services import send_invitations

    send_invitations(UserFactory(), ["a@example.test"], district="ca")

    invitation = InvitationCode.objects.get(email="a@example.test")
    assert invitation.region == _region(countries, "ca")


def test_committee_registration_with_a_declared_country(countries):
    from apps.accounts.services import create_invitation, register_committee

    _invitation, code = create_invitation(UserFactory())

    member = register_committee(
        email="recenzent@example.test",
        password=PASSWORD,
        first_name="Piotr",
        last_name="Nowak",
        invitation_code=code,
        district="br",
    )

    assert (member.district, member.region, member.district_verified) == (
        "br",
        _region(countries, "br"),
        False,
    )


def test_committee_registration_takes_the_country_from_the_code(countries):
    from apps.accounts.services import create_invitation, register_committee

    _invitation, code = create_invitation(UserFactory(), district="in")

    member = register_committee(
        email="recenzent2@example.test",
        password=PASSWORD,
        first_name="Asha",
        last_name="Rao",
        invitation_code=code,
        district="br",
    )

    assert (member.district, member.region, member.district_verified) == (
        "in",
        _region(countries, "in"),
        True,
    )


def test_committee_registration_refuses_a_voivodeship_before_spending_the_code(countries):
    from apps.accounts.services import create_invitation, register_committee

    invitation, code = create_invitation(UserFactory())

    with pytest.raises(DomainError):
        register_committee(
            email="r3@example.test",
            password=PASSWORD,
            first_name="Jan",
            last_name="Kowal",
            invitation_code=code,
            district="mazowieckie",
        )
    invitation.refresh_from_db()
    assert invitation.used_count == 0


def test_import_invitation_accept_saves_a_country(countries):
    from apps.accounts.bulk_registration import accept_invitation

    # Konto od razu nieaktywne i bez potwierdzonego adresu – tak wygląda konto z importu przed
    # przyjęciem zaproszenia. Nie ``is_active=False`` + ``save()`` na koncie z fabryki: ``User.save()``
    # czyta przejście aktywne → nieaktywne jako blokadę i stawia ``blocked_at``, a zablokowanemu
    # kontu ``accept_invitation`` słusznie odmawia.
    participant = ParticipantFactory(
        district="", birth_year=1990, user__is_active=False, user__email_verified_at=None
    )

    accept_invitation(
        participant,
        password=PASSWORD,
        phone="600 100 200",
        district="kr",
        given={"terms_consent": True, "gdpr_consent": True},
    )

    participant.refresh_from_db()
    assert participant.region == _region(countries, "kr")


# --- API rejestracji ----------------------------------------------------------------------------


def _api_payload(**overrides):
    payload = {
        "email": "api-kraj@example.test",
        "password": PASSWORD,
        "first_name": "Anna",
        "last_name": "Schmidt",
        "school": "Gymnasium",
        "district": "de",
        "grade": 2,
        "birth_year": 1990,
        "phone": "600 100 200",
        "terms_consent": True,
        "gdpr_consent": True,
        **api_captcha_fields(),
    }
    payload.update(overrides)
    return payload


def test_api_registration_accepts_a_country(countries, open_registration):  # noqa: ARG001
    response = APIClient().post("/api/auth/register/participant/", _api_payload(), format="json")

    assert response.status_code == 201, response.content
    saved = Participant.objects.get(user__email="api-kraj@example.test")
    assert saved.region == _region(countries, "de")


def test_api_registration_refuses_a_deactivated_voivodeship(countries, open_registration):  # noqa: ARG001
    response = APIClient().post(
        "/api/auth/register/participant/", _api_payload(district="mazowieckie"), format="json"
    )

    assert response.status_code == 400
    assert not Participant.objects.filter(user__email="api-kraj@example.test").exists()


def test_api_district_choices_follow_the_competition(competition, as_competition):
    from apps.accounts.serializers import DistrictField

    with as_competition(competition):
        assert "mazowieckie" in DistrictField().choices
    switch_to_countries(competition)
    competition.refresh_from_db()
    with as_competition(competition):
        choices = DistrictField().choices
    assert "de" in choices and "mazowieckie" not in choices


# --- koszt nazwy kraju w listach (REG-01, poprawka po przeglądzie) ------------------------------


def _stage(competition):
    from apps.competitions.tests.factories import CurrentEditionFactory, StageFactory

    edition = CurrentEditionFactory(competition=competition)
    return edition, StageFactory(competition=competition, edition=edition)


def _enter(competition, stage, codes):
    from apps.competitions.tests.factories import StageEntryFactory

    for code in codes:
        participant = ParticipantFactory(
            competition=competition, district=code, region=_region(competition, code)
        )
        StageEntryFactory(competition=competition, stage=stage, participant=participant)


def test_participant_export_reads_country_names_without_a_query_per_row(countries):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from apps.core.exports import participant_dataset

    edition, stage = _stage(countries)
    _enter(countries, stage, ["de"])
    with CaptureQueriesContext(connection) as one:
        rows = list(participant_dataset(edition).rows)
    assert any("Germany" in row for row in rows)

    _enter(countries, stage, ["fr", "jp", "br"])
    with CaptureQueriesContext(connection) as four:
        list(participant_dataset(edition).rows)
    assert len(four) == len(one)


def test_stage_results_read_country_names_without_a_query_per_row(countries):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from apps.results.services import compute_stage_results

    _edition, stage = _stage(countries)
    _enter(countries, stage, ["de"])
    with CaptureQueriesContext(connection) as one:
        compute_stage_results(stage, preview=True)

    _enter(countries, stage, ["fr", "jp", "br", "it"])
    with CaptureQueriesContext(connection) as many:
        compute_stage_results(stage, preview=True)
    assert len(many) == len(one)
