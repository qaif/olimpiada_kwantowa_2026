"""Delegacje krajowe (DEL-01): zaproszenie opiekuna, przyjęcie, zgłaszanie uczniów i granice.

Konkurs testowy to Konkurs #1 przestawiony na kraje (REG-01) i na tryb ``DELEGATIONS`` – tak samo,
jak operator przestawi ``iqo``. Testy pilnują trzech rzeczy: tego, że opiekun dochodzi wyłącznie do
uczniów swojego kraju, tego, że zgody i hasło składa uczeń, a nie opiekun, oraz tego, że Olimpiada
Kwantowa (tryb ``OPEN``) nie widzi z tej funkcji niczego.
"""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest
from django.contrib.auth.models import Group
from django.db import connection
from django.http import Http404
from django.utils import timezone

from apps.accounts import delegation_services as service
from apps.accounts.consents import ConsentKind
from apps.accounts.delegations import Delegation, DelegationInvitation, DelegationLeader, DelegationStatus
from apps.accounts.models import (
    GROUP_TEAM_LEADER,
    CompetitionRole,
    ConsentRecord,
    Membership,
    Participant,
    User,
    hash_invitation_code,
)
from apps.accounts.regions import switch_to_countries
from apps.accounts.services import has_role
from apps.accounts.tests.factories import CoordinatorFactory, UserFactory
from apps.competitions.tests.factories import CurrentEditionFactory, StageFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.tenancy.models import RegistrationMode

pytestmark = pytest.mark.django_db

PASSWORD = "Opiekun-Druzyny-2026!"
LEADER_CONSENTS = {ConsentKind.TERMS: True, ConsentKind.PRIVACY: True}
ADULT_BIRTH = timezone.localdate().replace(year=timezone.localdate().year - 17)


def make_delegations_competition(competition, *, edition=True):
    """Konkurs w trybie delegacji z krajami – to, co operator zrobi z ``iqo``."""
    switch_to_countries(competition)
    competition.registration_mode = RegistrationMode.DELEGATIONS
    competition.save(update_fields=["registration_mode"])
    if edition:
        CurrentEditionFactory(competition=competition)
    return competition


@pytest.fixture
def iqo(competition):
    return make_delegations_competition(competition)


@pytest.fixture
def coordinator():
    return CoordinatorFactory()


def invite(competition, coordinator, email="lead@example.test", country="de", captured=None):
    """Zaproszenie z przechwyceniem tokenu – w bazie jest tylko skrót, więc bierzemy go z listu."""
    sent = {}

    def capture(invitation, token, *, request=None):
        sent["token"] = token
        invitation.sent_at = timezone.now()
        invitation.save(update_fields=["sent_at"])

    original = service._send_leader_invitation
    service._send_leader_invitation = capture
    try:
        invitation = service.invite_leader(competition, email=email, country_code=country, actor=coordinator)
    finally:
        service._send_leader_invitation = original
    return invitation, sent["token"]


def leader_for_country(competition, coordinator, email, country="de"):
    invitation, token = invite(competition, coordinator, email=email, country=country)
    return service.accept_leader_invitation(
        invitation, first_name="Lena", last_name="Leiter", password=PASSWORD, given=LEADER_CONSENTS
    )


def add(leader, email="kid@example.test", **overrides):
    data = {
        "first_name": "Kurt",
        "last_name": "Kind",
        "email": email,
        "birth_date": ADULT_BIRTH,
        "school": "Gymnasium Berlin",
        "grade": 2,
    }
    data.update(overrides)
    return service.add_student(leader, **data)


# --- tryb rejestracji -------------------------------------------------------------------------------


def test_open_mode_is_the_default_and_keeps_registration_open(competition):
    from apps.competitions.registration import ensure_registration_open

    CurrentEditionFactory(competition=competition)

    assert competition.registration_mode == RegistrationMode.OPEN
    assert not competition.uses_delegations
    assert ensure_registration_open().is_open


def test_delegations_mode_closes_self_registration_with_its_own_reason(iqo):
    from apps.competitions.models import REGISTRATION_DELEGATIONS, current_registration_status
    from apps.competitions.registration import ensure_registration_open, registration_message

    state = current_registration_status(competition=iqo)
    assert not state.is_open
    assert state.reason == REGISTRATION_DELEGATIONS
    assert "opiekun" in registration_message(state)
    with pytest.raises(DomainError) as error:
        ensure_registration_open()
    assert error.value.machine_code == "REGISTRATION_CLOSED"


def test_delegations_mode_refuses_service_registration(iqo):
    from apps.accounts.services import register_participant

    with pytest.raises(DomainError) as error:
        register_participant(
            email="self@example.test",
            password=PASSWORD,
            first_name="Self",
            last_name="Registered",
            district="de",
            grade=2,
            birth_date=ADULT_BIRTH,
            gdpr_consent=True,
            terms_consent=True,
            phone="+48600100200",
            school="Gymnasium Berlin",
        )
    assert error.value.machine_code == "REGISTRATION_CLOSED"
    assert not User.objects.filter(email="self@example.test").exists()


def test_delegations_mode_refuses_class_list_import(iqo):
    from apps.accounts.bulk_registration import ensure_import_allowed, import_students

    with pytest.raises(DomainError) as error:
        ensure_import_allowed(iqo)
    assert error.value.machine_code == "IMPORT_DELEGATIONS"
    with pytest.raises(DomainError):
        import_students([], school_name="Gymnasium Berlin")


def test_delegations_services_do_not_exist_in_open_mode(competition, coordinator):
    switch_to_countries(competition)
    CurrentEditionFactory(competition=competition)

    with pytest.raises(Http404):
        service.invite_leader(competition, email="x@example.test", country_code="de", actor=coordinator)


# --- zaproszenie opiekuna ---------------------------------------------------------------------------


def test_invitation_creates_the_country_delegation_and_stores_only_a_hash(iqo, coordinator):
    invitation, token = invite(iqo, coordinator)

    delegation = invitation.delegation
    assert delegation.country.code == "de"
    assert delegation.max_students == iqo.delegation_max_students
    assert invitation.token_hash == hash_invitation_code(token)
    assert token not in invitation.token_hash
    assert invitation.expires_at > timezone.now() + timedelta(days=13)
    assert AuditLog.objects.filter(action="delegation.leader_invited").exists()


def test_second_leader_of_the_same_country_joins_the_same_delegation(iqo, coordinator):
    first, _ = invite(iqo, coordinator, email="a@example.test")
    second, _ = invite(iqo, coordinator, email="b@example.test")

    assert first.delegation_id == second.delegation_id
    assert Delegation.objects.count() == 1


def test_inviting_the_same_address_again_rotates_the_token(iqo, coordinator):
    first, old_token = invite(iqo, coordinator)
    second, new_token = invite(iqo, coordinator)

    assert first.pk == second.pk
    assert old_token != new_token
    with pytest.raises(DomainError):
        service.read_leader_invitation(old_token, iqo)
    assert service.read_leader_invitation(new_token, iqo).pk == first.pk


def test_invitation_mail_goes_in_the_competition_language(
    iqo, coordinator, mailoutbox, django_capture_on_commit_callbacks
):
    iqo.default_language = "en"
    iqo.interface_languages = ["en", "pl"]
    iqo.save(update_fields=["default_language", "interface_languages"])

    with django_capture_on_commit_callbacks(execute=True):
        invitation = service.invite_leader(
            iqo, email="lead@example.test", country_code="de", actor=coordinator
        )

    assert len(mailoutbox) == 1
    message = mailoutbox[0]
    assert message.to == ["lead@example.test"]
    assert "national team leader" in message.subject
    assert "/delegation/accept/" in message.body
    assert "Germany" in message.body
    assert invitation.sent_at is not None


def test_unknown_or_inactive_country_is_refused(iqo, coordinator):
    with pytest.raises(DomainError) as error:
        service.invite_leader(iqo, email="x@example.test", country_code="mazowieckie", actor=coordinator)
    assert error.value.machine_code == "COUNTRY_INVALID"


# --- przyjęcie zaproszenia ----------------------------------------------------------------------------


def test_accepting_creates_an_active_account_role_and_consent_evidence(iqo, coordinator):
    invitation, token = invite(iqo, coordinator)

    leader = service.accept_leader_invitation(
        service.read_leader_invitation(token, iqo),
        first_name="Lena",
        last_name="Leiter",
        password=PASSWORD,
        given=LEADER_CONSENTS,
    )

    user = leader.user
    assert user.is_active and user.email_verified_at is not None
    assert user.check_password(PASSWORD)
    assert has_role(user, iqo, CompetitionRole.TEAM_LEADER)
    assert Membership.objects.filter(user=user, competition=iqo, role=CompetitionRole.TEAM_LEADER).exists()
    kinds = set(ConsentRecord.objects.filter(team_leader=leader).values_list("kind", flat=True))
    assert kinds == {ConsentKind.TERMS, ConsentKind.PRIVACY}
    invitation.refresh_from_db()
    assert invitation.accepted_by == user


def test_invitation_link_works_only_once(iqo, coordinator):
    invitation, token = invite(iqo, coordinator)
    service.accept_leader_invitation(
        invitation, first_name="L", last_name="L", password=PASSWORD, given=LEADER_CONSENTS
    )

    with pytest.raises(DomainError) as error:
        service.read_leader_invitation(token, iqo)
    assert error.value.machine_code == "DELEGATION_INVITATION_INVALID"


def test_expired_and_revoked_invitations_do_not_open(iqo, coordinator):
    expired, expired_token = invite(iqo, coordinator, email="old@example.test")
    DelegationInvitation.objects.filter(pk=expired.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    revoked, revoked_token = invite(iqo, coordinator, email="gone@example.test")
    service.revoke_leader_invitation(revoked, actor=coordinator)

    for token in (expired_token, revoked_token, "zgadniety-token"):
        with pytest.raises(DomainError):
            service.read_leader_invitation(token, iqo)


def test_missing_consent_creates_nothing(iqo, coordinator):
    invitation, _ = invite(iqo, coordinator)

    with pytest.raises(DomainError) as error:
        service.accept_leader_invitation(
            invitation,
            first_name="L",
            last_name="L",
            password=PASSWORD,
            given={ConsentKind.TERMS: True, ConsentKind.PRIVACY: False},
        )
    assert error.value.machine_code == "CONSENT_REQUIRED"
    assert not User.objects.filter(email="lead@example.test").exists()


def test_logged_in_account_must_match_the_invited_address(iqo, coordinator):
    invitation, _ = invite(iqo, coordinator)
    stranger = UserFactory(email="stranger@example.test")

    with pytest.raises(DomainError) as error:
        service.accept_leader_invitation(invitation, user=stranger, given=LEADER_CONSENTS)
    assert error.value.machine_code == "EMAIL_MISMATCH"
    assert not DelegationLeader.objects.exists()


def test_existing_account_accepts_after_logging_in_and_keeps_its_password(iqo, coordinator):
    existing = UserFactory(email="lead@example.test", password="Stare-Haslo-2026!")
    invitation, _ = invite(iqo, coordinator)

    with pytest.raises(DomainError) as error:
        service.accept_leader_invitation(
            invitation, first_name="X", last_name="Y", password=PASSWORD, given=LEADER_CONSENTS
        )
    assert error.value.machine_code == "ACCOUNT_EXISTS"

    leader = service.accept_leader_invitation(invitation, user=existing, given=LEADER_CONSENTS)
    existing.refresh_from_db()
    assert leader.user == existing
    assert existing.check_password("Stare-Haslo-2026!")


def test_one_person_leads_only_one_delegation_per_edition(iqo, coordinator):
    leader_for_country(iqo, coordinator, "lead@example.test", country="de")

    with pytest.raises(DomainError) as error:
        service.invite_leader(iqo, email="lead@example.test", country_code="fr", actor=coordinator)
    assert error.value.machine_code == "LEADS_OTHER_DELEGATION"


def test_removing_a_leader_drops_the_role_but_keeps_the_students(iqo, coordinator):
    leader = leader_for_country(iqo, coordinator, "lead@example.test")
    student = add(leader)
    user = leader.user

    service.remove_leader(leader, actor=coordinator)

    assert not DelegationLeader.objects.filter(user=user).exists()
    user.refresh_from_db()
    assert not has_role(user, iqo, CompetitionRole.TEAM_LEADER)
    assert not user.groups.filter(name=GROUP_TEAM_LEADER).exists()
    assert Participant.objects.filter(pk=student.pk, delegation=leader.delegation_id).exists()
    assert service.leader_for(user, iqo) is None


# --- uczniowie -------------------------------------------------------------------------------------------


def test_student_gets_an_invited_account_in_the_delegation_country(
    iqo, coordinator, mailoutbox, django_capture_on_commit_callbacks
):
    leader = leader_for_country(iqo, coordinator, "lead@example.test")

    with django_capture_on_commit_callbacks(execute=True):
        student = add(leader)

    assert student.region.code == "de"
    assert student.district == "de"
    assert student.country == "DE"
    assert student.delegation_id == leader.delegation_id
    assert student.registered_by == leader.user
    assert student.invited_at is not None
    assert student.gdpr_consent_at is None
    assert not student.user.is_active
    assert not student.user.has_usable_password()
    assert has_role(student.user, iqo, CompetitionRole.PARTICIPANT) is False  # nieaktywne konto nie ma ról
    assert student.user.groups.filter(name="participant").exists()
    assert [message.to for message in mailoutbox] == [["kid@example.test"]]
    assert "/zaproszenie/" in mailoutbox[0].body


def test_student_limit_is_enforced(iqo, coordinator):
    leader = leader_for_country(iqo, coordinator, "lead@example.test")
    Delegation.objects.filter(pk=leader.delegation_id).update(max_students=1)
    add(leader, email="one@example.test")

    with pytest.raises(DomainError) as error:
        add(leader, email="two@example.test")
    assert error.value.machine_code == "DELEGATION_FULL"
    assert not User.objects.filter(email="two@example.test").exists()


def test_closed_delegation_and_closed_window_refuse_new_students(iqo, coordinator):
    leader = leader_for_country(iqo, coordinator, "lead@example.test")
    Delegation.objects.filter(pk=leader.delegation_id).update(status=DelegationStatus.CLOSED)
    leader.delegation.refresh_from_db()
    with pytest.raises(DomainError) as error:
        add(leader)
    assert error.value.machine_code == "DELEGATION_CLOSED"

    Delegation.objects.filter(pk=leader.delegation_id).update(status=DelegationStatus.ACTIVE)
    leader.delegation.edition.__class__.objects.filter(pk=leader.delegation.edition_id).update(
        registration_enabled=False
    )
    with pytest.raises(DomainError) as error:
        add(leader)
    assert error.value.machine_code == "REGISTRATION_CLOSED"


def test_taken_address_is_refused_instead_of_linking_someone_elses_account(iqo, coordinator):
    leader = leader_for_country(iqo, coordinator, "lead@example.test")
    UserFactory(email="taken@example.test")

    with pytest.raises(DomainError) as error:
        add(leader, email="taken@example.test")
    assert error.value.machine_code == "EMAIL_TAKEN"


def test_two_leaders_of_one_country_share_students(iqo, coordinator):
    first = leader_for_country(iqo, coordinator, "a@example.test")
    second = leader_for_country(iqo, coordinator, "b@example.test")
    student = add(first)

    assert list(service.students_of(second.delegation)) == [student]
    assert service.student_of(second, student.pk) == student


def test_leader_cannot_reach_a_student_of_another_country(iqo, coordinator):
    german = leader_for_country(iqo, coordinator, "de@example.test", country="de")
    french = leader_for_country(iqo, coordinator, "fr@example.test", country="fr")
    student = add(german)

    with pytest.raises(Http404):
        service.student_of(french, student.pk)
    with pytest.raises(Http404):
        service.remove_student(french, student)


def test_student_data_can_be_corrected_before_activation_only(
    iqo, coordinator, mailoutbox, django_capture_on_commit_callbacks
):
    leader = leader_for_country(iqo, coordinator, "lead@example.test")
    student = add(leader)

    with django_capture_on_commit_callbacks(execute=True):
        service.update_student(leader, student, email="new@example.test", school="Gymnasium Bonn", grade=3)
    student.refresh_from_db()
    assert student.user.email == "new@example.test"
    assert student.school == "Gymnasium Bonn"
    assert mailoutbox[-1].to == ["new@example.test"]

    from apps.accounts.activation import mark_activated

    mark_activated(student.user)
    student.refresh_from_db()
    with pytest.raises(DomainError) as error:
        service.update_student(leader, student, school="Somewhere Else")
    assert error.value.machine_code == "STUDENT_ACTIVE"


def test_student_can_be_removed_before_the_first_stage_starts(iqo, coordinator):
    leader = leader_for_country(iqo, coordinator, "lead@example.test")
    student = add(leader)
    user_pk = student.user_id

    assert service.remove_student(leader, student) == "deleted"
    assert not User.objects.filter(pk=user_pk).exists()


def test_student_cannot_be_removed_after_the_first_stage_started(iqo, coordinator):
    leader = leader_for_country(iqo, coordinator, "lead@example.test")
    student = add(leader)
    StageFactory(edition=leader.delegation.edition, competition=iqo)

    with pytest.raises(DomainError) as error:
        service.remove_student(leader, student)
    assert error.value.machine_code == "STAGE_STARTED"


def test_student_accepting_the_invitation_keeps_the_delegation_country(iqo, coordinator):
    from apps.accounts.bulk_registration import accept_invitation

    leader = leader_for_country(iqo, coordinator, "lead@example.test")
    student = add(leader)

    accepted = accept_invitation(
        student,
        password=PASSWORD,
        phone="+49301234567",
        district="fr",
        given={"terms_consent": True, "gdpr_consent": True, "guardian_consent": True},
    )

    assert accepted.region.code == "de"
    assert accepted.user.is_active
    assert accepted.gdpr_consent_at is not None


@pytest.mark.django_db(transaction=True)
def test_two_leaders_adding_at_once_cannot_exceed_the_limit(competition):
    """Dwa wątki, dwa połączenia z bazą, jedno wolne miejsce – zgłoszony zostaje dokładnie jeden uczeń."""
    make_delegations_competition(competition)
    coordinator = CoordinatorFactory()
    first = leader_for_country(competition, coordinator, "a@example.test")
    second = leader_for_country(competition, coordinator, "b@example.test")
    Delegation.objects.filter(pk=first.delegation_id).update(max_students=1)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def worker(leader, email):
        try:
            barrier.wait(timeout=10)
            add(leader, email=email)
            outcomes.append("ok")
        except Exception as exc:  # noqa: BLE001 - test ma pokazać przyczynę, nie tylko timeout
            outcomes.append(getattr(exc, "machine_code", repr(exc)))
        finally:
            connection.close()

    threads = [
        threading.Thread(target=worker, args=(first, "x@example.test")),
        threading.Thread(target=worker, args=(second, "y@example.test")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(outcomes) == ["DELEGATION_FULL", "ok"]
    assert Participant.objects.filter(delegation_id=first.delegation_id).count() == 1


# --- RODO -------------------------------------------------------------------------------------------------


def test_processing_register_lists_delegations_only_in_delegations_mode(competition):
    from apps.accounts.processing_register import activities_for

    assert "delegacje" not in {activity.key for activity in activities_for(competition)}
    make_delegations_competition(competition, edition=False)
    assert "delegacje" in {activity.key for activity in activities_for(competition)}


def test_account_export_has_the_leader_section_without_students(iqo, coordinator):
    from apps.accounts.data_export import export_payload

    leader = leader_for_country(iqo, coordinator, "lead@example.test")
    add(leader)

    payload = export_payload(leader.user)

    assert payload["delegacje_opiekun_druzyny"][0]["kraj"] == "Germany"
    assert {row["wlasciciel"] for row in payload["zgody"]} == {"opiekun_druzyny"}
    assert "kid@example.test" not in str(payload)


def test_deleting_a_leader_account_removes_role_and_invitations(iqo, coordinator):
    from apps.accounts.profile import delete_own_account

    leader = leader_for_country(iqo, coordinator, "lead@example.test")
    student = add(leader)
    user_pk = leader.user_id

    assert delete_own_account(leader.user) == "deleted"
    assert not User.objects.filter(pk=user_pk).exists()
    assert not DelegationInvitation.objects.filter(email="lead@example.test").exists()
    student.refresh_from_db()
    assert student.registered_by is None
    assert student.delegation_id is not None


def test_team_leader_group_exists_after_migrations():
    assert Group.objects.filter(name=GROUP_TEAM_LEADER).exists()
