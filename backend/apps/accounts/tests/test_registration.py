"""T-02, kryteria 1-4: rejestracja uczestnika i komitetu, obsługa kodów zaproszeń."""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import (
    GROUP_APPEALS,
    GROUP_PARTICIPANT,
    GROUP_REVIEWER,
    CommitteeMember,
    CommitteeStatus,
    InvitationCode,
    InvitationGrantsStatus,
    Participant,
    User,
    hash_invitation_code,
)

from .factories import InvitationCodeFactory

REGISTER_PARTICIPANT_URL = "/api/auth/register/participant/"
REGISTER_COMMITTEE_URL = "/api/auth/register/committee/"
PASSWORD = "Poprawne-Haslo-2026"


def participant_payload(**overrides):
    payload = {
        "email": "uczestnik@example.test",
        "password": PASSWORD,
        "first_name": "Anna",
        "last_name": "Nowak",
        "school": "LO nr 3",
        "district": "mazowiecki",
        "birth_year": 2008,
        "gdpr_consent": True,
        "guardian_consent": True,
    }
    payload.update(overrides)
    return payload


def committee_payload(**overrides):
    payload = {
        "email": "recenzent@example.test",
        "password": PASSWORD,
        "first_name": "Piotr",
        "last_name": "Wiśniewski",
        "invitation_code": "kod-testowy-0001",
        "district": "mazowiecki",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def api():
    return APIClient()


@pytest.mark.django_db
def test_kryterium_1_rejestracja_uczestnika_tworzy_usera_w_grupie_participant_z_kodem(api):
    """1. Rejestracja uczestnika tworzy User w grupie `participant` i Participant z unikalnym public_code."""
    resp = api.post(REGISTER_PARTICIPANT_URL, participant_payload(), format="json")

    assert resp.status_code == 201, resp.data
    body = resp.json()
    assert set(body) == {"id", "email", "public_code"}
    user = User.objects.get(email="uczestnik@example.test")
    assert body["id"] == user.id
    assert user.groups.filter(name=GROUP_PARTICIPANT).exists()
    participant = Participant.objects.get(user=user)
    assert participant.public_code == body["public_code"]
    assert participant.public_code.startswith("OLM-")
    assert participant.gdpr_consent_at is not None
    # hasło nigdy nie wraca w odpowiedzi i jest zapisane jako hash
    assert "password" not in body
    assert PASSWORD not in resp.content.decode()
    assert user.password != PASSWORD
    assert user.check_password(PASSWORD)


@pytest.mark.django_db
def test_kryterium_1_public_code_jest_unikalny_dla_wielu_uczestnikow(api):
    """1. public_code jest unikalny między uczestnikami."""
    for i in range(5):
        resp = api.post(
            REGISTER_PARTICIPANT_URL, participant_payload(email=f"u{i}@example.test"), format="json"
        )
        assert resp.status_code == 201, resp.data
    codes = set(Participant.objects.values_list("public_code", flat=True))
    assert len(codes) == 5


@pytest.mark.django_db
def test_kryterium_2_rejestracja_bez_zgody_rodo_zwraca_400_gdpr_consent_required(api):
    """2. Rejestracja uczestnika bez zgody RODO → 400 GDPR_CONSENT_REQUIRED."""
    resp = api.post(REGISTER_PARTICIPANT_URL, participant_payload(gdpr_consent=False), format="json")

    assert resp.status_code == 400
    assert resp.json()["code"] == "GDPR_CONSENT_REQUIRED"
    assert not User.objects.filter(email="uczestnik@example.test").exists()
    assert Participant.objects.count() == 0


@pytest.mark.django_db
def test_kryterium_3_kod_active_daje_aktywnego_recenzenta_w_grupie_reviewer(api):
    """3. Kod grants_status=ACTIVE → CommitteeMember ACTIVE w grupie `reviewer`."""
    InvitationCodeFactory(plain_code="kod-testowy-0001", grants_status=InvitationGrantsStatus.ACTIVE)

    resp = api.post(REGISTER_COMMITTEE_URL, committee_payload(), format="json")

    assert resp.status_code == 201, resp.data
    body = resp.json()
    assert set(body) == {"id", "email", "status"}
    assert body["status"] == CommitteeStatus.ACTIVE
    member = CommitteeMember.objects.get(user__email="recenzent@example.test")
    assert member.status == CommitteeStatus.ACTIVE
    assert member.user.groups.filter(name=GROUP_REVIEWER).exists()
    assert not member.user.groups.filter(name=GROUP_APPEALS).exists()
    assert PASSWORD not in resp.content.decode()


@pytest.mark.django_db
def test_kryterium_3_kod_pending_daje_status_pending_bez_grupy_reviewer(api):
    """3. Kod grants_status=PENDING → status PENDING, bez grupy `reviewer`."""
    InvitationCodeFactory(plain_code="kod-testowy-0001", grants_status=InvitationGrantsStatus.PENDING)

    resp = api.post(REGISTER_COMMITTEE_URL, committee_payload(), format="json")

    assert resp.status_code == 201, resp.data
    assert resp.json()["status"] == CommitteeStatus.PENDING
    member = CommitteeMember.objects.get(user__email="recenzent@example.test")
    assert member.status == CommitteeStatus.PENDING
    assert not member.user.groups.filter(name=GROUP_REVIEWER).exists()


@pytest.mark.django_db
def test_kryterium_3_kod_appeals_dodaje_grupe_appeals(api):
    """3. Kod z is_appeals=True i statusem ACTIVE nadaje też grupę `appeals`."""
    InvitationCodeFactory(plain_code="kod-testowy-0001", is_appeals=True)

    resp = api.post(REGISTER_COMMITTEE_URL, committee_payload(), format="json")

    assert resp.status_code == 201, resp.data
    member = CommitteeMember.objects.get(user__email="recenzent@example.test")
    assert member.is_appeals_committee is True
    assert member.user.groups.filter(name=GROUP_APPEALS).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("scenario", "sent_code"),
    [("expired", "kod-testowy-0001"), ("used_up", "kod-testowy-0001"), ("unknown", "kod-ktorego-nie-ma")],
)
def test_kryterium_4_zly_kod_zwraca_400_invalid_invitation_i_nie_zwieksza_used_count(
    api, scenario, sent_code
):
    """4. Kod wygasły/zużyty/nieistniejący → 400 INVALID_INVITATION, used_count nie rośnie."""
    kwargs = {}
    if scenario == "expired":
        kwargs = {"expires_at": timezone.now() - timedelta(minutes=1)}
    elif scenario == "used_up":
        kwargs = {"max_uses": 1, "used_count": 1}
    invitation = InvitationCodeFactory(plain_code="kod-testowy-0001", **kwargs)
    used_before = invitation.used_count

    resp = api.post(REGISTER_COMMITTEE_URL, committee_payload(invitation_code=sent_code), format="json")

    assert resp.status_code == 400, resp.data
    assert resp.json()["code"] == "INVALID_INVITATION"
    invitation.refresh_from_db()
    assert invitation.used_count == used_before
    assert not User.objects.filter(email="recenzent@example.test").exists()
    assert CommitteeMember.objects.count() == 0


@pytest.mark.django_db
def test_kod_zaproszenia_jest_trzymany_wylacznie_jako_sha256(api):
    """Bezpieczeństwo: w bazie nie ma kodu jawnego, tylko sha256; kod nie wraca w odpowiedzi."""
    plain = "kod-testowy-0001"
    InvitationCodeFactory(plain_code=plain)

    stored = InvitationCode.objects.get()
    assert stored.code_hash == hash_invitation_code(plain)
    assert plain not in stored.code_hash

    resp = api.post(REGISTER_COMMITTEE_URL, committee_payload(), format="json")
    assert plain not in resp.content.decode()


@pytest.mark.django_db
def test_kod_wielokrotnego_uzytku_zwieksza_used_count_i_wyczerpuje_sie(api):
    """Kod z max_uses=2 działa dwa razy, a trzecia próba jest odrzucona."""
    invitation = InvitationCodeFactory(plain_code="kod-testowy-0001", max_uses=2)

    for i in range(2):
        resp = api.post(
            REGISTER_COMMITTEE_URL, committee_payload(email=f"rec{i}@example.test"), format="json"
        )
        assert resp.status_code == 201, resp.data
    invitation.refresh_from_db()
    assert invitation.used_count == 2

    resp = api.post(REGISTER_COMMITTEE_URL, committee_payload(email="rec3@example.test"), format="json")
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_INVITATION"
    invitation.refresh_from_db()
    assert invitation.used_count == 2


@pytest.mark.django_db
def test_rejestracja_na_zajety_email_zwraca_400_email_taken(api):
    """Nie da się przejąć istniejącego konta przez ponowną rejestrację."""
    api.post(REGISTER_PARTICIPANT_URL, participant_payload(), format="json")

    resp = api.post(REGISTER_PARTICIPANT_URL, participant_payload(password="Inne-Haslo-2026"), format="json")

    assert resp.status_code == 400
    assert resp.json()["code"] == "EMAIL_TAKEN"
    assert User.objects.filter(email="uczestnik@example.test").count() == 1


@pytest.mark.django_db
def test_slabe_haslo_jest_odrzucane_i_konto_nie_powstaje(api):
    """Walidatory haseł Django obowiązują w rejestracji."""
    resp = api.post(REGISTER_PARTICIPANT_URL, participant_payload(password="abc"), format="json")

    assert resp.status_code == 400
    assert resp.json()["code"] == "WEAK_PASSWORD"
    assert not User.objects.filter(email="uczestnik@example.test").exists()
