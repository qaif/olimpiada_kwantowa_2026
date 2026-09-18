"""Punkty z rozmowy w sumie etapu (T36, ``docs/UNIWERSALNY-ETAP-2.md`` § 1.2.3).

Domknięcie luki opisanej w ``docs/BACKLOG.md``: dotąd ``compute_stage_results`` dawało dla rozmowy
same zera, a punkty wpisywał koordynator w ``/admin/``. Teraz komponent ``INTERVIEW`` czyta
``InterviewScore`` – tą samą drogą i z tą samą wagą, co komponent pisemny i testowy.

Trzy pytania, w tej kolejności:

1. **Czy etap bez komponentu rozmowy zachowuje się dokładnie jak dziś?** Konkurs #1 nie ma ani
   jednego komponentu, więc nowa tabela nie ma prawa zmienić ani jednej ogłoszonej sumy – to jest
   wymaganie nadrzędne (§ 0.1) i dlatego stoi tu pierwsze.
2. Czy punkty z rozmowy wchodzą do sumy z wagą komponentu i czy dwie rozmowy w jednym etapie liczą
   się niezależnie?
3. Czy brak wyniku z **wymaganej** rozmowy zamyka tabelę tak samo, jak nierozliczona praca
   (``STAGE_NOT_FINALIZED``) – i czy rozmowa nieobowiązkowa liczy się jako zero?

Sumy w tym pliku sprawdza ``compute_stage_results``, a nie sam serwis wpisu: wpis, jego bramkę
flagi i więzy wiersza sprawdza ``apps/competitions/tests/test_interview_scores.py``.
"""

from __future__ import annotations

import pytest

from apps.competitions.interviews import record_interview_score
from apps.competitions.models import ComponentKind, InterviewScore, StageComponent
from apps.core.api import DomainError
from apps.results.services import PROCESS_EDITOR_FLAG, compute_stage_results

from .conftest import graded_entry, make_stage

pytestmark = pytest.mark.django_db


def enable(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), PROCESS_EDITOR_FLAG: True}
    competition.save(update_fields=["feature_flags"])
    return competition


def add_component(stage, kind, *, position: int = 1, numerator: int = 1, denominator: int = 1, **kwargs):
    return StageComponent.objects.create(
        stage=stage,
        kind=kind,
        position=position,
        weight_numerator=numerator,
        weight_denominator=denominator,
        **kwargs,
    )


def score(entry, component, points, actor=None):
    """Wpis komisji **przez serwis**, bo to on jest jedyną drogą do tej tabeli."""
    return record_interview_score(entry, component, points, actor=actor)


def totals(stage) -> list[int]:
    return [row["total"] for row in compute_stage_results(stage)]


# --- (a) etap bez komponentu rozmowy: ani jednej zmiany ---------------------------------------------


def test_a_stage_without_components_takes_todays_path(competition):
    """Konkurs #1 ma flagę wyłączoną, ale nawet z włączoną nie ma ani jednego komponentu.

    Etap bez komponentów czyta ``Stage.format`` i sumuje oceny bez wag – dokładnie jak przed
    etapem 2 – a nowa tabela punktów z rozmowy nie ma wtedy kogo dotyczyć.
    """
    stage = make_stage(problems=1)
    graded_entry(stage, [6])
    enable(competition)

    assert totals(stage) == [6]
    assert "components" not in compute_stage_results(stage)[0]


def test_the_flag_off_keeps_the_interview_points_out_of_the_table(competition):
    """Komponent i wynik w bazie, flaga wyłączona – suma jest dokładnie dzisiejsza."""
    stage = make_stage(problems=1)
    entry = graded_entry(stage, [6])
    component = add_component(stage, ComponentKind.INTERVIEW, position=2)
    InterviewScore.objects.create(entry=entry, component=component, points=5, max_points=6)

    assert totals(stage) == [6]
    assert "components" not in compute_stage_results(stage)[0]


# --- (b) punkty z rozmowy w sumie --------------------------------------------------------------------


def test_an_interview_component_brings_its_points_into_the_total(competition):
    stage = make_stage(problems=1)
    entry = graded_entry(stage, [6])
    written = add_component(stage, ComponentKind.SUBMISSIONS, position=1)
    interview = add_component(stage, ComponentKind.INTERVIEW, position=2)
    enable(competition)
    score(entry, interview, 5)

    row = compute_stage_results(stage)[0]

    assert row["components"] == {str(written.pk): 6, str(interview.pk): 5}
    assert row["total"] == 11


def test_the_interview_weight_counts_like_every_other(competition):
    """Waga jest ułamkiem zwykłym, a zaokrąglenie następuje **raz**, na końcu (§ 1.2.6)."""
    stage = make_stage(problems=1)
    entry = graded_entry(stage, [6])
    add_component(stage, ComponentKind.SUBMISSIONS, position=1)
    interview = add_component(stage, ComponentKind.INTERVIEW, position=2, numerator=1, denominator=2)
    enable(competition)
    score(entry, interview, 5)

    # 6 + 5/2 = 8,5 → 9 (połówka w górę, tą samą metodą co test online).
    assert totals(stage) == [9]


def test_two_interviews_in_one_stage_score_independently(competition):
    """Rozmowa wstępna i finałowa to dwa komponenty i dwa wyniki – nie jedna liczba w dwóch kolumnach."""
    stage = make_stage(problems=0)
    entry = graded_entry(stage, [])
    first = add_component(stage, ComponentKind.INTERVIEW, position=1)
    second = add_component(stage, ComponentKind.INTERVIEW, position=2)
    enable(competition)
    score(entry, first, 2)
    score(entry, second, 6)

    row = compute_stage_results(stage)[0]

    assert row["components"] == {str(first.pk): 2, str(second.pk): 6}
    assert row["total"] == 8


def test_the_snapshot_still_does_not_carry_the_breakdown(competition):
    """Rozbicie na komponenty zostaje w wierszu roboczym – ogłoszona tabela ma jawną listę pól."""
    from apps.results.models import Anonymization
    from apps.results.services import build_snapshot

    stage = make_stage(problems=0)
    entry = graded_entry(stage, [])
    interview = add_component(stage, ComponentKind.INTERVIEW)
    enable(competition)
    score(entry, interview, 5)

    snapshot = build_snapshot(compute_stage_results(stage), Anonymization.CODE)

    assert snapshot[0]["total"] == 5
    assert "components" not in snapshot[0]


# --- (c) brak wyniku: wymagany zamyka tabelę, nieobowiązkowy jest zerem --------------------------------


def test_a_missing_required_interview_holds_the_table(competition):
    """Brak wyniku nie jest zerem, tylko brakującą oceną – ten sam kod, co przy nierozliczonej pracy."""
    stage = make_stage(problems=0)
    graded_entry(stage, [])
    add_component(stage, ComponentKind.INTERVIEW, required=True)
    enable(competition)

    with pytest.raises(DomainError) as error:
        compute_stage_results(stage)

    assert error.value.machine_code == "STAGE_NOT_FINALIZED"


def test_a_zero_from_the_committee_is_a_result_like_any_other(competition):
    """„Komisja wpisała zero” zamyka etap; „komisja jeszcze nie wpisała” – nie."""
    stage = make_stage(problems=0)
    entry = graded_entry(stage, [])
    interview = add_component(stage, ComponentKind.INTERVIEW, required=True)
    enable(competition)
    score(entry, interview, 0)

    assert totals(stage) == [0]


def test_an_optional_interview_counts_as_zero(competition):
    """``required=False`` to rozmowa dodatkowa – nie trzyma całej tabeli za zakładnika."""
    stage = make_stage(problems=1)
    graded_entry(stage, [6])
    add_component(stage, ComponentKind.SUBMISSIONS, position=1)
    add_component(stage, ComponentKind.INTERVIEW, position=2, required=False)
    enable(competition)

    assert totals(stage) == [6]


def test_the_preview_never_waits_for_the_committee(competition):
    """Symulacja progu z definicji biegnie w trakcie – brama „wynik wpisany” zamknęłaby ją całkiem."""
    stage = make_stage(problems=0)
    graded_entry(stage, [])
    add_component(stage, ComponentKind.INTERVIEW, required=True)
    enable(competition)

    assert [row["total"] for row in compute_stage_results(stage, preview=True)] == [0]
