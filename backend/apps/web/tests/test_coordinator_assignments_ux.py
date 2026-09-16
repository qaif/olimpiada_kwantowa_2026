"""Ekran „Przydziały i oceny” od strony obsługi: filtry, strony, czynności zbiorcze, historia.

``test_coordinator_assignments.py`` pilnuje **czynności** (reguła, przydział, korekta punktów).
Ten plik pilnuje tego, co decyduje o tym, czy da się ich użyć na prawdziwym etapie: czy tabelę da
się zawęzić, czy filtr przeżywa przejście na kolejną stronę, czy pakiet prac wykonuje się w 29/30
zamiast 0/30 i czy historia przy stu wierszach nie kosztuje stu zapytań.

Osobno także dlatego, że każdy z tych testów przechodzi przez **adres** (``?problem=``, ``?status=``,
POST na ``…/assignments/bulk/``), a nie przez serwis: to kontrakt ekranu z organizatorem, a nie
reguła domenowa.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.tests.factories import ActiveReviewerFactory, ParticipantFactory
from apps.competitions.tests.factories import StageEntryFactory
from apps.core.models import AuditLog
from apps.grading.models import Review, ReviewStatus
from apps.grading.services import assign_reviewer_to_submission
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

pytestmark = pytest.mark.django_db


def assignments_url(stage) -> str:
    return f"/coordinator/stages/{stage.pk}/assignments/"


def bulk_url(stage) -> str:
    return f"/coordinator/stages/{stage.pk}/assignments/bulk/"


def make_submission(stage, problem, *, status=SubmissionStatus.LOCKED):
    """Praca innego uczestnika w tym etapie – każdy wiersz tabeli to inna osoba."""
    entry = StageEntryFactory(participant=ParticipantFactory(), stage=stage)
    return SubmissionFactory(entry=entry, problem=problem, status=status)


def codes(response) -> str:
    return response.content.decode()


# --- filtry -------------------------------------------------------------------------------------


def test_problem_filter_narrows_the_table(web_client, coordinator, elim_stage, problems):
    first = make_submission(elim_stage, problems[0])
    second = make_submission(elim_stage, problems[1])
    web_client.force_login(coordinator)

    content = codes(web_client.get(assignments_url(elim_stage), {"problem": problems[0].pk}))

    assert first.entry.participant.public_code in content
    assert second.entry.participant.public_code not in content


def test_status_filter_separates_submitted_from_locked(web_client, coordinator, elim_stage, problems):
    waiting = make_submission(elim_stage, problems[0], status=SubmissionStatus.SUBMITTED)
    locked = make_submission(elim_stage, problems[0])
    web_client.force_login(coordinator)

    submitted = codes(web_client.get(assignments_url(elim_stage), {"status": "submitted"}))
    to_assign = codes(web_client.get(assignments_url(elim_stage), {"status": "to_assign"}))

    assert waiting.entry.participant.public_code in submitted
    assert locked.entry.participant.public_code not in submitted
    assert locked.entry.participant.public_code in to_assign


def test_unknown_status_shows_the_whole_list(web_client, coordinator, elim_stage, problems):
    """Literówka w adresie ma pokazać listę, a nie pustą tabelę bez wyjaśnienia."""
    locked = make_submission(elim_stage, problems[0])
    web_client.force_login(coordinator)

    content = codes(web_client.get(assignments_url(elim_stage), {"status": "nie-ma-takiego"}))

    assert locked.entry.participant.public_code in content


def test_reviewer_filter_shows_only_the_work_they_hold(web_client, coordinator, elim_stage, problems):
    mine = make_submission(elim_stage, problems[0])
    other = make_submission(elim_stage, problems[0])
    reviewer = ActiveReviewerFactory()
    assign_reviewer_to_submission(mine, reviewer)
    assign_reviewer_to_submission(other, ActiveReviewerFactory())
    web_client.force_login(coordinator)

    content = codes(web_client.get(assignments_url(elim_stage), {"reviewer": reviewer.pk}))

    assert mine.entry.participant.public_code in content
    assert other.entry.participant.public_code not in content


def test_withdrawn_review_leaves_the_reviewer_queue(web_client, coordinator, elim_stage, problems):
    """Odebrana praca znika z filtra recenzenta – w kolejce ma stać to, co trzyma w ręku."""
    submission = make_submission(elim_stage, problems[0])
    reviewer = ActiveReviewerFactory()
    review = assign_reviewer_to_submission(submission, reviewer)
    review.status = ReviewStatus.CANCELLED
    review.save(update_fields=["status"])
    web_client.force_login(coordinator)

    content = codes(web_client.get(assignments_url(elim_stage), {"reviewer": reviewer.pk}))

    assert submission.entry.participant.public_code not in content


def test_counters_describe_the_stage_not_the_filter(web_client, coordinator, elim_stage, problems):
    make_submission(elim_stage, problems[0], status=SubmissionStatus.SUBMITTED)
    make_submission(elim_stage, problems[0])
    make_submission(elim_stage, problems[1])
    web_client.force_login(coordinator)

    response = web_client.get(assignments_url(elim_stage), {"status": "submitted"})
    counters = {row["key"]: row["count"] for row in response.context["counters"]}

    assert counters["submitted"] == 1
    assert counters["to_assign"] == 2


def test_counters_link_to_their_own_filter_and_toggle_it_off(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    plain = web_client.get(assignments_url(elim_stage), {"q": "OLM"})
    active = web_client.get(assignments_url(elim_stage), {"status": "submitted"})
    by_key = {row["key"]: row for row in plain.context["counters"]}
    toggled = {row["key"]: row for row in active.context["counters"]}

    # Pozostałe filtry przechodzą do adresu licznika…
    assert "q=OLM" in by_key["submitted"]["query"]
    assert "status=submitted" in by_key["submitted"]["query"]
    # …a kliknięcie licznika już włączonego zdejmuje filtr.
    assert toggled["submitted"]["active"] is True
    assert "status=" not in toggled["submitted"]["query"]


# --- stronicowanie ------------------------------------------------------------------------------


def test_pagination_splits_rows_and_keeps_filters(web_client, coordinator, elim_stage, problems, monkeypatch):
    """Rozmiar strony podmieniamy, bo testem jest **podział**, a nie liczba sto."""
    from apps.web.views import coordinator as views

    monkeypatch.setattr(views, "ASSIGNMENTS_PAGE_SIZE", 2)
    made = [make_submission(elim_stage, problems[0]) for _ in range(3)]
    web_client.force_login(coordinator)

    first = web_client.get(assignments_url(elim_stage), {"problem": problems[0].pk})
    second = web_client.get(assignments_url(elim_stage), {"problem": problems[0].pk, "page": 2})

    assert first.context["paginator"].num_pages == 2
    assert len(first.context["submission_rows"]) == 2
    assert len(second.context["submission_rows"]) == 1
    # Filtr przeżywa przejście na kolejną stronę – w adresie i w tym, co strona pokazuje.
    assert f"problem={problems[0].pk}" in first.context["filter_query"]
    shown = codes(first) + codes(second)
    for submission in made:
        assert submission.entry.participant.public_code in shown


def test_page_number_returns_with_the_action(web_client, coordinator, elim_stage, problems):
    """Akcja z drugiej strony przefiltrowanej tabeli wraca na drugą stronę tej samej tabeli."""
    submission = make_submission(elim_stage, problems[0])
    reviewer = ActiveReviewerFactory()
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/submissions/{submission.pk}/assign-reviewer/",
        {"reviewer_id": reviewer.pk, "filters": f"status=to_assign&problem={problems[0].pk}&page=2"},
    )

    assert response["Location"].startswith(assignments_url(elim_stage))
    assert "status=to_assign" in response["Location"]
    assert "page=2" in response["Location"]


def test_foreign_parameters_do_not_ride_back_in_the_redirect(web_client, coordinator, elim_stage, problems):
    """``filters`` przepisuje wyłącznie znane klucze – reszta nie ma jak wejść do przekierowania."""
    submission = make_submission(elim_stage, problems[0])
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/submissions/{submission.pk}/lock-for-review/",
        {"filters": "status=to_assign&next=https://example.test/"},
    )

    assert "example.test" not in response["Location"]
    assert "status=to_assign" in response["Location"]


# --- czynności zbiorcze -------------------------------------------------------------------------


def test_bulk_assign_reports_done_and_skipped(web_client, coordinator, elim_stage, problems):
    reviewer = ActiveReviewerFactory()
    fresh = make_submission(elim_stage, problems[0])
    taken = make_submission(elim_stage, problems[0])
    assign_reviewer_to_submission(taken, reviewer)
    web_client.force_login(coordinator)

    response = web_client.post(
        bulk_url(elim_stage),
        {
            "action": "assign",
            "reviewer_id": reviewer.pk,
            "submission_ids": [fresh.pk, taken.pk],
        },
        follow=True,
    )
    content = codes(response)

    assert Review.objects.filter(submission=fresh, reviewer=reviewer).count() == 1
    assert Review.objects.filter(submission=taken, reviewer=reviewer).count() == 1
    assert "1 prac" in content
    # Pominięcie jest wypisane z powodem i z kodem pracy, żeby dało się je dokończyć ręcznie.
    assert "Pominięto 1" in content
    assert taken.entry.participant.public_code in content


def test_bulk_unassign_takes_the_work_back(web_client, coordinator, elim_stage, problems):
    reviewer = ActiveReviewerFactory()
    mine = make_submission(elim_stage, problems[0])
    untouched = make_submission(elim_stage, problems[0])
    review = assign_reviewer_to_submission(mine, reviewer)
    web_client.force_login(coordinator)

    response = web_client.post(
        bulk_url(elim_stage),
        {
            "action": "unassign",
            "reviewer_id": reviewer.pk,
            "submission_ids": [mine.pk, untouched.pk],
        },
        follow=True,
    )

    review.refresh_from_db()
    assert review.status == ReviewStatus.CANCELLED
    assert "nie ma tej pracy w ręku" in codes(response)


def test_bulk_lock_pulls_submitted_work_into_review(web_client, coordinator, elim_stage, problems):
    waiting = make_submission(elim_stage, problems[0], status=SubmissionStatus.SUBMITTED)
    already = make_submission(elim_stage, problems[0])
    web_client.force_login(coordinator)

    response = web_client.post(
        bulk_url(elim_stage),
        {"action": "lock", "submission_ids": [waiting.pk, already.pk]},
        follow=True,
    )

    waiting.refresh_from_db()
    assert waiting.status == SubmissionStatus.LOCKED
    assert "Pominięto 1" in codes(response)


def test_bulk_without_selection_is_refused(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = web_client.post(bulk_url(elim_stage), {"action": "lock"}, follow=True)

    assert "Nie zaznaczono żadnej pracy" in codes(response)


def test_bulk_without_action_is_refused(web_client, coordinator, elim_stage, problems):
    submission = make_submission(elim_stage, problems[0])
    web_client.force_login(coordinator)

    response = web_client.post(bulk_url(elim_stage), {"submission_ids": [submission.pk]}, follow=True)

    assert "Wybierz czynność zbiorczą" in codes(response)


def test_bulk_ignores_work_from_another_stage(web_client, coordinator, elim_stage, interview_stage, problems):
    """Zakresem czynności jest etap z adresu – identyfikator z innego etapu nie przechodzi."""
    from apps.competitions.tests.factories import ProblemFactory

    foreign = make_submission(
        interview_stage, ProblemFactory(stage=interview_stage, number=1), status=SubmissionStatus.SUBMITTED
    )
    web_client.force_login(coordinator)

    web_client.post(bulk_url(elim_stage), {"action": "lock", "submission_ids": [foreign.pk]})

    foreign.refresh_from_db()
    assert foreign.status == SubmissionStatus.SUBMITTED


def test_bulk_is_403_for_a_reviewer(web_client, reviewer, elim_stage, problems):
    submission = make_submission(elim_stage, problems[0], status=SubmissionStatus.SUBMITTED)
    web_client.force_login(reviewer.user)

    response = web_client.post(bulk_url(elim_stage), {"action": "lock", "submission_ids": [submission.pk]})

    submission.refresh_from_db()
    assert response.status_code == 403
    assert submission.status == SubmissionStatus.SUBMITTED


# --- historia -----------------------------------------------------------------------------------


def test_history_lists_audit_of_the_work_and_of_its_reviews(web_client, coordinator, elim_stage, problems):
    submission = make_submission(elim_stage, problems[0])
    reviewer = ActiveReviewerFactory()
    review = assign_reviewer_to_submission(submission, reviewer, actor=coordinator)
    web_client.force_login(coordinator)

    row = web_client.get(assignments_url(elim_stage)).context["submission_rows"][0]
    actions = {entry.action for entry in row["history"]}

    assert AuditLog.objects.filter(target_type="grading.review", target_id=str(review.pk)).exists()
    assert "review.assigned_manually" in actions


def test_history_costs_the_same_for_two_rows_and_for_six(web_client, coordinator, elim_stage, problems):
    """Jedno zapytanie na całą stronę, nie jedno na wiersz – inaczej sto wierszy to setki zapytań."""
    web_client.force_login(coordinator)

    def cost(count: int) -> int:
        for _ in range(count):
            submission = make_submission(elim_stage, problems[0])
            assign_reviewer_to_submission(submission, ActiveReviewerFactory(), actor=coordinator)
        with CaptureQueriesContext(connection) as captured:
            web_client.get(assignments_url(elim_stage))
        return len(captured)

    small = cost(2)
    large = cost(4)

    assert large <= small
    # Górna granica, żeby test złapał też regresję w innym miejscu ekranu (skale, liczniki, reguły).
    assert large <= 30


# --- praca bez JavaScriptu ------------------------------------------------------------------------


def test_every_change_is_a_plain_form_post(web_client, coordinator, elim_stage, problems):
    """Ekran nie ma ani jednego skryptu inline (strict CSP) i wysyła wszystko zwykłym POST-em."""
    submission = make_submission(elim_stage, problems[0])
    assign_reviewer_to_submission(submission, ActiveReviewerFactory())
    web_client.force_login(coordinator)

    content = codes(web_client.get(assignments_url(elim_stage)))

    assert "<script>" not in content
    assert "js/assignments.js" in content
    assert "css/assignments.css" in content
    assert f'action="{bulk_url(elim_stage)}"' in content
    # Zaznaczenie jest jedno i obsługuje dwie rzeczy – paczkę ZIP wskazuje ``formaction``.
    assert 'form="stage-rows"' in content
    assert f'formaction="/coordinator/stages/{elim_stage.pk}/download/"' in content


def test_rules_panel_is_collapsed_with_a_count(web_client, coordinator, elim_stage, problems):
    from apps.grading.services import add_problem_reviewer_rule

    add_problem_reviewer_rule(problems[0], ActiveReviewerFactory())
    web_client.force_login(coordinator)

    response = web_client.get(assignments_url(elim_stage))

    assert response.context["rules_count"] == 1
    assert "reguł: 1" in codes(response)


def test_reviewer_cannot_open_the_screen(web_client, reviewer, elim_stage):
    web_client.force_login(reviewer.user)

    assert web_client.get(assignments_url(elim_stage)).status_code == 403
