"""Suma etapu z wagami i przesunięciem skali oraz wiersz wpisu drużynowego (§ 1.2.3, § 1.2.6).

Przedmiotem jest to, czego nie widać po kodzie ``compute_stage_results``:

- **Konkurs #1 nie zmienia ani jednej liczby.** Bez flagi ``weighted_scoring`` suma jest dzisiejszym
  sumowaniem ``int``, a z flagą – przy wagach ``1/1`` i przesunięciu ``0`` – daje wiersz **równy**
  co do klucza i co do wartości. To jest ta sama asercja, co ``test_weight_one_over_one_equals_
  integer_sum`` i ``test_scoring_offset_zero_changes_nothing`` z § 5.2, tyle że po stronie wyników.
- **Zaokrąglenie zapada raz i w górę.** Suma idzie przez ``Fraction``, więc nie zależy od kolejności
  dodawania; dopiero wynik sprowadza się do pełnych punktów metodą ``ROUND_HALF_UP``.
- **Ocena w tabeli jest oceną wystawioną**, a nie liczbą z bazy: przy skali z punktami ujemnymi
  leży ona przesunięta, a odjęcie przesunięcia ma zapaść **raz**, w jednym miejscu.
- **Brak pracy to zero punktów**, także w skali z punktami ujemnymi – gdzie „zero w bazie” znaczy
  „najniższa ocena skali”, a nie „nic nie oddał”.
- **Wpis drużynowy** podpisuje się nazwą drużyny, a w tabeli po pseudonimach – kodem publicznym.
"""

import pytest

from apps.competitions.models import StageEntry, Team
from apps.competitions.tests.factories import ProblemFactory
from apps.grading.tests.factories import FinalGradeFactory
from apps.results.models import Anonymization
from apps.results.services import build_snapshot, compute_stage_results
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

from .conftest import graded_entry, make_stage, stage_problems

pytestmark = pytest.mark.django_db

WEIGHTED = "weighted_scoring"
TEAMS = "team_entries"

#: Skala z punktem ujemnym. Najniższa wartość to ``-3``, więc przesunięcie wynosi ``3``, a oceny
#: leżą w bazie jako ``0``, ``3`` i ``8``.
NEGATIVE_VALUES = [
    {"value": -3, "label": "odpowiedź błędna"},
    {"value": 0, "label": "brak odpowiedzi"},
    {"value": 5, "label": "odpowiedź poprawna"},
]


def enable(competition, *names):
    """Włącza flagi tą samą drogą, którą zrobi to wdrożenie: wartością w bazie."""
    competition.feature_flags = {**(competition.feature_flags or {}), **{name: True for name in names}}
    competition.save(update_fields=["feature_flags"])
    return competition


def set_weights(problem, numerator, denominator):
    problem.weight_numerator = numerator
    problem.weight_denominator = denominator
    problem.save(update_fields=["weight_numerator", "weight_denominator"])
    return problem


def negative_scale(stage):
    """Skala z punktami ujemnymi wpisana wprost, razem z przesunięciem, które jej odpowiada."""
    scale = stage.scoring_scale
    scale.values = NEGATIVE_VALUES
    scale.max_value = 5
    scale.offset = 3
    scale.full_clean()
    scale.save()
    return scale


# --- waga 1/1 i przesunięcie 0: Konkurs #1 nie zmienia ani jednej liczby ---------------------------


def test_weight_one_over_one_gives_exactly_todays_row(competition):
    """Ten sam etap liczony obiema drogami daje wiersz **równy**, a nie podobny."""
    stage = make_stage(problems=2)
    graded_entry(stage, [6, 2])

    today = compute_stage_results(stage)
    enable(competition, WEIGHTED)
    weighted = compute_stage_results(stage)

    assert today == weighted
    assert weighted[0]["total"] == 8


def test_scoring_offset_zero_changes_no_points(competition):
    """Skala bez punktów ujemnych ma przesunięcie zero – i wtedy ocena w tabeli jest oceną z bazy."""
    enable(competition, WEIGHTED)
    stage = make_stage(problems=2)
    first, second = stage_problems(stage)
    graded_entry(stage, [5, 0])

    rows = compute_stage_results(stage)

    assert rows[0]["points"] == {str(first.number): 5, str(second.number): 0}
    assert rows[0]["total"] == 5


# --- wagi ------------------------------------------------------------------------------------------


def test_weight_halves_the_problem(competition):
    enable(competition, WEIGHTED)
    stage = make_stage(problems=2)
    first, second = stage_problems(stage)
    set_weights(first, 1, 2)
    graded_entry(stage, [6, 2])

    rows = compute_stage_results(stage)

    # 6 * 1/2 + 2 * 1/1 = 5. Ocena w tabeli zostaje **niewagowana** – waga jest cechą sumy,
    # a nie oceny: recenzent wystawił sześć i sześć ma stać w kolumnie zadania.
    assert rows[0]["points"] == {str(first.number): 6, str(second.number): 2}
    assert rows[0]["total"] == 5


def test_rounding_is_half_up_and_happens_once(competition):
    """Trzy trzecie po 5 punktów dają 5, a nie 4 – bo ``1/3`` nie jest zaokrąglane po drodze."""
    enable(competition, WEIGHTED)
    stage = make_stage(problems=3)
    for problem in stage_problems(stage):
        set_weights(problem, 1, 3)
    graded_entry(stage, [5, 5, 5])

    assert compute_stage_results(stage)[0]["total"] == 5


def test_half_a_point_rounds_up(competition):
    enable(competition, WEIGHTED)
    stage = make_stage(problems=1)
    set_weights(stage_problems(stage)[0], 1, 2)
    graded_entry(stage, [5])

    # 5 * 1/2 = 2,5 → 3 (ROUND_HALF_UP), tą samą metodą, co przy punktach z testu online.
    assert compute_stage_results(stage)[0]["total"] == 3


def test_weight_zero_keeps_the_problem_out_of_the_total(competition):
    enable(competition, WEIGHTED)
    stage = make_stage(problems=2)
    first, second = stage_problems(stage)
    set_weights(first, 0, 1)
    graded_entry(stage, [6, 2])

    rows = compute_stage_results(stage)

    assert rows[0]["points"][str(first.number)] == 6
    assert rows[0]["total"] == 2


# --- przesunięcie skali ----------------------------------------------------------------------------


def test_points_are_the_score_the_reviewer_gave(competition):
    """Baza trzyma ``0``, a tabela ma pokazać ``-3`` – odjęcie przesunięcia zapada raz."""
    enable(competition, WEIGHTED)
    stage = make_stage(problems=2)
    first, second = stage_problems(stage)
    negative_scale(stage)
    graded_entry(stage, [0, 8])

    rows = compute_stage_results(stage)

    assert rows[0]["points"] == {str(first.number): -3, str(second.number): 5}
    assert rows[0]["total"] == 2


def test_a_problem_without_a_grade_counts_as_zero_even_with_an_offset(competition):
    """Brak pracy to zero punktów, a nie najniższa ocena skali – to dwie różne rzeczy."""
    enable(competition, WEIGHTED)
    stage = make_stage(problems=2)
    first, second = stage_problems(stage)
    negative_scale(stage)
    graded_entry(stage, [8, None])

    rows = compute_stage_results(stage)

    assert rows[0]["points"] == {str(first.number): 5, str(second.number): 0}
    assert rows[0]["total"] == 5


def test_the_total_never_drops_below_zero(competition):
    enable(competition, WEIGHTED)
    stage = make_stage(problems=2)
    negative_scale(stage)
    graded_entry(stage, [0, 0])

    rows = compute_stage_results(stage)

    assert rows[0]["total"] == 0


def test_a_problem_with_its_own_scale_is_not_shifted(competition):
    """Własna skala zadania jest osobną skalą, a nie wariantem etapowej – przesunięcie jej nie dotyczy."""
    enable(competition, WEIGHTED)
    stage = make_stage(problems=1)
    negative_scale(stage)
    own = ProblemFactory(
        competition=competition,
        stage=stage,
        number=9,
        scoring_values=[{"value": 0, "label": "nie"}, {"value": 4, "label": "tak"}],
        max_points=4,
    )
    entry = graded_entry(stage, [8])
    submission = SubmissionFactory(entry=entry, problem=own, status=SubmissionStatus.FINAL)
    FinalGradeFactory(submission=submission, score=4)

    rows = compute_stage_results(stage)

    assert rows[0]["points"][str(own.number)] == 4
    assert rows[0]["total"] == 9


# --- wpis drużynowy --------------------------------------------------------------------------------


def team_entry(stage, competition, *, name="Kwanty 1", code="OLM-TEAM1"):
    """Wpis, którego właścicielem jest drużyna – bez uczestnika, tak jak zakłada więz ``single_owner``."""
    team = Team.objects.create(
        competition=competition,
        edition=stage.edition,
        name=name,
        public_code=code,
        school="LO nr 7",
    )
    return StageEntry.objects.create(stage=stage, team=team, participant=None)


def test_a_team_row_carries_the_team_name_and_code(competition):
    enable(competition, TEAMS)
    stage = make_stage(problems=1)
    team_entry(stage, competition)

    rows = compute_stage_results(stage)

    assert rows[0]["public_code"] == "OLM-TEAM1"
    assert rows[0]["team_name"] == "Kwanty 1"
    # Drużyna nie ma nazwiska, wieku ani zgód – i to jest stan „domyślnie zamknięty”, a nie brak.
    assert rows[0]["participant_id"] is None
    assert rows[0]["publish_full_name"] is False
    assert rows[0]["is_adult"] is False


def test_a_team_is_signed_with_its_name_in_the_published_table(competition):
    enable(competition, TEAMS)
    stage = make_stage(problems=1)
    team_entry(stage, competition)
    rows = compute_stage_results(stage)

    assert build_snapshot(rows, Anonymization.FULL)[0]["display"] == "Kwanty 1"
    assert build_snapshot(rows, Anonymization.INITIALS_SCHOOL)[0]["display"] == "Kwanty 1"


def test_a_team_in_the_pseudonymous_table_stays_a_code(competition):
    """Tabela po pseudonimach jest po pseudonimach bez wyjątku – nazwa składu bywa nazwiskiem."""
    enable(competition, TEAMS)
    stage = make_stage(problems=1)
    team_entry(stage, competition)
    rows = compute_stage_results(stage)

    assert build_snapshot(rows, Anonymization.CODE)[0]["display"] == "OLM-TEAM1"


def test_a_participant_row_has_no_team_key(competition):
    """Wiersz uczestnika nie zyskuje ani jednego klucza – to jest ta sama reguła, co przy kategoriach."""
    enable(competition, TEAMS)
    stage = make_stage(problems=1)
    graded_entry(stage, [6])

    assert "team_name" not in compute_stage_results(stage)[0]
