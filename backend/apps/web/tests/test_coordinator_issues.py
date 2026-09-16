"""Ekran koordynatora ``/coordinator/issues/``: kolejka, filtr etapu, rozstrzygnięcie i skróty."""

import pytest
from django.urls import reverse

from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.competitions.models import StageKind
from apps.competitions.tests.factories import (
    ProblemFactory,
    ScoringScaleFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.grading.issues import open_issue, resolve_issue
from apps.grading.models import ReviewStatus, WorkIssueKind, WorkIssueStatus
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

pytestmark = pytest.mark.django_db


def _review(stage, problem=None, reviewer=None, *, status=ReviewStatus.ASSIGNED, **kwargs):
    submission = SubmissionFactory(
        entry=StageEntryFactory(stage=stage),
        problem=problem or ProblemFactory(stage=stage),
        status=SubmissionStatus.IN_REVIEW,
    )
    return ReviewFactory(
        submission=submission,
        reviewer=reviewer or ActiveReviewerFactory(),
        status=status,
        **kwargs,
    )


def test_ekran_wymaga_roli_koordynatora(web_client, elim_stage):
    web_client.force_login(ActiveReviewerFactory().user)

    assert web_client.get(reverse("web:coordinator-issues")).status_code == 403


def test_kolejka_pokazuje_otwarte_zgloszenia(web_client, coordinator, elim_stage, problems):
    open_issue(_review(elim_stage, problems[0]), WorkIssueKind.UNREADABLE, "Skan jest nieczytelny.")
    web_client.force_login(coordinator)

    body = web_client.get(reverse("web:coordinator-issues")).content.decode()

    assert "Skan jest nieczytelny." in body
    assert "praca nieczytelna" in body


def test_filtr_etapu_zawęża_kolejkę(web_client, coordinator, edition, elim_stage, problems):
    other = StageFactory(edition=edition, kind=StageKind.DISTRICT)
    ScoringScaleFactory(stage=other)
    open_issue(_review(elim_stage, problems[0]), WorkIssueKind.OTHER, "z eliminacji")
    open_issue(_review(other), WorkIssueKind.OTHER, "z okręgu")
    web_client.force_login(coordinator)

    body = web_client.get(f"{reverse('web:coordinator-issues')}?stage={other.pk}").content.decode()

    assert "z okręgu" in body
    assert "z eliminacji" not in body


def test_koordynator_rozwiazuje_zgloszenie(web_client, coordinator, elim_stage, problems):
    issue = open_issue(_review(elim_stage, problems[0]), WorkIssueKind.OTHER, "opis")
    web_client.force_login(coordinator)

    response = web_client.post(
        reverse("web:coordinator-issue-resolve", kwargs={"pk": issue.pk}),
        {"resolution": "Poprosiliśmy o nowy skan."},
    )

    assert response.status_code == 302
    issue.refresh_from_db()
    assert issue.status == WorkIssueStatus.RESOLVED
    assert issue.resolved_by == coordinator


def test_rozwiazanie_bez_uzasadnienia_konczy_sie_komunikatem(web_client, coordinator, elim_stage, problems):
    issue = open_issue(_review(elim_stage, problems[0]), WorkIssueKind.OTHER, "opis")
    web_client.force_login(coordinator)

    web_client.post(reverse("web:coordinator-issue-resolve", kwargs={"pk": issue.pk}), {"resolution": ""})

    issue.refresh_from_db()
    assert issue.status == WorkIssueStatus.OPEN


def test_rozwiazanie_wraca_z_zachowanym_filtrem(web_client, coordinator, elim_stage, problems):
    issue = open_issue(_review(elim_stage, problems[0]), WorkIssueKind.OTHER, "opis")
    web_client.force_login(coordinator)

    response = web_client.post(
        reverse("web:coordinator-issue-resolve", kwargs={"pk": issue.pk}),
        {"resolution": "załatwione", "stage": elim_stage.pk},
    )

    assert response["Location"].endswith(f"?stage={elim_stage.pk}")


def test_skrot_odbiera_prace_recenzentowi_i_zostawia_zgloszenie_otwarte(
    web_client, coordinator, elim_stage, problems
):
    """Odebranie pracy jest czynnością, a nie rozstrzygnięciem sprawy – zamyka ją osobne kliknięcie."""
    review = _review(elim_stage, problems[0])
    issue = open_issue(review, WorkIssueKind.WRONG_PROBLEM, "to inne zadanie")
    web_client.force_login(coordinator)

    response = web_client.post(
        reverse("web:coordinator-issue-unassign", kwargs={"pk": issue.pk}), {"stage": elim_stage.pk}
    )

    assert response.status_code == 302
    review.refresh_from_db()
    issue.refresh_from_db()
    assert review.status == ReviewStatus.CANCELLED
    assert issue.status == WorkIssueStatus.OPEN


def test_kolejka_ma_odnosnik_do_przydzialow_etapu(web_client, coordinator, elim_stage, problems):
    open_issue(_review(elim_stage, problems[0]), WorkIssueKind.OTHER, "opis")
    web_client.force_login(coordinator)

    body = web_client.get(reverse("web:coordinator-issues")).content.decode()

    assert reverse("web:coordinator-stage-assignments", args=[elim_stage.pk]) in body


def test_pulpit_pokazuje_licznik_z_odnosnikiem(web_client, coordinator, elim_stage, problems):
    open_issue(_review(elim_stage, problems[0]), WorkIssueKind.OTHER, "opis")
    web_client.force_login(coordinator)

    body = web_client.get(reverse("web:coordinator")).content.decode()

    assert "Zgłoszone problemy" in body
    assert reverse("web:coordinator-issues") in body


def test_rozwiazane_zgloszenie_pokazuje_uzasadnienie(web_client, coordinator, elim_stage, problems):
    issue = open_issue(_review(elim_stage, problems[0]), WorkIssueKind.OTHER, "opis")
    resolve_issue(issue, "Praca przydzielona komu innemu.", actor=coordinator)
    web_client.force_login(coordinator)

    body = web_client.get(reverse("web:coordinator-issues")).content.decode()

    assert "Praca przydzielona komu innemu." in body
