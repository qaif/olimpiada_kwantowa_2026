"""Województwo członka komitetu: skąd się bierze i kto je zmienia.

Kod zaproszenia z województwem nadpisuje payload rejestracji, a koordynator ustala województwo
profilu zarejestrowanego na kod bez województwa – albo je usuwa, bo pole jest opcjonalne
(decyzja organizatora: „województwo nie ma znaczenia w przypadku członków komitetu”).
"""

from io import StringIO

import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.accounts.models import CommitteeMember, InvitationCode
from apps.accounts.services import create_invitation, register_committee
from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CommitteeMemberFactory,
    CoordinatorFactory,
    InvitationCodeFactory,
    ParticipantFactory,
)
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def test_invitation_district_overrides_registration_payload():
    """Kod z województwem wygrywa z deklaracją z formularza i nadaje district_verified=True."""
    InvitationCodeFactory(plain_code="kod-z-okregiem", district="pomorskie")

    member = register_committee(
        email="recenzent.kod@example.test",
        password="Poprawne-Haslo-2026",
        first_name="Anna",
        last_name="Recenzentka",
        invitation_code="kod-z-okregiem",
        district="mazowieckie",  # deklaracja z payloadu – ma zostać zignorowana
    )

    assert member.district == "pomorskie"
    assert member.district_verified is True


def test_invitation_without_district_leaves_profile_unverified():
    """Kod bez województwa → profil samodeklarowany, district_verified=False."""
    InvitationCodeFactory(plain_code="kod-bez-okregu", district=None)

    member = register_committee(
        email="recenzent.bez@example.test",
        password="Poprawne-Haslo-2026",
        first_name="Jan",
        last_name="Recenzent",
        invitation_code="kod-bez-okregu",
        district="mazowieckie",
    )

    assert member.district == "mazowieckie"
    assert member.district_verified is False


def test_create_invitation_service_and_command_carry_district():
    coordinator = CoordinatorFactory()

    invitation, _ = create_invitation(coordinator, district="slaskie")
    assert invitation.district == "slaskie"

    out = StringIO()
    call_command("create_invitation", email=coordinator.email, district="lubelskie", stdout=out)

    assert InvitationCode.objects.filter(district="lubelskie").exists()
    assert "district=lubelskie" in out.getvalue()


def test_coordinator_verifies_district_and_it_is_audited(client):
    member = ActiveReviewerFactory(district="mazowieckie", district_verified=False)
    coordinator = CoordinatorFactory()
    client.force_authenticate(coordinator)

    response = client.post(
        f"/api/auth/committee/{member.pk}/verify-district/", {"district": "podlaskie"}, format="json"
    )

    assert response.status_code == 200
    assert response.data["district"] == "podlaskie"
    assert response.data["district_verified"] is True
    member.refresh_from_db()
    assert (member.district, member.district_verified) == ("podlaskie", True)
    entry = AuditLog.objects.get(action="committee.district_verified", target_id=str(member.pk))
    assert entry.actor == coordinator
    assert entry.diff["district"] == {"from": "mazowieckie", "to": "podlaskie"}


def test_verify_district_is_coordinator_only(client):
    member = ActiveReviewerFactory(district_verified=False)
    url = f"/api/auth/committee/{member.pk}/verify-district/"

    client.force_authenticate(member.user)
    assert client.post(url, {"district": "podlaskie"}, format="json").status_code == 403

    client.force_authenticate(ParticipantFactory().user)
    assert client.post(url, {"district": "podlaskie"}, format="json").status_code == 403

    client.force_authenticate(None)
    assert client.post(url, {"district": "podlaskie"}, format="json").status_code == 401

    member.refresh_from_db()
    assert member.district_verified is False
    assert not CommitteeMember.objects.filter(district="podlaskie").exists()


def test_verify_district_with_an_empty_value_clears_the_district(client):
    """Pusta wartość usuwa województwo – bez tego pomyłki nie dałoby się cofnąć."""
    member = ActiveReviewerFactory(district="mazowieckie", district_verified=True)
    coordinator = CoordinatorFactory()
    client.force_authenticate(coordinator)

    response = client.post(
        f"/api/auth/committee/{member.pk}/verify-district/", {"district": ""}, format="json"
    )

    assert response.status_code == 200
    assert response.data["district"] is None
    assert response.data["district_verified"] is False
    member.refresh_from_db()
    assert (member.district, member.district_verified) == (None, False)
    entry = AuditLog.objects.get(action="committee.district_verified", target_id=str(member.pk))
    assert entry.diff["district"] == {"from": "mazowieckie", "to": None}
    assert entry.diff["district_verified"] == {"from": True, "to": False}


def test_verify_district_rejects_a_value_outside_the_list(client):
    """Wolny tekst nie jest „pustą wartością” – lista województw pozostaje zamknięta."""
    member = ActiveReviewerFactory(district="mazowieckie", district_verified=True)
    client.force_authenticate(CoordinatorFactory())

    response = client.post(
        f"/api/auth/committee/{member.pk}/verify-district/", {"district": "   "}, format="json"
    )

    assert response.status_code == 400
    member.refresh_from_db()
    assert (member.district, member.district_verified) == ("mazowieckie", True)


def test_verify_district_is_refused_for_a_member_who_is_not_active(client):
    """Województwo ustala się wyłącznie aktywnemu członkowi komitetu – także przy czyszczeniu."""
    member = CommitteeMemberFactory(district="mazowieckie", district_verified=True)
    client.force_authenticate(CoordinatorFactory())
    url = f"/api/auth/committee/{member.pk}/verify-district/"

    assert client.post(url, {"district": "podlaskie"}, format="json").status_code == 400
    response = client.post(url, {"district": ""}, format="json")

    assert response.status_code == 400
    assert response.data["code"] == "MEMBER_NOT_ACTIVE"
    member.refresh_from_db()
    assert (member.district, member.district_verified) == ("mazowieckie", True)
