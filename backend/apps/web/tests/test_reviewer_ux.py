"""Czytelność panelu recenzenta: zakładki kolejki, pasek podsumowania i układ strony oceny.

Te testy pilnują **kształtu ekranu**, a nie reguł domenowych – tamte są w ``test_reviewer.py``,
``test_reviewer_tools.py`` i ``test_reviewer_extras.py``. Pilnują dwóch rzeczy naraz i obie są
tu celem:

1. że kolejka jest kolejką (zakładki po stanie, porządek po terminie, podsumowanie nad listą),
   a strona oceny jest dwiema kolumnami z przyklejonym panelem;
2. że **żaden formularz nie zmienił adresu**. Przestawianie sekcji na ekranie nie może po cichu
   przekierować zapisu szkicu albo zgłoszenia problemu gdzie indziej, a jest to dokładnie ten
   rodzaj usterki, którego nie widać na zrzucie ekranu.
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.tests.factories import ActiveReviewerFactory, ParticipantFactory
from apps.competitions.tests.factories import StageEntryFactory
from apps.grading.models import Review, ReviewStatus
from apps.grading.tests.factories import ReviewFactory
from apps.grading.worklog import heartbeat
from apps.submissions.models import AvStatus, SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory, SubmissionFileFactory

pytestmark = pytest.mark.django_db


def _review(stage, problem, reviewer, *, status=ReviewStatus.ASSIGNED, **kwargs):
    submission = SubmissionFactory(
        entry=StageEntryFactory(stage=stage, participant=ParticipantFactory()),
        problem=problem,
        status=SubmissionStatus.IN_REVIEW,
    )
    return ReviewFactory(submission=submission, reviewer=reviewer, status=status, **kwargs)


# --- kolejka: zakładki i porządek ----------------------------------------------------------------


def test_queue_has_a_tab_for_every_review_state(web_client, elim_stage, problems):
    """Cztery stany, cztery zakładki – i wszystkie cztery wypełnia serwer, nie przeglądarka."""
    reviewer = ActiveReviewerFactory()
    _review(elim_stage, problems[0], reviewer)
    _review(elim_stage, problems[0], reviewer, status=ReviewStatus.DRAFT)
    _review(elim_stage, problems[0], reviewer, status=ReviewStatus.SUBMITTED, score=5)
    _review(elim_stage, problems[0], reviewer, status=ReviewStatus.CANCELLED)
    web_client.force_login(reviewer.user)

    content = web_client.get(reverse("web:review-list")).content.decode()

    for key in ("todo", "draft", "submitted", "cancelled"):
        assert f'data-queue-tab="{key}"' in content
        assert f'data-queue-panel="{key}"' in content


def test_first_non_empty_tab_is_the_one_that_opens(web_client, elim_stage, problems):
    """Otwarcie pustej zakładki pokazywałoby „nic tu nie ma” nad listą, w której coś jest."""
    reviewer = ActiveReviewerFactory()
    _review(elim_stage, problems[0], reviewer, status=ReviewStatus.SUBMITTED, score=5)
    web_client.force_login(reviewer.user)

    content = web_client.get(reverse("web:review-list")).content.decode()

    assert 'data-queue-active="submitted"' in content


def test_queue_is_ordered_by_due_date_not_by_assignment(web_client, elim_stage, problems):
    """Recenzent ma zacząć od tego, co przepada najwcześniej – nawet jeśli dostał to później."""
    reviewer = ActiveReviewerFactory()
    later = _review(elim_stage, problems[0], reviewer)
    sooner = _review(elim_stage, problems[0], reviewer)
    now = timezone.now()
    Review.objects.filter(pk=later.pk).update(due_at=now + timedelta(days=9))
    Review.objects.filter(pk=sooner.pk).update(due_at=now + timedelta(days=1))
    web_client.force_login(reviewer.user)

    content = web_client.get(reverse("web:review-list")).content.decode()

    assert content.index(f'href="/review/{sooner.pk}/"') < content.index(f'href="/review/{later.pk}/"')


def test_row_says_how_much_time_is_left(web_client, elim_stage, problems):
    reviewer = ActiveReviewerFactory()
    review = _review(elim_stage, problems[0], reviewer)
    Review.objects.filter(pk=review.pk).update(due_at=timezone.now() + timedelta(days=3, hours=2))
    web_client.force_login(reviewer.user)

    content = web_client.get(reverse("web:review-list")).content.decode()

    assert "za 3 dni" in content
    assert "po terminie" not in content


def test_summary_strip_answers_how_much_is_left_and_by_when(web_client, elim_stage, problems):
    reviewer = ActiveReviewerFactory()
    review = _review(elim_stage, problems[0], reviewer)
    _review(elim_stage, problems[0], reviewer, status=ReviewStatus.SUBMITTED, score=5)
    Review.objects.filter(pk=review.pk).update(due_at=timezone.now() + timedelta(days=4))
    start = timezone.now()
    heartbeat(review, now=start - timedelta(seconds=180))
    heartbeat(review, now=start)
    web_client.force_login(reviewer.user)

    content = web_client.get(reverse("web:review-list")).content.decode()

    assert "do zrobienia" in content
    assert "najbliższy termin" in content
    # Suma czasu jest jedna na cały panel: licznik przycina pojedynczy sygnał do 90 sekund.
    assert "czas pracy łącznie" in content
    assert "1 min" in content


def test_empty_queue_explains_who_assigns_the_work(web_client, elim_stage):
    reviewer = ActiveReviewerFactory()
    web_client.force_login(reviewer.user)

    content = web_client.get(reverse("web:review-list")).content.decode()

    assert "Brak przydziałów" in content
    assert "Przydziału dokonuje koordynator" in content
    # Pierwsza pomoc dla oceniającego po raz pierwszy stoi na liście, zwinięta.
    assert "Jak oceniać" in content


def test_queue_ships_the_tab_script(web_client, elim_stage, problems):
    reviewer = ActiveReviewerFactory()
    _review(elim_stage, problems[0], reviewer)
    web_client.force_login(reviewer.user)

    content = web_client.get(reverse("web:review-list")).content.decode()

    assert "js/reviewer-layout.js" in content
    assert "css/reviewer.css" in content


# --- strona oceny: dwie kolumny i przyklejony panel ----------------------------------------------


@pytest.fixture
def review(elim_stage, problems):
    reviewer = ActiveReviewerFactory()
    item = _review(elim_stage, problems[0], reviewer)
    SubmissionFileFactory(submission=item.submission, av_status=AvStatus.CLEAN, mime="application/pdf")
    return item


def test_detail_is_two_columns_with_a_sticky_grading_panel(web_client, review):
    web_client.force_login(review.reviewer.user)

    content = web_client.get(reverse("web:review-detail", kwargs={"pk": review.pk})).content.decode()

    assert 'class="review-layout"' in content
    assert 'class="pdf-pane"' in content
    # ``review-panel`` jest tym, co arkusz przykleja powyżej 1000 px (static/css/reviewer.css).
    assert 'class="form-pane review-panel" id="review-panel"' in content
    assert "css/reviewer.css" in content


def test_detail_offers_a_way_into_the_panel_on_a_narrow_screen(web_client, review):
    """Poniżej 1000 px panel oceny ląduje pod rozwiązaniem – pasek jest drogą do niego."""
    web_client.force_login(review.reviewer.user)

    content = web_client.get(reverse("web:review-detail", kwargs={"pk": review.pk})).content.decode()

    assert 'class="review-jump" href="#review-panel"' in content


def test_annotation_toolbar_stands_above_the_preview(web_client, review):
    web_client.force_login(review.reviewer.user)

    content = web_client.get(reverse("web:review-detail", kwargs={"pk": review.pk})).content.decode()

    assert "data-annotate-mode" in content
    assert "data-annotations-toggle" in content
    assert "data-annotations-filter" in content
    assert content.index("data-annotate-mode") < content.index('id="pdf-viewer"')


def test_keyboard_shortcuts_are_documented_where_they_work(web_client, review):
    web_client.force_login(review.reviewer.user)

    content = web_client.get(reverse("web:review-detail", kwargs={"pk": review.pk})).content.decode()

    assert "js/reviewer-layout.js" in content
    assert "Skróty klawiaturowe" in content
    assert 'data-shortcut="draft"' in content


def test_grading_panel_keeps_the_order_the_reviewer_works_in(web_client, review):
    """Punkty, komentarz dla uczestnika, komentarz wewnętrzny, przyciski – w tej kolejności."""
    web_client.force_login(review.reviewer.user)

    content = web_client.get(reverse("web:review-detail", kwargs={"pk": review.pk})).content.decode()

    assert (
        content.index('name="score"')
        < content.index('id="id_comment_for_participant"')
        < content.index('id="id_comment_internal"')
        < content.index("Wystaw ocenę")
    )
    # Szablony komentarzy są podpowiedzią do pola dla uczestnika i stoją tuż pod nim.
    assert content.index('id="id_comment_for_participant"') < content.index('id="comment-snippets"')
    assert content.index('id="comment-snippets"') < content.index('id="id_comment_internal"')


def test_helper_texts_explain_the_scale_and_the_internal_comment(web_client, review):
    web_client.force_login(review.reviewer.user)

    content = web_client.get(reverse("web:review-detail", kwargs={"pk": review.pk})).content.decode()

    assert "Skala etapu: wolno wystawić wyłącznie jedną z poniższych wartości" in content
    assert "„Wewnętrzny” znaczy" in content


def test_cancelled_review_shows_no_grading_form_at_all(web_client, elim_stage, problems):
    reviewer = ActiveReviewerFactory()
    review = _review(elim_stage, problems[0], reviewer, status=ReviewStatus.CANCELLED)
    web_client.force_login(reviewer.user)

    content = web_client.get(reverse("web:review-detail", kwargs={"pk": review.pk})).content.decode()

    assert 'id="review-form"' not in content
    assert "Koordynator odebrał Ci tę pracę." in content


# --- adresy formularzy: przestawianie sekcji niczego nie przekierowuje ---------------------------


def test_every_form_still_posts_to_its_own_url(web_client, review):
    """Najważniejszy test tego pliku: układ się zmienił, adresy zapisu – nie."""
    web_client.force_login(review.reviewer.user)
    pk = review.pk

    content = web_client.get(reverse("web:review-detail", kwargs={"pk": pk})).content.decode()

    assert f'action="{reverse("web:review-submit", kwargs={"pk": pk})}"' in content
    assert f'hx-post="{reverse("web:review-draft", kwargs={"pk": pk})}"' in content
    assert f'action="{reverse("web:review-issue-add", kwargs={"pk": pk})}"' in content
    assert f'action="{reverse("web:review-snippet-add")}"' in content
    assert 'hx-target="#draft-status"' in content


def test_revision_form_still_posts_to_the_revision_url(web_client, elim_stage, problems):
    reviewer = ActiveReviewerFactory()
    review = _review(elim_stage, problems[0], reviewer, status=ReviewStatus.SUBMITTED, score=5)
    web_client.force_login(reviewer.user)

    content = web_client.get(reverse("web:review-detail", kwargs={"pk": review.pk})).content.decode()

    assert f'action="{reverse("web:review-revise", kwargs={"pk": review.pk})}"' in content
