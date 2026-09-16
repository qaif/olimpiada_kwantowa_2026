"""Porównanie ocen po odsłonięciu rundy 1 i wątek notatek recenzentów.

Dwie granice są tu przedmiotem testów: przed kompletem ocen recenzent nie widzi **niczego**
(ocena ślepa), a po odsłonięciu widzi punkty i komentarz publiczny drugiej strony, ale nigdy
jej tożsamości.
"""

import pytest

from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.core.api import DomainError
from apps.grading.comparison import (
    add_review_note,
    can_read_notes,
    comparison_context,
    notes_by_submission,
    reviewer_labels,
)
from apps.grading.models import ReviewStatus
from apps.grading.services import submit_review
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import SubmissionStatus

from .conftest import locked_submission

pytestmark = pytest.mark.django_db


@pytest.fixture
def submission(stage):
    created = locked_submission(stage)
    created.status = SubmissionStatus.IN_REVIEW
    created.save(update_fields=["status"])
    return created


@pytest.fixture
def reviews(submission):
    """Dwie recenzje rundy 1 – na razie nieoddane."""
    return [ReviewFactory(submission=submission), ReviewFactory(submission=submission)]


def submit_both(reviews, first_score=2, second_score=5):
    submit_review(reviews[0], first_score, comment_for_participant="Brakuje uzasadnienia kroku 3.")
    submit_review(reviews[1], second_score, comment_for_participant="Rachunki poprawne.")
    for review in reviews:
        review.refresh_from_db()
    return reviews


def test_comparison_is_hidden_until_every_review_is_submitted(reviews):
    submit_review(reviews[0], 2)
    reviews[0].refresh_from_db()

    assert comparison_context(reviews[0]) is None


def test_comparison_shows_other_scores_without_identities(reviews, submission):
    submit_both(reviews)

    context = comparison_context(reviews[0])

    assert context["own_score"] == 2
    assert [row["score"] for row in context["rows"]] == [5]
    assert context["rows"][0]["difference"] == 3
    assert context["rows"][0]["comment_for_participant"] == "Rachunki poprawne."
    label = context["rows"][0]["label"]
    assert label.startswith("Recenzent ")
    assert reviews[1].reviewer.user.email not in label


def test_agreed_scores_are_marked_as_agreed(reviews):
    submit_both(reviews, first_score=5, second_score=5)

    context = comparison_context(reviews[0])

    assert context["agreed"] is True
    assert context["rows"][0]["difference"] == 0


def test_labels_are_stable_between_comparison_and_notes(reviews, submission):
    submit_both(reviews)
    labels = reviewer_labels(submission)
    add_review_note(submission, reviews[1].reviewer, "Zostaję przy swojej ocenie.")

    context = comparison_context(reviews[0])

    assert context["rows"][0]["label"] == labels[reviews[1].reviewer_id]
    assert context["notes"][0]["label"] == labels[reviews[1].reviewer_id]
    assert context["notes"][0]["own"] is False


def test_note_is_refused_before_the_scores_are_revealed(reviews, submission):
    with pytest.raises(DomainError) as exc:
        add_review_note(submission, reviews[0].reviewer, "Za wcześnie.")

    assert exc.value.machine_code == "NOT_REVEALED"


def test_note_is_refused_to_someone_who_does_not_review_this_submission(reviews, submission):
    submit_both(reviews)
    stranger = ActiveReviewerFactory()

    with pytest.raises(DomainError) as exc:
        add_review_note(submission, stranger, "Wtrącam się.")

    assert exc.value.machine_code == "NOT_A_REVIEWER_OF_SUBMISSION"
    assert can_read_notes(submission, stranger) is False


def test_thread_closes_when_the_submission_is_final(reviews, submission):
    submit_both(reviews)
    submission.status = SubmissionStatus.FINAL
    submission.save(update_fields=["status"])

    with pytest.raises(DomainError) as exc:
        add_review_note(submission, reviews[0].reviewer, "Po fakcie.")

    assert exc.value.machine_code == "SUBMISSION_FINAL"


def test_empty_note_is_refused(reviews, submission):
    submit_both(reviews)

    with pytest.raises(DomainError) as exc:
        add_review_note(submission, reviews[0].reviewer, "   ")

    assert exc.value.machine_code == "EMPTY_NOTE"


def test_notes_by_submission_groups_the_thread_for_the_coordinator(reviews, submission):
    submit_both(reviews)
    add_review_note(submission, reviews[0].reviewer, "Proponuję 5.")
    add_review_note(submission, reviews[1].reviewer, "Zgoda.")

    grouped = notes_by_submission([submission])

    assert [note["text"] for note in grouped[submission.pk]] == ["Proponuję 5.", "Zgoda."]
    assert all(note["label"].startswith("Recenzent ") for note in grouped[submission.pk])


def test_withdrawn_review_does_not_block_the_comparison(reviews, submission):
    """Praca w moderacji ma oceny odsłonięte także wtedy, gdy jedną recenzję po drodze anulowano."""
    submit_both(reviews)
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.MODERATION
    reviews[1].status = ReviewStatus.CANCELLED
    reviews[1].save(update_fields=["status"])

    assert comparison_context(reviews[0]) is not None
