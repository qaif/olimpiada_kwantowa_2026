"""Przydział ręczny: reguły „zadanie → recenzent z góry” i przydział pojedynczej pracy.

Dwa narzędzia, o które poprosił organizator, uzupełniają automat z ``test_assignment.py``.
Testy pilnują tego, co w nich najłatwiej zepsuć: pierwszeństwa reguł przed równoważeniem,
nienaruszalności konfliktu interesów oraz kompletu odmów przydziału ręcznego.
"""

import pytest

from apps.accounts.models import CommitteeStatus
from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CommitteeMemberFactory,
    CoordinatorFactory,
    PendingReviewerFactory,
)
from apps.competitions.tests.factories import ProblemFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.grading.models import ROUND_BLIND, ProblemReviewerRule, Review, ReviewStatus
from apps.grading.services import (
    add_problem_reviewer_rule,
    assign_reviewer_to_submission,
    assign_reviewers,
    remove_problem_reviewer_rule,
    unassign_reviewer,
)
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

from .conftest import locked_submission

pytestmark = pytest.mark.django_db


def reviewer_ids(submission) -> set[int]:
    """Recenzenci realnie przydzieleni do pracy (bez anulowanych)."""
    return set(
        Review.objects.filter(submission=submission)
        .exclude(status=ReviewStatus.CANCELLED)
        .values_list("reviewer_id", flat=True)
    )


# --- reguły zadań ------------------------------------------------------------------------------


def test_rule_assigns_existing_locked_submissions_immediately(stage):
    """Reguła dodana po zamknięciu etapu dopisuje recenzenta do prac już zablokowanych."""
    chosen = ActiveReviewerFactory()
    problem = ProblemFactory(stage=stage)
    first = locked_submission(stage, problem=problem)
    second = locked_submission(stage, problem=problem)
    other_problem = locked_submission(stage)

    result = add_problem_reviewer_rule(problem, chosen, actor=CoordinatorFactory())

    assert (result["assigned"], result["conflicts"], result["already"]) == (2, 0, 0)
    assert reviewer_ids(first) == {chosen.pk}
    assert reviewer_ids(second) == {chosen.pk}
    assert reviewer_ids(other_problem) == set()
    first.refresh_from_db()
    assert first.status == SubmissionStatus.IN_REVIEW
    assert AuditLog.objects.filter(action="review.rule_added").count() == 1


def test_rule_added_twice_is_refused(stage):
    """Para (zadanie, recenzent) jest unikalna – druga reguła to odmowa, nie cichy duplikat."""
    reviewer = ActiveReviewerFactory()
    problem = ProblemFactory(stage=stage)
    add_problem_reviewer_rule(problem, reviewer)

    with pytest.raises(DomainError) as exc:
        add_problem_reviewer_rule(problem, reviewer)

    assert exc.value.machine_code == "RULE_ALREADY_EXISTS"
    assert ProblemReviewerRule.objects.count() == 1


def test_rule_for_inactive_reviewer_is_refused(stage):
    """Reguła dla osoby, której nie wolno przydzielać, nie powstaje w ogóle."""
    problem = ProblemFactory(stage=stage)

    with pytest.raises(DomainError) as exc:
        add_problem_reviewer_rule(problem, PendingReviewerFactory())

    assert exc.value.machine_code == "REVIEWER_NOT_ELIGIBLE"
    assert not ProblemReviewerRule.objects.exists()


def test_auto_assignment_honours_rule_and_fills_the_rest(stage):
    """Recenzent z reguły wchodzi zawsze, automat dobiera tylko brakujące miejsce."""
    ruled = ActiveReviewerFactory()
    ActiveReviewerFactory()
    ActiveReviewerFactory()
    problem = ProblemFactory(stage=stage)
    submission = locked_submission(stage, problem=problem)
    add_problem_reviewer_rule(problem, ruled)
    # Reguła zdążyła już przydzielić pracę – automat ma dołożyć drugiego recenzenta, nie trzeciego.
    assert reviewer_ids(submission) == {ruled.pk}

    result = assign_reviewers(stage, 2)

    assert result["assignments"] == 1
    assigned = reviewer_ids(submission)
    assert ruled.pk in assigned
    assert len(assigned) == 2


def test_rule_beats_balancing_for_future_submissions(stage):
    """Praca, która wpłynęła po utworzeniu reguły, też trafia do wskazanego recenzenta."""
    ruled = ActiveReviewerFactory()
    ActiveReviewerFactory()
    ActiveReviewerFactory()
    problem = ProblemFactory(stage=stage)
    add_problem_reviewer_rule(problem, ruled)
    late = locked_submission(stage, problem=problem)

    assign_reviewers(stage, 2)

    assert ruled.pk in reviewer_ids(late)


def test_rules_exceeding_per_submission_are_all_assigned(stage):
    """Trzy reguły przy ``per_submission=2``: wygrywa decyzja organizatora, a nie liczba z formularza."""
    first, second, third = ActiveReviewerFactory(), ActiveReviewerFactory(), ActiveReviewerFactory()
    ActiveReviewerFactory()
    problem = ProblemFactory(stage=stage)
    # Reguły powstają **przed** pracą, więc rozstrzyga ścieżka automatyczna, a nie dopisanie
    # recenzji w chwili tworzenia reguły.
    for member in (first, second, third):
        add_problem_reviewer_rule(problem, member)
    submission = locked_submission(stage, problem=problem)

    assign_reviewers(stage, 2)

    assert reviewer_ids(submission) == {first.pk, second.pk, third.pk}


def test_rule_reviewer_in_conflict_is_skipped_with_reason(district_stage):
    """Konflikt okręgu wygrywa z regułą: praca trafia do ``skipped`` z ``RULE_REVIEWER_CONFLICT``."""
    conflicted = ActiveReviewerFactory(district="mazowieckie")
    ActiveReviewerFactory(district="malopolskie")
    ActiveReviewerFactory(district="pomorskie")
    problem = ProblemFactory(stage=district_stage)
    submission = locked_submission(district_stage, district="mazowieckie", problem=problem)
    # Reguła powstaje mimo konfliktu – dotyczy zadania, a konflikt rozstrzyga się per uczestnik.
    result = add_problem_reviewer_rule(problem, conflicted)
    assert (result["assigned"], result["conflicts"]) == (0, 1)

    outcome = assign_reviewers(district_stage, 2)

    assert outcome["skipped"] == [
        {
            "submission_id": submission.pk,
            "public_code": submission.entry.participant.public_code,
            "reason": "RULE_REVIEWER_CONFLICT",
            "reviewer_id": conflicted.pk,
        }
    ]
    assert conflicted.pk not in reviewer_ids(submission)
    assert len(reviewer_ids(submission)) == 2


def test_remove_rule_keeps_existing_reviews(stage):
    """Skasowanie reguły nie rusza recenzji, które już z niej powstały."""
    reviewer = ActiveReviewerFactory()
    problem = ProblemFactory(stage=stage)
    submission = locked_submission(stage, problem=problem)
    rule = add_problem_reviewer_rule(problem, reviewer)["rule"]

    remove_problem_reviewer_rule(rule, actor=CoordinatorFactory())

    assert not ProblemReviewerRule.objects.exists()
    assert reviewer_ids(submission) == {reviewer.pk}
    assert AuditLog.objects.filter(action="review.rule_removed").count() == 1


def test_suspended_rule_reviewer_is_not_assigned_by_automat(stage):
    """Reguła dla osoby zawieszonej po jej utworzeniu przestaje działać – pula jest bramką."""
    reviewer = ActiveReviewerFactory()
    ActiveReviewerFactory()
    ActiveReviewerFactory()
    problem = ProblemFactory(stage=stage)
    add_problem_reviewer_rule(problem, reviewer)
    submission = locked_submission(stage, problem=problem)
    reviewer.status = CommitteeStatus.SUSPENDED
    reviewer.save(update_fields=["status"])

    assign_reviewers(stage, 2)

    assert reviewer.pk not in reviewer_ids(submission)


# --- przydział pojedynczej pracy ---------------------------------------------------------------


def test_manual_assignment_creates_blind_review_and_starts_review(stage):
    """Ścieżka szczęśliwa: recenzja ASSIGNED w rundzie 1, praca LOCKED → IN_REVIEW, wpis audytowy."""
    reviewer = ActiveReviewerFactory()
    submission = locked_submission(stage)
    coordinator = CoordinatorFactory()

    review = assign_reviewer_to_submission(submission, reviewer, actor=coordinator)

    assert review.round == ROUND_BLIND
    assert review.status == ReviewStatus.ASSIGNED
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.IN_REVIEW
    entry = AuditLog.objects.get(action="review.assigned_manually")
    assert entry.diff == {
        "submission_id": submission.pk,
        "reviewer_id": reviewer.pk,
        "round": ROUND_BLIND,
    }


def test_manual_assignment_refuses_duplicate(stage):
    """Ten sam recenzent drugi raz → ALREADY_ASSIGNED, a nie druga recenzja."""
    reviewer = ActiveReviewerFactory()
    submission = locked_submission(stage)
    assign_reviewer_to_submission(submission, reviewer)

    with pytest.raises(DomainError) as exc:
        assign_reviewer_to_submission(submission, reviewer)

    assert exc.value.machine_code == "ALREADY_ASSIGNED"
    assert Review.objects.filter(submission=submission).count() == 1


def test_manual_assignment_refuses_conflict_of_interest(district_stage):
    """Ręczne wskazanie nie zwalnia z procedury – konflikt okręgu odmawia tak samo, jak automat."""
    conflicted = ActiveReviewerFactory(district="mazowieckie")
    submission = locked_submission(district_stage, district="mazowieckie")

    with pytest.raises(DomainError) as exc:
        assign_reviewer_to_submission(submission, conflicted)

    assert exc.value.machine_code == "REVIEWER_CONFLICT_OF_INTEREST"
    assert exc.value.status_code == 409
    assert not Review.objects.exists()


def test_manual_assignment_refuses_inactive_reviewer(stage):
    """Osoba spoza puli recenzentów nie dostanie pracy, nawet wskazana z ręki."""
    submission = locked_submission(stage)

    with pytest.raises(DomainError) as exc:
        assign_reviewer_to_submission(submission, CommitteeMemberFactory(status=CommitteeStatus.ACTIVE))

    assert exc.value.machine_code == "REVIEWER_NOT_ELIGIBLE"


def test_manual_assignment_refuses_unassignable_submission(stage):
    """Praca poza stanami LOCKED/IN_REVIEW nie przyjmuje przydziałów."""
    submission = locked_submission(stage)
    submission.status = SubmissionStatus.SUBMITTED
    submission.save(update_fields=["status"])

    with pytest.raises(DomainError) as exc:
        assign_reviewer_to_submission(submission, ActiveReviewerFactory())

    assert exc.value.machine_code == "SUBMISSION_NOT_ASSIGNABLE"


def test_manual_assignment_refuses_superseded_version(stage):
    """Starsza wersja pracy nie wchodzi do oceniania obok nowszej – ten sam predykat, co w automacie."""
    older = locked_submission(stage)
    SubmissionFactory(
        entry=older.entry, problem=older.problem, version=older.version + 1, status=SubmissionStatus.LOCKED
    )

    with pytest.raises(DomainError) as exc:
        assign_reviewer_to_submission(older, ActiveReviewerFactory())

    assert exc.value.machine_code == "SUBMISSION_NOT_ASSIGNABLE"


def test_unassign_cancels_untouched_assignment(stage):
    """Cofnięcie nierozpoczętego przydziału: status CANCELLED, rekord zostaje, audyt zapisany."""
    reviewer = ActiveReviewerFactory()
    submission = locked_submission(stage)
    review = assign_reviewer_to_submission(submission, reviewer)

    unassign_reviewer(review, actor=CoordinatorFactory())

    review.refresh_from_db()
    assert review.status == ReviewStatus.CANCELLED
    assert Review.objects.filter(pk=review.pk).exists()
    assert AuditLog.objects.filter(action="review.unassigned").count() == 1


def test_unassign_refuses_started_review(stage):
    """Szkic znaczy, że ktoś już czyta pracę – cofnąć się nie da."""
    review = assign_reviewer_to_submission(locked_submission(stage), ActiveReviewerFactory())
    review.status = ReviewStatus.DRAFT
    review.save(update_fields=["status"])

    with pytest.raises(DomainError) as exc:
        unassign_reviewer(review)

    assert exc.value.machine_code == "REVIEW_NOT_ASSIGNED"


def test_reassignment_after_unassign_reuses_the_cancelled_row(stage):
    """Ponowny przydział tej samej pary nie wywraca się na unikalności (submission, reviewer, round)."""
    reviewer = ActiveReviewerFactory()
    submission = locked_submission(stage)
    review = assign_reviewer_to_submission(submission, reviewer)
    unassign_reviewer(review)

    again = assign_reviewer_to_submission(submission, reviewer)

    assert again.pk == review.pk
    assert again.status == ReviewStatus.ASSIGNED
    assert Review.objects.filter(submission=submission, reviewer=reviewer).count() == 1
