"""Poprawa własnej oceny przez recenzenta i odebranie pracy przez koordynatora.

Dwie prośby organizatora z jednego zdania: „członek komitetu powinien móc zmienić swoją ocenę,
chyba że koordynator odebrał mu zadanie”. Testy pilnują tego, co w nich najłatwiej zepsuć –
przeliczenia rundy 1 po zmianie (ocena uzgodniona znika i powstaje na nowo albo praca idzie do
moderacji) oraz kompletu odmów, bo to one decydują, czego tym trybem **nie** wolno ruszyć.
"""

import pytest

from apps.accounts.tests.factories import ActiveReviewerFactory, CoordinatorFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.grading.models import ROUND_TIEBREAK, FinalGrade, GradeMethod, Review, ReviewStatus
from apps.grading.services import (
    assign_reviewer_to_submission,
    assign_third_reviewer,
    resolve_moderation,
    revise_review,
    revision_block_reason,
    submit_review,
    unassign_reviewer,
    withdrawal_block_reason,
)
from apps.results.models import ResultsPublication
from apps.submissions.models import SubmissionStatus

from .conftest import locked_submission

pytestmark = pytest.mark.django_db


def reviewed_pair(stage, *, first: int = 5, second: int = 5):
    """Praca z dwiema wystawionymi ocenami rundy 1 – punkt wyjścia wszystkich tych scenariuszy."""
    submission = locked_submission(stage)
    one = assign_reviewer_to_submission(submission, ActiveReviewerFactory())
    two = assign_reviewer_to_submission(submission, ActiveReviewerFactory())
    submit_review(one, first)
    submit_review(two, second)
    # Serwisy zwracają własne instancje, więc obiekty w ręku testu byłyby jeszcze „przydzielone”.
    for item in (submission, one, two):
        item.refresh_from_db()
    return submission, one, two


def grade_of(submission) -> FinalGrade | None:
    return FinalGrade.objects.filter(submission=submission).first()


# --- poprawa własnej oceny ----------------------------------------------------------------------


def test_revision_replaces_the_consensus_grade_with_moderation(stage):
    """Poprawka rozbija zgodność: ocena uzgodniona znika, praca idzie do moderacji."""
    submission, one, _ = reviewed_pair(stage)
    assert grade_of(submission).method == GradeMethod.CONSENSUS

    revise_review(one, 2, "po ponownej lekturze")

    submission.refresh_from_db()
    one.refresh_from_db()
    assert one.score == 2
    assert one.comment_internal == "po ponownej lekturze"
    assert one.status == ReviewStatus.SUBMITTED
    assert one.revised_at is not None
    assert one.submitted_at is not None and one.submitted_at < one.revised_at
    assert grade_of(submission) is None
    assert submission.status == SubmissionStatus.MODERATION
    assert AuditLog.objects.filter(action="review.revised").count() == 1
    withdrawn = AuditLog.objects.get(action="grade.withdrawn")
    assert withdrawn.diff["reason"] == "REVIEW_REVISED"


def test_consensus_grade_is_recreated_when_scores_agree_again(stage):
    """Powrót do zgodności odtwarza ocenę uzgodnioną – z nową wartością, nie ze starą."""
    submission, one, _ = reviewed_pair(stage, first=5, second=5)
    revise_review(one, 2)

    revise_review(one, 5)

    submission.refresh_from_db()
    grade = grade_of(submission)
    assert grade is not None
    assert (grade.score, grade.method) == (5, GradeMethod.CONSENSUS)
    assert submission.status == SubmissionStatus.GRADED_PROVISIONAL


def test_revision_that_ends_the_disagreement_cancels_the_pending_tiebreak(stage):
    """Rozjemca nie ma już czego rozstrzygać – jego przydział znika z powodem „poprawiona ocena”."""
    submission, one, _ = reviewed_pair(stage, first=5, second=2)
    assert submission.status == SubmissionStatus.MODERATION
    third = assign_third_reviewer(submission, ActiveReviewerFactory(), actor=CoordinatorFactory())

    revise_review(one, 2)

    submission.refresh_from_db()
    third.refresh_from_db()
    assert third.status == ReviewStatus.CANCELLED
    assert grade_of(submission).method == GradeMethod.CONSENSUS
    assert submission.status == SubmissionStatus.GRADED_PROVISIONAL
    cancelled = AuditLog.objects.filter(action="review.cancelled").latest("id")
    assert cancelled.diff["reason"] == "REVIEW_REVISED"


def test_tiebreak_revision_updates_the_third_review_grade_in_place(stage):
    """Ocena rundy 2 *jest* oceną końcową – poprawka przepisuje ją, a nie tworzy drugiej."""
    submission, _, _ = reviewed_pair(stage, first=5, second=2)
    third = assign_third_reviewer(submission, ActiveReviewerFactory(), actor=CoordinatorFactory())
    submit_review(third, 5, "rozstrzygam na 5")
    grade_id = grade_of(submission).pk

    revise_review(third, 6, "jednak 6")

    submission.refresh_from_db()
    grade = grade_of(submission)
    assert grade.pk == grade_id
    assert (grade.score, grade.method) == (6, GradeMethod.THIRD_REVIEW)
    assert grade.rationale == "jednak 6"
    assert submission.status == SubmissionStatus.GRADED_PROVISIONAL
    assert FinalGrade.objects.filter(submission=submission).count() == 1
    updated = AuditLog.objects.get(action="grade.updated")
    assert (updated.diff["from"], updated.diff["to"]) == (5, 6)


def test_revision_refuses_a_withdrawn_review(stage):
    """Praca odebrana przez koordynatora jest dla recenzenta zamknięta – to sedno prośby."""
    submission, one, _ = reviewed_pair(stage)
    unassign_reviewer(one, actor=CoordinatorFactory())

    with pytest.raises(DomainError) as exc:
        revise_review(one, 2)

    assert exc.value.machine_code == "REVIEW_CANCELLED"
    assert "odebrał" in str(exc.value.detail)


def test_revision_refuses_a_review_that_was_never_submitted(stage):
    """Szkic poprawia się zwykłym wystawieniem oceny, a nie tym trybem."""
    review = assign_reviewer_to_submission(locked_submission(stage), ActiveReviewerFactory())

    with pytest.raises(DomainError) as exc:
        revise_review(review, 2)

    assert exc.value.machine_code == "REVIEW_NOT_SUBMITTED"


def test_revision_refuses_after_results_are_published(stage):
    """Ogłoszona tabela jest dokumentem – cicha zmiana oceny rozjeżdżałaby akta z tym, co ogłoszono."""
    submission, one, _ = reviewed_pair(stage)
    ResultsPublication.objects.create(stage=stage)

    with pytest.raises(DomainError) as exc:
        revise_review(one, 2)

    assert exc.value.machine_code == "RESULTS_PUBLISHED"
    assert grade_of(submission) is not None


def test_revision_refuses_when_a_human_decided_the_grade(stage):
    """Rozstrzygnięcie koordynatora nie może się zmienić w tle przez poprawkę recenzji rundy 1."""
    submission, one, _ = reviewed_pair(stage, first=5, second=2)
    resolve_moderation(submission, CoordinatorFactory(), 6, rationale="posiedzenie komisji")

    with pytest.raises(DomainError) as exc:
        revise_review(one, 6)

    assert exc.value.machine_code == "GRADE_DECIDED"
    assert grade_of(submission).method == GradeMethod.MODERATION


def test_revision_refuses_a_final_submission(stage):
    """Praca po zamknięciu okna reklamacji ma etap za sobą."""
    submission, one, _ = reviewed_pair(stage)
    FinalGrade.objects.filter(submission=submission).delete()
    submission.status = SubmissionStatus.FINAL
    submission.save(update_fields=["status"])

    with pytest.raises(DomainError) as exc:
        revise_review(one, 2)

    assert exc.value.machine_code == "SUBMISSION_CLOSED"


def test_revision_refuses_a_score_outside_the_scale(stage):
    """Skala etapu obowiązuje tak samo, jak przy pierwszym wystawieniu oceny."""
    _, one, _ = reviewed_pair(stage)

    with pytest.raises(DomainError) as exc:
        revise_review(one, 4)

    assert exc.value.machine_code == "SCORE_NOT_IN_SCALE"


def test_revision_block_reason_matches_what_the_service_does(stage):
    """Widok pyta o powód tą samą funkcją, którą zastosuje zapis – inaczej ekran by kłamał."""
    _, one, _ = reviewed_pair(stage)
    assert revision_block_reason(one) is None

    unassign_reviewer(one, actor=CoordinatorFactory())
    one.refresh_from_db()
    assert revision_block_reason(one) == "REVIEW_CANCELLED"


# --- odebranie pracy przez koordynatora ---------------------------------------------------------


def test_withdrawal_of_a_submitted_review_removes_the_consensus_grade(stage):
    """Wypadła jedna z dwóch zgodnych ocen – podstawa konsensusu zniknęła razem z nią."""
    submission, one, two = reviewed_pair(stage)

    unassign_reviewer(one, actor=CoordinatorFactory())

    submission.refresh_from_db()
    one.refresh_from_db()
    two.refresh_from_db()
    assert one.status == ReviewStatus.CANCELLED
    assert one.score == 5, "punkty zostają na rekordzie jako historia, choć już się nie liczą"
    assert grade_of(submission) is None
    assert submission.status == SubmissionStatus.IN_REVIEW
    assert two.status == ReviewStatus.SUBMITTED
    withdrawn = AuditLog.objects.get(action="grade.withdrawn")
    assert withdrawn.diff["reason"] == "REVIEW_WITHDRAWN"
    assert AuditLog.objects.filter(action="review.withdrawn").count() == 1


def test_withdrawal_lets_the_coordinator_assign_a_replacement(stage):
    """Sens odebrania pracy: dać ją komuś innemu. Praca musi więc zostać przydzielalna."""
    submission, one, _ = reviewed_pair(stage)
    unassign_reviewer(one, actor=CoordinatorFactory())

    replacement = assign_reviewer_to_submission(submission, ActiveReviewerFactory())
    submit_review(replacement, 5)

    submission.refresh_from_db()
    assert grade_of(submission).score == 5
    assert submission.status == SubmissionStatus.GRADED_PROVISIONAL


def test_withdrawal_resettles_round_one_when_two_reviews_remain(stage):
    """Gdy po odebraniu zostają dwie oceny, runda rozstrzyga się od nowa – tu zgodnie."""
    submission = locked_submission(stage)
    # Trzej recenzenci muszą być przydzieleni **przed** pierwszą oceną: praca w moderacji nie
    # przyjmuje już nowych przydziałów rundy 1.
    one, two, three = [assign_reviewer_to_submission(submission, ActiveReviewerFactory()) for _ in range(3)]
    submit_review(one, 2)
    submit_review(two, 5)
    submit_review(three, 5)
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.MODERATION

    unassign_reviewer(one, actor=CoordinatorFactory())

    submission.refresh_from_db()
    assert grade_of(submission).method == GradeMethod.CONSENSUS
    assert submission.status == SubmissionStatus.GRADED_PROVISIONAL


def test_withdrawal_refuses_a_grade_decided_by_a_human(stage):
    """Ocenę rozstrzygniętą przez człowieka zmienia się korektą z uzasadnieniem, a nie odebraniem."""
    submission, one, _ = reviewed_pair(stage, first=5, second=2)
    resolve_moderation(submission, CoordinatorFactory(), 6, rationale="posiedzenie komisji")

    with pytest.raises(DomainError) as exc:
        unassign_reviewer(one, actor=CoordinatorFactory())

    assert exc.value.machine_code == "GRADE_DECIDED"


def test_withdrawal_refuses_a_submitted_tiebreak_review(stage):
    """Ocena rozjemcy *jest* oceną końcową – jej też nie odbiera się przyciskiem „Odbierz”."""
    submission, _, _ = reviewed_pair(stage, first=5, second=2)
    third = assign_third_reviewer(submission, ActiveReviewerFactory(), actor=CoordinatorFactory())
    submit_review(third, 6)

    with pytest.raises(DomainError) as exc:
        unassign_reviewer(third, actor=CoordinatorFactory())

    assert exc.value.machine_code == "GRADE_DECIDED"
    assert grade_of(submission).score == 6


def test_withdrawal_allows_taking_back_a_pending_tiebreak(stage):
    """Nierozstrzygnięty przydział rozjemczy odbiera się normalnie – nie ma jeszcze oceny."""
    submission, _, _ = reviewed_pair(stage, first=5, second=2)
    third = assign_third_reviewer(submission, ActiveReviewerFactory(), actor=CoordinatorFactory())

    unassign_reviewer(third, actor=CoordinatorFactory())

    submission.refresh_from_db()
    third.refresh_from_db()
    assert third.status == ReviewStatus.CANCELLED
    assert submission.status == SubmissionStatus.MODERATION


def test_withdrawal_refuses_after_results_are_published(stage):
    """Po ogłoszeniu wyników zmiana idzie przez ponowne przeliczenie, nie przez odbieranie prac."""
    _, one, _ = reviewed_pair(stage)
    ResultsPublication.objects.create(stage=stage)

    with pytest.raises(DomainError) as exc:
        unassign_reviewer(one, actor=CoordinatorFactory())

    assert exc.value.machine_code == "RESULTS_PUBLISHED"


def test_withdrawal_block_reason_is_empty_for_a_plain_assignment(stage):
    """Zwykły przydział odbiera się bez przeszkód – bramki dotyczą rozstrzygnięć, nie przydziałów."""
    review = assign_reviewer_to_submission(locked_submission(stage), ActiveReviewerFactory())
    assert withdrawal_block_reason(review) is None


# --- API ----------------------------------------------------------------------------------------


def test_api_reviewer_revises_own_review(client, stage):
    submission, one, _ = reviewed_pair(stage)
    client.force_authenticate(one.reviewer.user)

    response = client.post(
        f"/api/grading/reviews/{one.pk}/revise/",
        {"score": 2, "comment_internal": "poprawka"},
        format="json",
    )

    one.refresh_from_db()
    submission.refresh_from_db()
    assert response.status_code == 200
    assert response.data["score"] == 2
    assert response.data["revised_at"] is not None
    assert one.score == 2
    assert submission.status == SubmissionStatus.MODERATION


def test_api_revision_is_404_for_another_reviewer(client, stage):
    """Cudza recenzja nie istnieje – odpowiedź nie może potwierdzać, że taki przydział jest."""
    _, one, _ = reviewed_pair(stage)
    client.force_authenticate(ActiveReviewerFactory().user)

    response = client.post(f"/api/grading/reviews/{one.pk}/revise/", {"score": 2}, format="json")

    one.refresh_from_db()
    assert response.status_code == 404
    assert one.score == 5


def test_api_revision_of_a_withdrawn_review_is_409(client, stage):
    _, one, _ = reviewed_pair(stage)
    unassign_reviewer(one, actor=CoordinatorFactory())
    client.force_authenticate(one.reviewer.user)

    response = client.post(f"/api/grading/reviews/{one.pk}/revise/", {"score": 2}, format="json")

    assert response.status_code == 409
    assert response.data["code"] == "REVIEW_CANCELLED"


def test_api_coordinator_withdraws_a_submitted_review(client, stage):
    submission, one, _ = reviewed_pair(stage)
    client.force_authenticate(CoordinatorFactory())

    response = client.post(f"/api/grading/reviews/{one.pk}/unassign/")

    submission.refresh_from_db()
    assert response.status_code == 200
    assert response.data["status"] == ReviewStatus.CANCELLED
    assert grade_of(submission) is None
    assert submission.status == SubmissionStatus.IN_REVIEW


def test_api_reviewer_cannot_withdraw_a_review(client, stage):
    """Odebranie pracy jest decyzją koordynatora, także po zmianie semantyki tego adresu."""
    _, one, _ = reviewed_pair(stage)
    client.force_authenticate(one.reviewer.user)

    response = client.post(f"/api/grading/reviews/{one.pk}/unassign/")

    one.refresh_from_db()
    assert response.status_code == 403
    assert one.status == ReviewStatus.SUBMITTED


def test_tiebreak_round_constant_is_used_by_the_dispute_flow(stage):
    """Strażnik oczywistości: scenariusze wyżej opierają się na tym, że runda 2 to ROUND_TIEBREAK."""
    submission, _, _ = reviewed_pair(stage, first=5, second=2)
    third = assign_third_reviewer(submission, ActiveReviewerFactory(), actor=CoordinatorFactory())
    assert third.round == ROUND_TIEBREAK
    assert Review.objects.filter(submission=submission, round=ROUND_TIEBREAK).count() == 1
