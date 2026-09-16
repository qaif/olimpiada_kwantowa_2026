"""Zgłoszenia problemów z pracami: otwarcie, rozstrzygnięcie, kolejka i audyt."""

import pytest

from apps.accounts.tests.factories import ActiveReviewerFactory, CoordinatorFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.grading.issues import (
    issue_rows,
    open_issue,
    open_issue_count,
    open_issues_for_reviewer,
    resolve_issue,
)
from apps.grading.models import WorkIssueKind, WorkIssueStatus
from apps.grading.tests.conftest import locked_submission
from apps.grading.tests.factories import ReviewFactory

pytestmark = pytest.mark.django_db


def _review(stage, reviewer=None):
    return ReviewFactory(submission=locked_submission(stage), reviewer=reviewer or ActiveReviewerFactory())


def test_zgloszenie_zapisuje_prace_i_rodzaj(stage):
    review = _review(stage)

    issue = open_issue(review, WorkIssueKind.UNREADABLE, "Skan jest nieczytelny.")

    assert issue.submission_id == review.submission_id
    assert issue.status == WorkIssueStatus.OPEN
    assert AuditLog.objects.filter(action="issue.opened").exists()


def test_audyt_nie_przechowuje_tresci_zgloszenia(stage):
    """Dziennik zdarzeń nie jest drugą kopią zgłoszenia – opis bywa zdaniem o konkretnej pracy."""
    review = _review(stage)
    open_issue(review, WorkIssueKind.OTHER, "Tajna treść zgłoszenia.")

    entry = AuditLog.objects.get(action="issue.opened")
    assert "Tajna" not in str(entry.diff)
    assert entry.diff["length"] == len("Tajna treść zgłoszenia.")


def test_nieznany_rodzaj_to_odmowa(stage):
    review = _review(stage)

    with pytest.raises(DomainError) as error:
        open_issue(review, "COS_INNEGO", "opis")

    assert error.value.machine_code == "ISSUE_KIND_INVALID"


def test_pusty_opis_to_odmowa(stage):
    review = _review(stage)

    with pytest.raises(DomainError) as error:
        open_issue(review, WorkIssueKind.OTHER, "   ")

    assert error.value.machine_code == "ISSUE_TEXT_REQUIRED"


def test_drugie_otwarte_zgloszenie_do_tej_samej_recenzji_to_odmowa(stage):
    review = _review(stage)
    open_issue(review, WorkIssueKind.UNREADABLE, "pierwsze")

    with pytest.raises(DomainError) as error:
        open_issue(review, WorkIssueKind.OTHER, "drugie")

    assert error.value.machine_code == "ISSUE_ALREADY_OPEN"


def test_po_rozwiazaniu_wolno_zglosic_ponownie(stage):
    review = _review(stage)
    issue = open_issue(review, WorkIssueKind.UNREADABLE, "pierwsze")
    resolve_issue(issue, "poprosiliśmy o nowy skan")

    assert open_issue(review, WorkIssueKind.OTHER, "druga sprawa").status == WorkIssueStatus.OPEN


def test_rozwiazanie_wymaga_uzasadnienia(stage):
    issue = open_issue(_review(stage), WorkIssueKind.OTHER, "opis")

    with pytest.raises(DomainError) as error:
        resolve_issue(issue, "  ")

    assert error.value.machine_code == "ISSUE_RESOLUTION_REQUIRED"


def test_powtorne_rozwiazanie_to_odmowa(stage):
    coordinator = CoordinatorFactory()
    issue = open_issue(_review(stage), WorkIssueKind.OTHER, "opis")
    resolve_issue(issue, "załatwione", actor=coordinator)

    with pytest.raises(DomainError) as error:
        resolve_issue(issue, "jeszcze raz", actor=coordinator)

    assert error.value.machine_code == "ISSUE_ALREADY_RESOLVED"


def test_rozwiazanie_zapisuje_kto_i_kiedy(stage):
    coordinator = CoordinatorFactory()
    issue = open_issue(_review(stage), WorkIssueKind.OTHER, "opis")

    resolved = resolve_issue(issue, "praca odebrana", actor=coordinator, request=None)

    assert resolved.status == WorkIssueStatus.RESOLVED
    assert resolved.resolved_by == coordinator
    assert resolved.resolved_at is not None
    assert AuditLog.objects.filter(action="issue.resolved").exists()


def test_recenzent_widzi_tylko_swoje_otwarte_zgloszenia(stage):
    mine = ActiveReviewerFactory()
    my_review = _review(stage, reviewer=mine)
    open_issue(my_review, WorkIssueKind.UNREADABLE, "moje")
    open_issue(_review(stage), WorkIssueKind.OTHER, "cudze")

    assert list(open_issues_for_reviewer(mine)) == [my_review.pk]


def test_kolejka_stawia_otwarte_przed_rozwiazanymi(stage):
    resolved = open_issue(_review(stage), WorkIssueKind.OTHER, "stare")
    resolve_issue(resolved, "załatwione")
    still_open = open_issue(_review(stage), WorkIssueKind.UNREADABLE, "nowe")

    rows = list(issue_rows())

    assert rows[0].pk == still_open.pk
    assert rows[-1].pk == resolved.pk


def test_kolejka_filtruje_po_etapie(stage, district_stage):
    open_issue(_review(stage), WorkIssueKind.OTHER, "eliminacje")
    other = open_issue(_review(district_stage), WorkIssueKind.OTHER, "okręg")

    assert [item.pk for item in issue_rows(district_stage)] == [other.pk]


def test_licznik_obejmuje_tylko_otwarte_i_wskazane_etapy(stage, district_stage):
    open_issue(_review(stage), WorkIssueKind.OTHER, "otwarte")
    closed = open_issue(_review(stage), WorkIssueKind.OTHER, "do zamknięcia")
    resolve_issue(closed, "załatwione")
    open_issue(_review(district_stage), WorkIssueKind.OTHER, "inny etap")

    assert open_issue_count([stage.pk]) == 1
    assert open_issue_count() == 2
