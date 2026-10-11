"""Ekrany sieci absolwentów: bramki (anonim, rola, flaga, inny konkurs), treść HTML, akcje POST."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import ActiveReviewerFactory, UserFactory
from apps.alumni.models import AlumniProfile, MentorshipStatus
from apps.alumni.tests.helpers import (
    alumnus,
    chat,
    coordinator_of,
    enable,
    joined,
    mentee,
    mentor,
    participant_of,
)
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

PARTICIPANT_URLS = ("web:alumni", "web:alumni-directory")
COORDINATOR_URLS = (
    "web:coordinator-alumni",
    "web:coordinator-alumni-mentoring",
    "web:coordinator-alumni-invitations",
    "web:coordinator-alumni-stats",
)


def logged(client_for, competition, user):
    client = client_for(competition)
    client.force_login(user)
    return client


# --- bramki -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", PARTICIPANT_URLS + COORDINATOR_URLS)
def test_anonymous_is_redirected_to_login(client_for, competition, name):
    enable(competition)
    response = client_for(competition).get(reverse(name))
    assert response.status_code == 302
    assert "/login/" in response["Location"]


@pytest.mark.parametrize("name", PARTICIPANT_URLS + COORDINATOR_URLS)
def test_reviewer_gets_403(client_for, competition, name):
    enable(competition)
    reviewer = ActiveReviewerFactory(competition=competition)
    grant_membership(reviewer.user, competition, CompetitionRole.REVIEWER)
    assert logged(client_for, competition, reviewer.user).get(reverse(name)).status_code == 403


@pytest.mark.parametrize("name", COORDINATOR_URLS)
def test_participant_cannot_open_coordinator_screens(client_for, competition, name):
    enable(competition)
    person = participant_of(competition)
    assert logged(client_for, competition, person.user).get(reverse(name)).status_code == 403


@pytest.mark.parametrize("name", PARTICIPANT_URLS)
def test_flag_off_is_404_for_participant(client_for, competition, name):
    person = participant_of(competition)
    assert logged(client_for, competition, person.user).get(reverse(name)).status_code == 404


@pytest.mark.parametrize("name", COORDINATOR_URLS)
def test_flag_off_is_404_for_coordinator(client_for, competition, name):
    assert logged(client_for, competition, coordinator_of(competition)).get(reverse(name)).status_code == 404


def test_wall_and_unsubscribe_are_404_without_flag(client_for, competition):
    client = client_for(competition)
    assert client.get(reverse("web:alumni-wall")).status_code == 404
    assert client.get(reverse("web:alumni-unsubscribe", args=["x"])).status_code == 404


def test_supervisor_without_participant_role_gets_403(client_for, competition):
    enable(competition)
    user = UserFactory(groups=["supervisor"])
    grant_membership(user, competition, CompetitionRole.SUPERVISOR)
    assert logged(client_for, competition, user).get(reverse("web:alumni")).status_code == 403


# --- uczestnik ----------------------------------------------------------------------------------------


def test_join_flow_and_nav_link(client_for, competition):
    enable(competition)
    person = alumnus(competition)
    client = logged(client_for, competition, person.user)

    page = client.get(reverse("web:alumni"))
    assert page.status_code == 200
    assert 'href="/me/alumni/"' in page.content.decode()  # link w pasku konta

    # Bez zaznaczonej zgody – nic nie powstaje.
    assert client.post(reverse("web:alumni-join"), {}).status_code == 400
    assert not AlumniProfile.objects.exists()

    response = client.post(reverse("web:alumni-join"), {"consent": "on"})
    assert response.status_code == 302
    assert AlumniProfile.objects.filter(participant=person).exists()


def test_nav_link_absent_without_flag(client_for, competition):
    person = participant_of(competition)
    page = logged(client_for, competition, person.user).get(reverse("web:me"))
    assert "/me/alumni/" not in page.content.decode()


def test_profile_form_rejects_foreign_link_host(client_for, competition):
    enable(competition)
    person = alumnus(competition)
    joined(person)
    client = logged(client_for, competition, person.user)

    response = client.post(
        reverse("web:alumni-profile"),
        {"mentor_capacity": 2, "linkedin_url": "https://evil.example/in/x", "listed": "on"},
    )

    assert response.status_code == 400
    assert AlumniProfile.objects.get(participant=person).linkedin_url == ""


def test_directory_html_has_no_sensitive_fields_and_escapes_bio(client_for, competition):
    enable(competition)
    shown = alumnus(competition, "Ala", "Ukryta", school="XIV LO im. Staszica")
    joined(
        shown,
        bio="<script>alert(1)</script>",
        linkedin_url="https://www.linkedin.com/in/ala",
        university="Uniwersytet Warszawski",
    )
    viewer = participant_of(competition, "Widz")
    html = logged(client_for, competition, viewer.user).get(reverse("web:alumni-directory")).content.decode()

    assert "Ala U." in html
    assert "Uniwersytet Warszawski" in html
    assert shown.user.email not in html
    assert shown.public_code not in html
    assert "Staszica" not in html
    assert "Ukryta" not in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert 'rel="nofollow noopener noreferrer ugc"' in html


def test_public_wall_shows_minimal_fields(client_for, competition):
    enable(competition, public_wall=True)
    person = alumnus(competition, "Ola", "Publiczna")
    joined(person, public=True, bio="Prywatne bio", city="Kraków", university="AGH")
    joined(alumnus(competition, "Nie", "Publiczny"), public=False)

    html = client_for(competition).get(reverse("web:alumni-wall")).content.decode()

    assert "Ola P." in html
    assert "AGH" in html
    assert "Prywatne bio" not in html
    assert "Kraków" not in html
    assert "Nie P." not in html


def test_request_mentor_and_accept_via_views(client_for, competition):
    enable(competition)
    chat(competition)
    teacher = mentor(competition)
    student = mentee(competition, minor=False)
    student_client = logged(client_for, competition, student.user)

    form = student_client.get(reverse("web:alumni-request", args=[teacher.token]))
    assert form.status_code == 200
    response = student_client.post(
        reverse("web:alumni-request", args=[teacher.token]), {"topic": "physics", "note": "Hej"}
    )
    assert response.status_code == 302

    row = teacher.participant.mentorships_as_mentor.get()
    teacher_client = logged(client_for, competition, teacher.participant.user)
    response = teacher_client.post(reverse("web:alumni-mentoring-action", args=[row.pk, "accept"]))
    row.refresh_from_db()
    assert row.status == MentorshipStatus.ACCEPTED
    assert response["Location"] == reverse("web:chat-thread", args=[row.conversation_id])

    # Rozmowa mentorska ma notkę polityki nad wątkiem.
    thread = teacher_client.get(reverse("web:chat-thread", args=[row.conversation_id])).content.decode()
    assert "Rozmowa mentorska" in thread


def test_unknown_action_is_404(client_for, competition):
    enable(competition)
    person = participant_of(competition)
    response = logged(client_for, competition, person.user).post(
        reverse("web:alumni-mentoring-action", args=[1, "hack"])
    )
    assert response.status_code == 404


def test_flag_view_for_foreign_mentorship_is_404(client_for, competition):
    enable(competition)
    chat(competition)
    teacher = mentor(competition)
    student = mentee(competition)
    from apps.alumni import mentoring

    row = mentoring.request_mentor(user=student.user, competition=competition, token=teacher.token)
    stranger = participant_of(competition, "Obcy")
    response = logged(client_for, competition, stranger.user).post(
        reverse("web:alumni-flag", args=[row.pk]), {"reason": "x"}
    )
    assert response.status_code == 404


# --- koordynator ------------------------------------------------------------------------------------


def test_coordinator_screens_render(client_for, competition):
    enable(competition)
    chat(competition)
    teacher = mentor(competition)
    student = mentee(competition)
    from apps.alumni import mentoring

    mentoring.request_mentor(user=student.user, competition=competition, token=teacher.token, note="Notatka")
    client = logged(client_for, competition, coordinator_of(competition))

    for name in COORDINATOR_URLS:
        assert client.get(reverse(name)).status_code == 200, name
    page = client.get(reverse("web:coordinator-alumni")).content.decode()
    assert teacher.participant.public_code in page  # koordynator widzi pełne dane – to jego rola
    assert "Absolwenci" in client.get(reverse("web:coordinator")).content.decode()


def test_coordinator_settings_post(client_for, competition):
    enable(competition, mentoring_enabled=False)
    client = logged(client_for, competition, coordinator_of(competition))

    response = client.post(
        reverse("web:coordinator-alumni"), {"eligibility": "LAUREATE", "mentoring_enabled": "on"}
    )

    assert response.status_code == 302
    from apps.alumni.services import settings_for

    row = settings_for(competition)
    assert row.eligibility == "LAUREATE"
    assert row.mentoring_enabled is True
    assert row.public_wall is False


def test_coordinator_of_other_competition_cannot_hide(client_for, competition, other_competition):
    enable(competition)
    enable(other_competition)
    profile = joined(alumnus(competition))
    client = logged(client_for, other_competition, coordinator_of(other_competition))

    response = client.post(reverse("web:coordinator-alumni-hide", args=[profile.pk]), {"hidden": "1"})

    assert response.status_code == 404
    profile.refresh_from_db()
    assert profile.hidden_at is None
