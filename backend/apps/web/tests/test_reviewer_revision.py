"""Panel recenzenta: poprawa własnej oceny i praca odebrana przez koordynatora.

Ekran ma mówić dokładnie to, co przyjmie zapis: formularz poprawki tylko wtedy, gdy serwis ją
przepuści, a w przeciwnym razie krótkie zdanie z powodem. Testy sprawdzają obie strony tej umowy.
"""

import pytest

from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.grading.models import ReviewStatus
from apps.grading.tests.factories import ReviewFactory
from apps.results.models import ResultsPublication
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def submission(entry, problems):
    return SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.IN_REVIEW)


@pytest.fixture
def submitted_review(submission, reviewer):
    """Recenzja wystawiona – jedyny stan, w którym poprawka w ogóle wchodzi w grę."""
    return ReviewFactory(
        submission=submission,
        reviewer=reviewer,
        status=ReviewStatus.SUBMITTED,
        score=5,
        comment_internal="pierwsza wersja",
    )


def test_detail_offers_the_revision_form_for_a_submitted_review(web_client, submitted_review):
    web_client.force_login(submitted_review.reviewer.user)

    content = web_client.get(f"/review/{submitted_review.pk}/").content.decode()

    assert f'action="/review/{submitted_review.pk}/revise/"' in content
    assert "Popraw ocenę" in content
    # Formularz jest wypełniony bieżącą oceną – poprawka zaczyna się od tego, co recenzent wystawił.
    assert "pierwsza wersja" in content
    assert "checked" in content
    assert "Zapisz szkic" not in content


def test_revision_through_the_panel_changes_the_score(web_client, submitted_review):
    web_client.force_login(submitted_review.reviewer.user)

    response = web_client.post(
        f"/review/{submitted_review.pk}/revise/",
        {"score": 2, "comment_internal": "po ponownej lekturze", "comment_for_participant": ""},
        follow=True,
    )

    submitted_review.refresh_from_db()
    assert response.status_code == 200
    assert "Poprawiona ocena została zapisana." in response.content.decode()
    assert submitted_review.score == 2
    assert submitted_review.revised_at is not None


def test_detail_explains_why_the_revision_is_closed(web_client, submitted_review, elim_stage):
    """Wyniki ogłoszone: zamiast przycisku – zdanie o tym, dlaczego nic się już nie da zmienić."""
    ResultsPublication.objects.create(stage=elim_stage)
    web_client.force_login(submitted_review.reviewer.user)

    content = web_client.get(f"/review/{submitted_review.pk}/").content.decode()

    assert "Wyniki tego etapu są już ogłoszone" in content
    assert f'action="/review/{submitted_review.pk}/revise/"' not in content
    assert "Popraw ocenę" not in content


def test_withdrawn_review_shows_no_form_at_all(web_client, submitted_review):
    """Prośba organizatora wprost: odebrana praca to koniec – żadnego formularza, jasny komunikat."""
    submitted_review.status = ReviewStatus.CANCELLED
    submitted_review.save(update_fields=["status"])
    web_client.force_login(submitted_review.reviewer.user)

    content = web_client.get(f"/review/{submitted_review.pk}/").content.decode()

    assert "Koordynator odebrał Ci tę pracę." in content
    assert f'action="/review/{submitted_review.pk}/revise/"' not in content
    assert f'action="/review/{submitted_review.pk}/submit/"' not in content


def test_revision_is_404_for_another_reviewer(web_client, submitted_review):
    """Cudza recenzja nie istnieje – ten sam wynik, co przy podglądzie."""
    web_client.force_login(ActiveReviewerFactory().user)

    response = web_client.post(f"/review/{submitted_review.pk}/revise/", {"score": 2})

    submitted_review.refresh_from_db()
    assert response.status_code == 404
    assert submitted_review.score == 5


def test_list_keeps_withdrawn_reviews_in_a_separate_group(web_client, submission, reviewer, problems):
    """Odebrana praca nie znika z panelu (to wyglądałoby na awarię), ale stoi poza kolejką."""
    open_review = ReviewFactory(submission=submission, reviewer=reviewer, status=ReviewStatus.ASSIGNED)
    withdrawn = ReviewFactory(
        submission=SubmissionFactory(
            entry=submission.entry, problem=problems[1], status=SubmissionStatus.IN_REVIEW
        ),
        reviewer=reviewer,
        status=ReviewStatus.CANCELLED,
        score=2,
    )
    web_client.force_login(reviewer.user)

    content = web_client.get("/review/").content.decode()

    assert "Prace odebrane przez koordynatora" in content
    assert f'href="/review/{open_review.pk}/"' in content
    assert f'href="/review/{withdrawn.pk}/"' not in content
