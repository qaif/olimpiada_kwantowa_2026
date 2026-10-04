"""RODO sieci absolwentów: rejestr czynności, eksport danych, anonimizacja konta, retencja."""

from __future__ import annotations

import pytest

from apps.accounts.processing_register import ALUMNI_ACTIVITY, activities_for
from apps.alumni import mentoring, services
from apps.alumni.models import AlumniProfile, EndReason, MentorshipFlag, MentorshipStatus
from apps.alumni.tests.helpers import alumnus, chat, enable, joined, mentee, mentor

pytestmark = pytest.mark.django_db


def test_register_row_only_with_flag(competition):
    assert "absolwenci" not in {activity.key for activity in activities_for(competition)}
    enable(competition)
    competition.refresh_from_db()
    assert ALUMNI_ACTIVITY in activities_for(competition)
    assert "art. 6 ust. 1 lit. a" in ALUMNI_ACTIVITY.legal_basis


def test_export_contains_profile_consent_and_mentoring(competition):
    enable(competition)
    chat(competition)
    teacher = mentor(competition, bio="Moje bio")
    student = mentee(competition)
    row = mentoring.request_mentor(
        user=student.user, competition=competition, token=teacher.token, note="Pomocy"
    )
    mentoring.flag(user=student.user, competition=competition, pk=row.pk, reason="Powód")

    teacher_data = services.export_for(teacher.participant.user, teacher.participant)
    student_data = services.export_for(student.user, student)

    assert teacher_data["profil"]["bio"] == "Moje bio"
    assert teacher_data["profil"]["osiagniecia"]
    assert [event["zdarzenie"] for event in teacher_data["zgody"]] == ["GRANTED"]
    assert teacher_data["mentoring"][0]["rola"] == "mentor"
    assert teacher_data["mentoring"][0]["notatka_prosby"] == ""  # cudza notatka nie jest danymi mentora
    assert student_data["mentoring"][0]["notatka_prosby"] == "Pomocy"
    assert student_data["zgloszenia"][0]["powod"] == "Powód"
    assert services.export_for(student.user, None)["profil"] is None


def test_export_payload_has_alumni_section(competition, as_competition):
    from apps.accounts.data_export import export_payload

    enable(competition)
    person = alumnus(competition)
    joined(person)
    with as_competition(competition):
        payload = export_payload(person.user)

    assert payload["absolwenci"]["profil"] is not None


def test_anonymisation_erases_profile_and_ends_relations(competition):
    from apps.accounts.profile import anonymise_account

    enable(competition)
    chat(competition)
    teacher = mentor(competition)
    student = mentee(competition)
    row = mentoring.request_mentor(
        user=student.user, competition=competition, token=teacher.token, note="Notatka"
    )
    mentoring.flag(user=student.user, competition=competition, pk=row.pk, reason="Powód")

    anonymise_account(student.user)

    row.refresh_from_db()
    assert row.status == MentorshipStatus.ENDED
    assert row.end_reason == EndReason.ACCOUNT_REMOVED
    assert row.note == ""
    flag = MentorshipFlag.objects.get()
    assert flag.reason == ""
    assert flag.reporter_id is None

    anonymise_account(teacher.participant.user)
    assert not AlumniProfile.objects.exists()


def test_retention_is_held_while_consent_is_active(competition):
    from apps.accounts.retention import BLOCKED_ALUMNI, _blocked_reason

    enable(competition)
    person = alumnus(competition)
    expired = {person.stage_entries.get().stage.edition_id}

    assert _blocked_reason(person, expired_ids=expired) == ""
    joined(person)
    assert _blocked_reason(person, expired_ids=expired) == BLOCKED_ALUMNI

    # Wyłączona sieć – cel przetwarzania się skończył, retencja wraca.
    competition.feature_flags = {**competition.feature_flags, "alumni": False}
    competition.save(update_fields=["feature_flags"])
    person.refresh_from_db()
    assert _blocked_reason(person, expired_ids=expired) == ""
