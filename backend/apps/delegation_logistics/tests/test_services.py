"""Logistyka finału (LOG-01) – reguły serwisu: członkowie, szyfrowanie, terminy, zdrowie, pokoje,
listy wizowe, identyfikatory, przypomnienia i RODO (retencja, usunięcie konta, eksport)."""

from __future__ import annotations

import json
from datetime import date, time, timedelta
from io import BytesIO

import pytest
from django.db import connection
from django.utils import timezone

from apps.accounts.models import UserPreference
from apps.accounts.tests.test_delegations import leader_for_country
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.delegation_logistics import access, badges, letters, privacy, reports, rooming, services
from apps.delegation_logistics.models import (
    AccessRole,
    DelegationMember,
    Diet,
    FieldGroup,
    InvitationLetter,
    LogisticsAccess,
    MemberKind,
    RoomGender,
    ScanStatus,
    enabled,
)

from .conftest import enable, years_ago

pytestmark = pytest.mark.django_db

PASSPORT = {
    "passport_name": "ANNA ADULT",
    "nationality": "de",
    "date_of_birth": years_ago(19),
    "passport_number": "C01X00T47",
    "passport_expiry": timezone.localdate() + timedelta(days=900),
}


def member_of(leader, participant=None, kind=MemberKind.STUDENT):
    members = services.members_of(leader.delegation)
    if participant is not None:
        return next(m for m in members if m.participant_id == participant.pk)
    return next(m for m in members if m.kind == kind)


# --- bramka ----------------------------------------------------------------------------------------


def test_open_competition_and_missing_flag_have_no_logistics(competition):
    assert not enabled(competition)
    enable(competition)
    # Sama flaga bez trybu delegacji niczego nie otwiera – konkurs krajowy ma formularz przyjazdu etapu.
    assert not enabled(competition)


def test_delegations_mode_with_flag_enables_logistics(iqo):
    assert enabled(iqo)


# --- członkowie ------------------------------------------------------------------------------------


def test_members_follow_the_delegation_and_drop_removed_students(leader, students):
    members = services.members_of(leader.delegation)
    assert sorted(m.kind for m in members) == ["LEADER", "STUDENT", "STUDENT"]

    adult, _minor = students
    DelegationMember.objects.filter(participant=adult).update(passport_number="x")
    from apps.accounts.models import Participant

    Participant.objects.filter(pk=adult.pk).update(delegation=None)
    members = services.members_of(leader.delegation)
    assert all(m.participant_id != adult.pk for m in members)


def test_removed_leader_loses_logistics_row(iqo, leader, other_leader, coordinator):
    from apps.accounts import delegation_services

    second = leader_for_country(iqo, coordinator, "deputy-de@example.test", country="de")
    assert sum(m.kind == MemberKind.LEADER for m in services.members_of(leader.delegation)) == 2
    delegation_services.remove_leader(second, actor=coordinator)
    leaders = [m for m in services.members_of(leader.delegation) if m.kind == MemberKind.LEADER]
    assert [m.user_id for m in leaders] == [leader.user_id]


def test_leader_reaches_only_members_of_own_delegation(leader, other_leader, students):
    from django.http import Http404

    foreign = member_of(other_leader, kind=MemberKind.LEADER)
    with pytest.raises(Http404):
        services.member_for_leader(leader, foreign.pk)
    own = member_of(leader, students[0])
    assert services.member_for_leader(leader, own.pk) == own


def test_sensitive_fields_are_encrypted_at_rest(leader, students):
    member = member_of(leader, students[0])
    services.save_member(member, dict(PASSPORT), actor=leader.user)

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT passport_number, passport_name, date_of_birth FROM delegation_logistics_delegationmember "
            "WHERE id = %s",
            [member.pk],
        )
        number, name, born = cursor.fetchone()
    assert "C01X00T47" not in number and number.startswith("enc1:")
    assert "ANNA" not in name
    assert born.startswith("enc1:")
    reloaded = DelegationMember.objects.get(pk=member.pk)
    assert reloaded.passport_number == "C01X00T47"
    assert reloaded.nationality == "DE"


def test_audit_names_fields_but_never_values(leader, students):
    member = member_of(leader, students[0])
    services.save_member(member, dict(PASSPORT), actor=leader.user)

    entry = AuditLog.objects.filter(action="logistics.member_updated").latest("at")
    assert "passport_number" in entry.diff["fields"]
    assert "C01X00T47" not in json.dumps(entry.diff)


def test_locked_group_refuses_leader_but_not_officer(leader, students, event, officer):
    event.deadline_identity = timezone.now() - timedelta(minutes=1)
    event.save()
    member = member_of(leader, students[0])

    with pytest.raises(DomainError) as error:
        services.save_member(member, {"passport_number": "X1"}, actor=leader.user)
    assert error.value.machine_code == "LOGISTICS_GROUP_LOCKED"
    # Inna grupa przed terminem – zapis przechodzi.
    services.save_member(member, {"tshirt_size": "M"}, actor=leader.user)
    services.save_member(member, {"passport_number": "X1"}, actor=officer, as_officer=True)
    assert DelegationMember.objects.get(pk=member.pk).passport_number == "X1"


def test_passport_must_be_valid_during_the_final(leader, students, event):
    member = member_of(leader, students[0])
    data = {**PASSPORT, "passport_expiry": event.ends_on - timedelta(days=1)}
    with pytest.raises(DomainError):
        services.save_member(member, data, actor=leader.user)


def test_health_data_needs_d21_and_explicit_consent(iqo, leader, students):
    member = member_of(leader, students[1])
    # Bez decyzji D21 pola zdrowia nie przechodzą przez serwis w ogóle.
    services.save_member(member, {"allergies": "peanuts", "tshirt_size": "S"}, actor=leader.user)
    member.refresh_from_db()
    assert member.allergies == "" and member.tshirt_size == "S"

    enable(iqo, health=True)
    with pytest.raises(DomainError) as error:
        services.save_member(member, {"allergies": "peanuts"}, actor=leader.user)
    assert error.value.machine_code == "HEALTH_CONSENT_REQUIRED"
    # „Bez ograniczeń” nie jest daną o zdrowiu – zgody nie wymaga.
    services.save_member(member, {"diet": Diet.NONE}, actor=leader.user)

    services.save_member(
        member, {"allergies": "peanuts", "diet": Diet.HALAL}, actor=leader.user, health_consent=True
    )
    member.refresh_from_db()
    assert member.allergies == "peanuts" and member.health_consent_at is not None

    services.withdraw_health_consent(member, actor=leader.user)
    member.refresh_from_db()
    assert member.allergies == "" and member.diet == "" and member.health_consent_at is None


def test_guests_are_limited_and_removable(leader):
    member = services.add_guest(
        leader.delegation, first_name="Olga", last_name="Observer", role="OBSERVER", actor=leader.user
    )
    assert member.kind == MemberKind.GUEST and member.role_label == "Obserwator"
    services.remove_guest(member, actor=leader.user)
    assert not DelegationMember.objects.filter(pk=member.pk).exists()

    for index in range(services.MAX_GUESTS):
        services.add_guest(
            leader.delegation, first_name="G", last_name=str(index), role="GUEST", actor=leader.user
        )
    with pytest.raises(DomainError):
        services.add_guest(leader.delegation, first_name="G", last_name="X", role="GUEST", actor=leader.user)


def test_missing_groups_drive_completeness(iqo, leader, students):
    member = member_of(leader, students[0])
    groups = services.groups_for(iqo)
    assert FieldGroup.HEALTH not in groups
    assert services.missing_groups(member, groups) == groups
    services.save_member(member, {**PASSPORT, "needs_accommodation": False}, actor=leader.user)
    member.refresh_from_db()
    missing = services.missing_groups(member, groups)
    assert FieldGroup.IDENTITY not in missing and FieldGroup.ACCOMMODATION not in missing


# --- zdjęcie ------------------------------------------------------------------------------------------


def test_photo_is_validated_by_content_and_shown_only_after_clean_scan(leader, students, monkeypatch):
    from apps.delegation_logistics import tasks

    monkeypatch.setattr(tasks.scan_badge_photo, "delay", lambda *args, **kwargs: None)
    member = member_of(leader, students[0])
    with pytest.raises(DomainError):
        services.upload_photo(member, BytesIO(b"<html>"), actor=leader.user)

    photo = BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    member = services.upload_photo(member, photo, actor=leader.user)
    assert member.photo_scan == ScanStatus.PENDING and services.open_photo(member) is None

    services.apply_photo_scan(member.pk, member.photo_key, ScanStatus.CLEAN)
    member.refresh_from_db()
    assert services.photo_bytes(member).startswith(b"\x89PNG")

    services.apply_photo_scan(
        member.pk, member.photo_key, ScanStatus.INFECTED
    )  # już rozpatrzone – bez skutku
    member.refresh_from_db()
    assert member.photo_clean


def test_infected_photo_is_removed(leader, students, monkeypatch):
    from apps.delegation_logistics import tasks

    monkeypatch.setattr(tasks.scan_badge_photo, "delay", lambda *args, **kwargs: None)
    member = services.upload_photo(
        member_of(leader, students[0]), BytesIO(b"\xff\xd8\xff" + b"\x00" * 32), actor=leader.user
    )
    services.apply_photo_scan(member.pk, member.photo_key, ScanStatus.INFECTED)
    member.refresh_from_db()
    assert member.photo_key == "" and member.photo_scan == ScanStatus.INFECTED


# --- pokoje -------------------------------------------------------------------------------------------


def test_minor_never_shares_a_room_with_an_adult(iqo, leader, students, officer, event):
    adult, minor = students
    edition = event.edition
    room = rooming.create_room(iqo, edition, name="101", capacity=2, gender=RoomGender.MALE, actor=officer)
    adult_member, minor_member = member_of(leader, adult), member_of(leader, minor)
    services.save_member(adult_member, {"gender": "M"}, actor=officer, as_officer=True)
    services.save_member(minor_member, {"gender": "M"}, actor=officer, as_officer=True)
    adult_member.refresh_from_db()
    minor_member.refresh_from_db()

    rooming.assign(minor_member, room, actor=officer)
    with pytest.raises(DomainError) as error:
        rooming.assign(adult_member, room, actor=officer)
    assert "Niepełnoletni" in str(error.value.detail)


def test_room_gender_capacity_and_any_rooms(iqo, leader, students, officer, event):
    adult, minor = students
    edition = event.edition
    female = rooming.create_room(
        iqo, edition, name="201", capacity=1, gender=RoomGender.FEMALE, actor=officer
    )
    anyroom = rooming.create_room(iqo, edition, name="301", capacity=1, gender=RoomGender.ANY, actor=officer)
    minor_member = member_of(leader, minor)
    services.save_member(minor_member, {"gender": "M"}, actor=officer, as_officer=True)
    minor_member.refresh_from_db()
    with pytest.raises(DomainError):
        rooming.assign(minor_member, female, actor=officer)
    with pytest.raises(DomainError):
        rooming.assign(minor_member, anyroom, actor=officer)

    leader_member = member_of(leader, kind=MemberKind.LEADER)
    rooming.assign(leader_member, anyroom, actor=officer)
    adult_member = member_of(leader, adult)
    with pytest.raises(DomainError) as error:
        rooming.assign(adult_member, anyroom, actor=officer)
    assert "pełny" in str(error.value.detail)


def test_student_without_birth_date_is_treated_as_minor(leader, students, event):
    member = member_of(leader, students[0])
    member.participant.birth_date = None
    assert member.is_minor_on(event.starts_on)
    assert not member_of(leader, kind=MemberKind.LEADER).is_minor_on(event.starts_on)


# --- listy zapraszające --------------------------------------------------------------------------------


def test_letters_are_numbered_and_rendered_from_a_snapshot(iqo, leader, students, officer, event):
    member = member_of(leader, students[0])
    with pytest.raises(DomainError):
        letters.issue_letter(iqo, leader.delegation, actor=officer)  # nikt nie ma dokumentu
    services.save_member(member, dict(PASSPORT), actor=leader.user)

    first = letters.issue_letter(iqo, leader.delegation, actor=officer)
    second = letters.issue_letter(iqo, leader.delegation, member=member, actor=officer)
    year = timezone.localdate().year
    assert first.number == f"IQO/{year}/0001" and second.number == f"IQO/{year}/0002"
    assert first.people_count == 1

    services.save_member(member, {"passport_number": "NEW123"}, actor=leader.user)
    assert letters.people_of(InvitationLetter.objects.get(pk=first.pk))[0]["passport"] == "C01X00T47"
    pdf = letters.letter_pdf(InvitationLetter.objects.get(pk=first.pk))
    assert pdf.startswith(b"%PDF")


# --- identyfikatory i odhaczanie ------------------------------------------------------------------------


def test_badge_qr_carries_only_an_opaque_token(iqo, leader, students):
    member = member_of(leader, students[0])
    url = badges.checkin_url(member, competition=iqo)
    # Adres = ścieżka ekranu obsługi + losowy token. Ani nazwiska, ani identyfikatora wiersza.
    assert url.endswith(f"/coordinator/logistics/checkin/{member.badge_token}/")
    assert "Adult" not in url and "adult" not in url
    old = member.badge_token
    badges.reissue_badge(member, actor=None)
    assert badges.member_by_token(iqo, old) is None
    assert badges.member_by_token(iqo, member.badge_token) == member
    assert badges.badges_pdf([member], competition=iqo).startswith(b"%PDF")


def test_check_in_is_idempotent_and_keeps_the_first_time(iqo, leader, students, officer, event):
    checkpoint = badges.create_checkpoint(iqo, event.edition, name="Arrival", actor=officer)
    member = member_of(leader, students[0])
    first = badges.check_in(member, checkpoint, actor=officer)
    again = badges.check_in(member, checkpoint, actor=officer)
    assert first.pk == again.pk and first.at == again.at
    badges.undo_check_in(member, checkpoint, actor=officer)
    assert badges.status_of(member) == {}


# --- przypomnienia ------------------------------------------------------------------------------------


def test_reminders_go_to_leaders_in_their_language(
    iqo, leader, students, coordinator, mailoutbox, django_capture_on_commit_callbacks
):
    iqo.interface_languages = ["pl", "en"]
    iqo.save(update_fields=["interface_languages"])
    UserPreference.objects.create(user=leader.user, language="en")
    with django_capture_on_commit_callbacks(execute=True):
        sent = reports.send_reminders(iqo, leader.delegation.edition, actor=coordinator)

    assert sent == 1
    message = mailoutbox[-1]
    assert message.to == ["lead-de@example.test"]
    assert "Missing data for the final logistics" in message.subject
    assert "Travel document" in message.body
    assert "/delegation/logistics/" in message.body
    assert "C01X00T47" not in message.body


# --- dostęp --------------------------------------------------------------------------------------------


def test_officer_grants_follow_the_rules(iqo, coordinator):
    from apps.accounts.tests.factories import CoordinatorFactory, UserFactory

    second = CoordinatorFactory(email="second@example.test")
    volunteer = UserFactory(email="vol@example.test")
    # Pierwszego oficera nadaje dowolny koordynator.
    access.grant(iqo, email=coordinator.email, role=AccessRole.OFFICER, actor=coordinator)
    with pytest.raises(DomainError):
        access.grant(
            iqo, email=volunteer.email, role=AccessRole.OFFICER, actor=coordinator
        )  # nie koordynator
    with pytest.raises(DomainError):
        access.grant(iqo, email=coordinator.email, role=AccessRole.OFFICER, actor=second)  # nie oficer
    access.grant(iqo, email=volunteer.email, role=AccessRole.CHECKIN, actor=second)
    assert access.can_check_in(volunteer, iqo) and not access.is_officer(volunteer, iqo)
    assert access.is_officer(coordinator, iqo) and not access.is_officer(second, iqo)
    grant = LogisticsAccess.objects.get(user=coordinator)
    with pytest.raises(DomainError):
        access.revoke(grant, actor=second)


# --- RODO ------------------------------------------------------------------------------------------------


def test_retention_purges_members_photos_and_letter_snapshots(
    iqo, leader, students, officer, event, monkeypatch
):
    from apps.delegation_logistics import tasks

    monkeypatch.setattr(tasks.scan_badge_photo, "delay", lambda *args, **kwargs: None)
    member = member_of(leader, students[0])
    services.save_member(member, dict(PASSPORT), actor=leader.user)
    services.upload_photo(member, BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8), actor=leader.user)
    letter = letters.issue_letter(iqo, leader.delegation, actor=officer)

    assert privacy.purge_expired() == 0  # finał jeszcze się nie odbył
    event.starts_on = timezone.localdate() - timedelta(days=50)
    event.ends_on = timezone.localdate() - timedelta(days=40)
    event.save()
    assert privacy.purge_expired() == 3

    letter.refresh_from_db()
    event.refresh_from_db()
    assert not DelegationMember.objects.filter(delegation=leader.delegation).exists()
    assert letter.content == "" and letter.content_purged_at is not None and letter.number
    assert event.purged_at is not None
    with pytest.raises(DomainError):
        services.add_guest(leader.delegation, first_name="A", last_name="B", role="GUEST", actor=leader.user)


def test_account_erasure_removes_logistics_and_letter_rows(iqo, leader, students, officer, event):
    from apps.accounts.profile import _erase_account

    adult, _minor = students
    member = member_of(leader, adult)
    services.save_member(member, dict(PASSPORT), actor=leader.user)
    letter = letters.issue_letter(iqo, leader.delegation, actor=officer)

    exported = privacy.export_section(adult.user)
    assert exported[0]["numer_paszportu"] == "C01X00T47"

    _erase_account(adult.user, actor=officer)
    assert not DelegationMember.objects.filter(pk=member.pk).exists()
    letter.refresh_from_db()
    assert letters.people_of(letter) == []


def test_data_export_contains_the_logistics_section(leader, students):
    from apps.accounts.data_export import export_payload

    member = member_of(leader, kind=MemberKind.LEADER)
    services.save_member(member, {"tshirt_size": "XL", "arrival_time": time(14, 5)}, actor=leader.user)
    payload = export_payload(leader.user)
    assert payload["logistyka_finalu"][0]["rozmiar_koszulki"] == "XL"


def test_processing_register_lists_the_activity_only_when_enabled(competition, iqo):
    from apps.accounts.processing_register import activities_for

    keys = [activity.key for activity in activities_for(iqo)]
    assert "logistyka-finalu" in keys


def test_open_competition_register_has_no_activity(competition):
    from apps.accounts.processing_register import activities_for

    assert "logistyka-finalu" not in [activity.key for activity in activities_for(competition)]


def test_reports_summaries(leader, students, officer):
    member = member_of(leader, students[0])
    services.save_member(
        member,
        {
            "tshirt_size": "M",
            "arrival_date": date(2027, 7, 1),
            "arrival_time": time(14, 5),
            "arrival_place": "WAW",
            "arrival_mode": "FLIGHT",
            "arrival_number": "lh1234",
        },
        actor=leader.user,
    )
    members = services.members_of(leader.delegation)
    board = reports.travel_board(members)
    assert board["days"][0]["slots"][0]["number"] == "LH1234"
    assert len(board["undated"]) == 2
    shirts = reports.tshirt_summary(members)
    assert shirts["total"] == 1 and shirts["unknown_total"] == 2
    csv = reports.dataset("full", leader.delegation.competition, leader.delegation.edition, members)
    assert csv.count == 3
