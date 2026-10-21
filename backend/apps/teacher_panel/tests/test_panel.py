"""Rama panelu nauczyciela (UX-PANEL-02): menu, flagi, Start, Szkoła, informacja dla ucznia."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.template.loader import render_to_string
from django.utils import timezone

from apps.accounts.models import SchoolParticipation
from apps.competitions.models import StageKind
from apps.competitions.tests.factories import StageFactory
from apps.submissions.tests.factories import SubmissionFactory

from .conftest import enter, flag, make_student

pytestmark = pytest.mark.django_db

START = "/supervisor/"


def page(client, path=START) -> str:
    response = client.get(path)
    assert response.status_code == 200, path
    return response.content.decode()


def nav_keys(html: str) -> list[str]:
    return re.findall(r'data-nav="([a-z_]+)"', html)


def current_keys(html: str) -> list[str]:
    return re.findall(r'aria-current="page" data-nav="([a-z_]+)"', html)


# --- menu -------------------------------------------------------------------------------------------


def test_menu_sections_and_labels(teacher_client, edition):
    html = page(teacher_client)

    assert 'aria-label="Nawigacja panelu nauczyciela"' in html
    assert "Menu nauczyciela" in html
    # Podpis menu koordynatora nie może się pojawić poza jego panelem (test_coordinator_shell).
    assert "Menu panelu" not in html
    assert nav_keys(html) == [
        "dashboard",
        "students",
        "import",
        "progress",
        "calendar",
        "school",
        "support",
        "profile",
        "password",
    ]


@pytest.mark.parametrize(
    ("path", "key"),
    [
        ("/supervisor/", "dashboard"),
        ("/supervisor/students/", "students"),
        ("/supervisor/import/", "import"),
        ("/supervisor/progress/", "progress"),
        ("/supervisor/school/", "school"),
    ],
)
def test_exactly_one_current_item(teacher_client, edition, path, key):
    assert current_keys(page(teacher_client, path)) == [key]


def test_flagged_items_follow_the_existing_switches(
    teacher_client, competition, edition, settings, monkeypatch
):
    flag(competition, "school_statistics")
    flag(competition, "workshop_materials")
    settings.TWO_FACTOR_ENABLED = True
    monkeypatch.setattr(
        "apps.workshop_materials.availability.has_visible_materials", lambda competition: True
    )
    monkeypatch.setattr("apps.promo.availability.has_public_materials", lambda competition: True)

    html = page(teacher_client)

    assert "statistics" in nav_keys(html)
    assert "materials" in nav_keys(html)
    assert "two_factor" in nav_keys(html)
    assert "posters" in nav_keys(html)
    assert current_keys(page(teacher_client, "/supervisor/statistics/")) == ["statistics"]


def test_participant_never_gets_the_teacher_menu(client_for, competition, edition):
    student = make_student(competition)
    client = client_for(competition)
    client.force_login(student.user)

    html = client.get("/me/").content.decode()

    assert "Menu nauczyciela" not in html
    assert client.get(START).status_code == 403


# --- Start ----------------------------------------------------------------------------------------


def test_start_tiles(teacher_client, competition, stage):
    worker = make_student(competition)
    SubmissionFactory(entry=enter(stage, worker), problem=stage.problems.first())
    enter(stage, make_student(competition))
    invited = make_student(competition, invited_at=timezone.now())
    invited.user.email_verified_at = None
    invited.user.save(update_fields=["email_verified_at"])

    response = teacher_client.get(START)
    summary = response.context["summary"]
    html = response.content.decode()

    assert (summary.active, summary.invited, summary.missing_work) == (2, 1, 1)
    assert summary.deadline["stage"] == stage and summary.deadline["kind"] == "deadline"
    assert "Bez pracy w otwartym etapie" in html and "?praca=missing" in html
    assert "niepotwierdzony" in html
    # Formalności – wyłącznie za flagą.
    assert "Bez zgody rodzica" not in html and "Bez zaświadczenia" not in html


def test_start_points_to_the_next_stage_when_none_is_open(teacher_client, competition, edition):
    later = StageFactory(
        edition=edition, kind=StageKind.DISTRICT, name="Etap II", opens_at=timezone.now() + timedelta(days=5)
    )

    summary = teacher_client.get(START).context["summary"]

    assert summary.deadline["stage"] == later and summary.deadline["kind"] == "opens"


def test_start_shows_formalities_with_the_flag(teacher_client, competition, stage):
    flag(competition, "supervisor_class_details")
    flag(competition, "student_status_certificate")
    enter(stage, make_student(competition, guardian_consent=False))

    response = teacher_client.get(START)
    html = response.content.decode()

    assert response.context["summary"].guardian_missing == 1
    assert response.context["summary"].certificate_missing == 1
    assert "Bez zgody rodzica" in html and "Bez zaświadczenia o statusie ucznia" in html


def test_empty_class_says_how_to_start(teacher_client, edition):
    assert "Nikt jeszcze nie wskazał Twojego adresu" in page(teacher_client)


# --- Szkoła -----------------------------------------------------------------------------------------


def test_school_page_toggles_participation_and_comes_back(teacher_client, teacher, edition):
    response = teacher_client.post("/supervisor/participation/")

    assert response.status_code == 302 and response["Location"].endswith("/supervisor/school/")
    assert SchoolParticipation.objects.filter(supervisor=teacher, edition=edition).exists()
    assert "Wycofaj potwierdzenie" in page(teacher_client, "/supervisor/school/")


# --- informacja dla ucznia i rejestr -----------------------------------------------------------------


def test_register_row_follows_the_flag(competition):
    from apps.accounts.processing_register import REGISTER_VERSION, activities_for

    assert tuple(int(part) for part in REGISTER_VERSION.split(".")) >= (1, 26)
    keys = {activity.key for activity in activities_for(competition)}
    assert "panel-nauczyciela-formalnosci" not in keys
    flag(competition, "supervisor_class_details")
    assert "panel-nauczyciela-formalnosci" in {activity.key for activity in activities_for(competition)}
    supervisors = next(item for item in activities_for(competition) if item.key == "opiekunowie")
    assert any("lista klasy" in category for category in supervisors.categories)


def test_profile_help_text_follows_the_flag(competition, settings):
    from apps.accounts.supervisors import reset_registration_cache
    from apps.cms.models import SiteSettings
    from apps.web.forms import SUPERVISOR_EMAIL_HELP_WITH_DETAILS, ParticipantProfileForm

    SiteSettings.objects.update_or_create(
        site=competition.site, defaults={"supervisor_registration_enabled": True}
    )
    reset_registration_cache()
    student = make_student(competition)

    plain = ParticipantProfileForm(participant=student).fields["supervisor_email"].help_text
    flag(competition, "supervisor_class_details")
    student.competition.refresh_from_db()
    detailed = ParticipantProfileForm(participant=student).fields["supervisor_email"].help_text

    assert "zaświadczenie" not in str(plain)
    assert detailed == SUPERVISOR_EMAIL_HELP_WITH_DETAILS


def test_consent_texts_mention_details_only_with_the_flag(client_for, competition):
    """List z prośbą o zgodę i strona zgody mówią o formalnościach wyłącznie przy fladze (D5)."""
    from django.urls import reverse

    from apps.accounts.supervisor_consent import (
        BODY_HTML_TEMPLATE,
        BODY_TEMPLATE,
        VIA_SUPERVISOR,
        make_token,
    )

    context = {"first_name": "Ola", "supervisor_email": "n@szkola.test", "link": "https://x.test/"}
    for template in (BODY_TEMPLATE, BODY_HTML_TEMPLATE):
        plain = render_to_string(template, {**context, "class_details": False})
        detailed = render_to_string(template, {**context, "class_details": True})
        assert "zaświadczenie o statusie ucznia" not in plain, template
        assert "zaświadczenie o statusie ucznia" in detailed, template

    student = make_student(competition, email="")
    client = client_for(competition)
    client.force_login(student.user)
    url = reverse("web:supervisor-consent", args=[make_token(student, "n@szkola.test", via=VIA_SUPERVISOR)])
    plain = client.get(url).content.decode()
    flag(competition, "supervisor_class_details")
    detailed = client.get(url).content.decode()

    assert "ani Twoich danych kontaktowych i zgód" in plain
    assert "zaświadczenie o statusie ucznia" not in plain
    assert "zaświadczenie o statusie ucznia" in detailed


def test_menu_and_progress_speak_english(teacher_client, stage, competition):
    """IQO jest anglojęzyczny: menu, nagłówki tabeli i stany przechodzą przez katalog aplikacji."""
    from django.utils import translation

    from ..nav import GROUPS
    from ..services import STEP_LABELS

    with translation.override("en"):
        labels = [str(item.label) for group in GROUPS for item in group.items]
        assert "Class progress" in labels and "Teacher menu" == translation.gettext("Menu nauczyciela")
        assert str(STEP_LABELS["none"]) == "nothing submitted"
