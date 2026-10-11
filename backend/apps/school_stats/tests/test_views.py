"""Ekrany STAT-01: bramka flagi i ról, izolacja konkursów, treść stron, CSV, PDF, audyt, menu, rejestr."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.accounts.models import CompetitionRole
from apps.accounts.processing_register import activities_for
from apps.accounts.tests.factories import ParticipantFactory
from apps.competitions.models import StageEntryStatus
from apps.competitions.tests.factories import EditionFactory
from apps.core.models import AuditLog
from apps.schools.tests.factories import SchoolFactory
from apps.tenancy.tests.factories import grant_membership
from apps.web.coordinator_nav import groups

from .conftest import make_supervisor, publish, student

pytestmark = pytest.mark.django_db


def urls(school_id: int = 1) -> list[str]:
    return [
        reverse("web:supervisor-statistics"),
        reverse("web:supervisor-statistics-report"),
        reverse("web:coordinator-school-stats"),
        reverse("web:coordinator-school-stats-export"),
        reverse("web:coordinator-school-stats-report", args=[school_id]),
    ]


# --- bramki --------------------------------------------------------------------------------------


def test_flag_off_means_404_everywhere(competition, edition, supervisor_client, coordinator_client):
    for url in urls():
        client = supervisor_client if url.startswith("/supervisor/") else coordinator_client
        assert client.get(url).status_code == 404, url


def test_anonymous_is_sent_to_login(flag_on, client_for):
    client = client_for(flag_on)
    for url in urls():
        response = client.get(url)
        assert response.status_code == 302 and "/login/" in response["Location"], url


def test_participant_cannot_open_either_panel(flag_on, client_for):
    profile = ParticipantFactory(competition=flag_on)
    grant_membership(profile.user, flag_on, CompetitionRole.PARTICIPANT)
    client = client_for(flag_on)
    client.force_login(profile.user)
    for url in urls():
        assert client.get(url).status_code == 403, url


def test_edition_of_another_competition_is_404(
    flag_on, edition, other_competition, coordinator_client, supervisor_client
):
    foreign = EditionFactory(competition=other_competition)
    assert (
        coordinator_client.get(reverse("web:coordinator-school-stats"), {"edition": foreign.pk}).status_code
        == 404
    )
    assert (
        supervisor_client.get(reverse("web:supervisor-statistics"), {"edition": foreign.pk}).status_code
        == 404
    )


# --- opiekun ---------------------------------------------------------------------------------------


def test_supervisor_page_shows_own_students_and_points_only_after_publication(
    flag_on, edition, stage, school, supervisor_client
):
    mine = student(stage, school=school, mine=True, status=StageEntryStatus.QUALIFIED)
    other = student(stage, school=school)
    url = reverse("web:supervisor-statistics")

    before = supervisor_client.get(url).content.decode()
    assert mine.participant.public_code in before
    assert other.participant.public_code not in before
    assert "wyniki nieogłoszone" in before
    assert "17 pkt" not in before

    publish(stage, {mine: 17, other: 3})
    after = supervisor_client.get(url).content.decode()
    assert "17 pkt" in after
    assert "awans" in after
    assert "<svg" in after  # wykres postępu (jedna edycja – jeden punkt)


def test_supervisor_report_pdf_for_verified_school(
    flag_on, edition, stage, school, supervisor_client, supervisor
):
    for _ in range(5):
        student(stage, school=school, mine=True)

    response = supervisor_client.get(reverse("web:supervisor-statistics-report"))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    body = b"".join(response.streaming_content)
    assert body.startswith(b"%PDF")
    assert AuditLog.objects.filter(action="school_stats.report_downloaded").exists()


def test_supervisor_report_pdf_requires_verified_school(flag_on, edition, stage, school, client_for):
    unverified = make_supervisor(flag_on, school=school, verified=False, email="nowy@szkola.test")
    client = client_for(flag_on)
    client.force_login(unverified.user)

    assert client.get(reverse("web:supervisor-statistics-report")).status_code == 404
    assert client.get(reverse("web:supervisor-statistics")).status_code == 200


def test_dashboard_link_follows_the_flag(competition, edition, supervisor_client):
    statistics_url = reverse("web:supervisor-statistics")
    assert statistics_url not in supervisor_client.get(reverse("web:supervisor")).content.decode()

    competition.feature_flags = {**(competition.feature_flags or {}), "school_statistics": True}
    competition.save(update_fields=["feature_flags"])
    assert statistics_url in supervisor_client.get(reverse("web:supervisor")).content.decode()


# --- koordynator -----------------------------------------------------------------------------------


def test_coordinator_page_ranks_schools(flag_on, edition, stage, school, other_school, coordinator_client):
    big = [student(stage, school=school) for _ in range(5)]
    small = student(stage, school=other_school)
    publish(stage, {**dict.fromkeys(big, 4), small: 1})

    response = coordinator_client.get(reverse("web:coordinator-school-stats"), {"sort": "results"})

    body = response.content.decode()
    assert response.status_code == 200
    assert body.index(school.name) < body.index(other_school.name)
    assert small.participant.user.last_name not in body


def test_coordinator_csv_export_is_audited(flag_on, edition, stage, school, coordinator_client):
    student(stage, school=school)

    response = coordinator_client.get(reverse("web:coordinator-school-stats-export"))

    assert response.status_code == 200
    content = b"".join(response.streaming_content).decode("utf-8-sig")
    assert content.splitlines()[0].startswith("Szkoła;RSPO;")
    assert school.name in content
    log = AuditLog.objects.get(action="export.generated")
    assert log.diff["kind"] == "school_statistics" and log.diff["rows"] == 1


def test_coordinator_report_only_for_schools_of_this_competition(
    flag_on, edition, stage, school, coordinator_client
):
    student(stage, school=school)
    stranger = SchoolFactory()

    ok = coordinator_client.get(reverse("web:coordinator-school-stats-report", args=[school.pk]))
    missing = coordinator_client.get(reverse("web:coordinator-school-stats-report", args=[stranger.pk]))

    assert ok.status_code == 200 and b"".join(ok.streaming_content).startswith(b"%PDF")
    assert missing.status_code == 404


# --- menu i rejestr czynności ---------------------------------------------------------------------


def test_menu_item_and_register_row_follow_the_flag(competition):
    def labels():
        return {item.label for group in groups([], competition) for item in group.items}

    keys = {activity.key for activity in activities_for(competition)}
    assert "Statystyki szkół" not in labels()
    assert "statystyki-szkol" not in keys

    competition.feature_flags = {**(competition.feature_flags or {}), "school_statistics": True}

    assert "Statystyki szkół" in labels()
    assert "statystyki-szkol" in {activity.key for activity in activities_for(competition)}
