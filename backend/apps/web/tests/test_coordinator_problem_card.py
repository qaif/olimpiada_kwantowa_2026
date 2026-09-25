"""Karta zadania (``/coordinator/problems/<id>/``).

Sprawdzamy to, czego nie widać w testach serwisów: że karta zbiera w jedno miejsce treść, skalę,
rubrykę, reguły, prace i statystyki jednego zadania, że każdy formularz celuje w **istniejący**
adres akcji koordynatora (karta nie ma własnych adresów zapisu), że sekcje opcjonalne znikają
zamiast wywracać stronę i że nikt poza koordynatorem tu nie wejdzie.
"""

import pytest

from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.competitions.problem_card import bar_width
from apps.core.tests.query_budgets import budget
from apps.grading.models import ProblemReviewerRule, ReviewStatus, RubricCriterion
from apps.grading.tests.factories import FinalGradeFactory, ReviewFactory
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

from .conftest import PARTICIPANT_LAST_NAME

pytestmark = pytest.mark.django_db


def problem_url(problem) -> str:
    return f"/coordinator/problems/{problem.pk}/"


@pytest.fixture
def locked(entry, problems):
    return SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.LOCKED)


# --- dostęp -------------------------------------------------------------------------------------


def test_non_coordinator_gets_403(web_client, reviewer, problems):
    web_client.force_login(reviewer.user)

    assert web_client.get(problem_url(problems[0])).status_code == 403


def test_participant_gets_403(web_client, participant, problems):
    web_client.force_login(participant.user)

    assert web_client.get(problem_url(problems[0])).status_code == 403


def test_anonymous_is_redirected_to_login(web_client, problems):
    response = web_client.get(problem_url(problems[0]))

    assert response.status_code == 302
    assert "/login/" in response["Location"]


def test_missing_problem_is_404(web_client, coordinator):
    web_client.force_login(coordinator)

    assert web_client.get("/coordinator/problems/999999/").status_code == 404


# --- sekcje -------------------------------------------------------------------------------------


def test_card_renders_all_sections(web_client, coordinator, problems, locked):
    web_client.force_login(coordinator)

    response = web_client.get(problem_url(problems[0]))
    content = response.content.decode()

    assert response.status_code == 200
    for heading in (
        "Treść i ustawienia",
        "Skala i rubryka",
        "Reguły przydziału",
        "Wzorcówka",
        "Prace",
        "Pobierz",
        "Statystyki",
    ):
        assert heading in content
    assert problems[0].title in content
    assert locked.entry.participant.public_code in content
    # Koordynator jest jedyną rolą, która widzi nazwisko obok kodu publicznego.
    assert PARTICIPANT_LAST_NAME in content


def test_card_shows_inherited_stage_scale(web_client, coordinator, problems):
    """Puste nadpisanie przy zadaniu znaczy „dziedzicz po etapie” – i karta mówi to wprost."""
    web_client.force_login(coordinator)

    response = web_client.get(problem_url(problems[0]))

    assert response.context["scale"]["source"] == "stage"
    assert response.context["scale"]["values"] == [0, 2, 5, 6]
    assert "skala etapu" in response.content.decode()


def test_card_shows_problem_scale_override(web_client, coordinator, problems):
    problem = problems[0]
    problem.scoring_values = [{"value": 0}, {"value": 10}]
    problem.max_points = 10
    problem.save(update_fields=["scoring_values", "max_points"])
    web_client.force_login(coordinator)

    response = web_client.get(problem_url(problem))

    assert response.context["scale"]["source"] == "problem"
    assert response.context["scale"]["values"] == [0, 10]
    assert "skala własna zadania" in response.content.decode()


def test_card_lists_rubric_criteria(web_client, coordinator, problems):
    RubricCriterion.objects.create(
        problem=problems[0], order=1, title="Uzasadnienie przejścia granicznego", max_points=3
    )
    web_client.force_login(coordinator)

    content = web_client.get(problem_url(problems[0])).content.decode()

    assert "Uzasadnienie przejścia granicznego" in content


def test_card_rules_use_existing_endpoints(web_client, coordinator, problems, reviewer):
    rule = ProblemReviewerRule.objects.create(problem=problems[0], reviewer=reviewer)
    other = ActiveReviewerFactory()
    web_client.force_login(coordinator)

    content = web_client.get(problem_url(problems[0])).content.decode()

    assert f'action="/coordinator/reviewer-rules/{rule.pk}/delete/"' in content
    assert f'action="/coordinator/problems/{problems[0].pk}/reviewer-rules/"' in content
    # Osoba, która regułę już ma, nie stoi w liście wyboru – druga taka sama jest niemożliwa.
    assert f'<option value="{other.pk}">' in content
    assert f'<option value="{reviewer.pk}">{reviewer.user.email}' not in content


def test_card_work_row_posts_to_existing_endpoints(web_client, coordinator, problems, locked, reviewer):
    review = ReviewFactory(submission=locked, reviewer=reviewer, status=ReviewStatus.SUBMITTED, score=5)
    web_client.force_login(coordinator)

    content = web_client.get(problem_url(problems[0])).content.decode()

    assert f'action="/coordinator/submissions/{locked.pk}/assign-reviewer/"' in content
    assert f'action="/coordinator/submissions/{locked.pk}/final-grade/"' in content
    assert f'action="/coordinator/reviews/{review.pk}/score/"' in content
    assert f'action="/coordinator/reviews/{review.pk}/unassign/"' in content


def test_card_offers_lock_for_submitted_work(web_client, coordinator, entry, problems):
    """Praca oddana, jeszcze niezablokowana: zamiast przydziału stoi „Zablokuj do oceny”."""
    submitted = SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.SUBMITTED)
    web_client.force_login(coordinator)

    content = web_client.get(problem_url(problems[0])).content.decode()

    assert f'action="/coordinator/submissions/{submitted.pk}/lock-for-review/"' in content
    assert f'action="/coordinator/submissions/{submitted.pk}/assign-reviewer/"' not in content


def test_card_search_filters_works(web_client, coordinator, problems, locked):
    web_client.force_login(coordinator)

    hit = web_client.get(problem_url(problems[0]), {"q": PARTICIPANT_LAST_NAME.lower()})
    miss = web_client.get(problem_url(problems[0]), {"q": "Kowalski-nie-ma"})

    assert locked.entry.participant.public_code in hit.content.decode()
    assert locked.entry.participant.public_code not in miss.content.decode()


def test_card_shows_only_this_problems_works(web_client, coordinator, entry, problems):
    mine = SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.LOCKED)
    other = SubmissionFactory(entry=entry, problem=problems[1], status=SubmissionStatus.LOCKED)
    web_client.force_login(coordinator)

    rows = web_client.get(problem_url(problems[0])).context["rows"]

    ids = {row["submission"].pk for row in rows}
    assert mine.pk in ids
    assert other.pk not in ids


def test_card_zip_link_carries_problem_filter(web_client, coordinator, problems, elim_stage):
    web_client.force_login(coordinator)

    content = web_client.get(problem_url(problems[0])).content.decode()

    assert f"/coordinator/stages/{elim_stage.pk}/download/?problem={problems[0].pk}" in content


def test_card_similarity_link_points_at_stage_screen(web_client, coordinator, problems, elim_stage):
    web_client.force_login(coordinator)

    response = web_client.get(problem_url(problems[0]))

    assert response.context["similarity_url"] == f"/coordinator/stages/{elim_stage.pk}/similarity/"


def test_card_distribution_counts_final_grades(web_client, coordinator, problems, locked):
    FinalGradeFactory(submission=locked, score=5)
    web_client.force_login(coordinator)

    response = web_client.get(problem_url(problems[0]))
    distribution = response.context["distribution"]

    assert distribution["total"] == 1
    assert distribution["mean"] == 5
    bar = next(item for item in distribution["bars"] if item["value"] == 5)
    assert bar["count"] == 1
    assert bar["width"] == 100
    # Wartość ze skali, której nikt nie dostał, zostaje na osi – „nikt nie dostał 6” jest informacją.
    assert any(item["value"] == 6 and item["count"] == 0 for item in distribution["bars"])
    assert "score-bars__fill--w100" in response.content.decode()


def test_card_without_works_still_renders(web_client, coordinator, problems):
    web_client.force_login(coordinator)

    response = web_client.get(problem_url(problems[1]))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Do tego zadania nikt jeszcze nie oddał pracy." in content
    assert "Żadna praca z tego zadania nie ma jeszcze oceny końcowej." in content


def test_card_without_model_solution_says_so(web_client, coordinator, problems):
    web_client.force_login(coordinator)

    response = web_client.get(problem_url(problems[0]))

    assert response.context["model_solution_url"] is None
    assert "To zadanie nie ma wgranej wzorcówki." in response.content.decode()


# --- szerokość słupka ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("count", "total", "expected"),
    [
        (0, 10, 0),
        (10, 10, 100),
        (1, 10, 10),
        # Jedna praca na dwustu to 0,5% – zaokrąglenie do zera kłamałoby („nikt nie dostał tylu”).
        (1, 200, 5),
        (0, 0, 0),
    ],
)
def test_bar_width_never_rounds_a_real_value_down_to_zero(count, total, expected):
    assert bar_width(count, total) == expected


# --- koszt --------------------------------------------------------------------------------------


def _add_works(entry, problem, reviewer, count: int, start: int = 0) -> None:
    for number in range(start, start + count):
        submission = SubmissionFactory(
            entry=entry, problem=problem, status=SubmissionStatus.LOCKED, version=number + 1
        )
        ReviewFactory(submission=submission, reviewer=reviewer, status=ReviewStatus.SUBMITTED, score=2)


def test_card_query_count_does_not_grow_with_works(
    web_client, coordinator, entry, problems, reviewer, django_assert_max_num_queries
):
    """Dziesięć prac ma kosztować tyle, co jedna – wiersze budują trzy agregaty, a nie pętla.

    Sprawdzamy **niezmienność**, a nie okrągłą liczbę: stały narzut (sesja, ustawienia serwisu,
    menu panelu) bywa zmieniany przez sąsiednie ekrany i nie o nim jest ta asercja. Sufit stoi
    obok wyłącznie jako bezpiecznik przed regresją rzędu wielkości.
    """
    _add_works(entry, problems[0], reviewer, 1)
    web_client.force_login(coordinator)
    # Pierwsze żądanie po zalogowaniu jest droższe od każdego następnego: wypełnia pamięci
    # podręczne procesu (ustawienia serwisu Wagtaila, definicje uprawnień). Ta asercja jest o tym,
    # jak koszt zależy od **danych**, więc rozgrzewka odbywa się przed pomiarem.
    web_client.get(problem_url(problems[0]))

    with django_assert_max_num_queries(budget("coordinator/problem-card")) as few:
        assert web_client.get(problem_url(problems[0])).status_code == 200

    _add_works(entry, problems[0], reviewer, 9, start=1)
    with django_assert_max_num_queries(budget("coordinator/problem-card")) as many:
        assert web_client.get(problem_url(problems[0])).status_code == 200

    assert len(many.captured_queries) == len(few.captured_queries)
