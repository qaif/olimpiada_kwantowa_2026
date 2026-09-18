"""Punkty z rozmowy: wpis komisji, więzy wiersza i bramka flagi (T36, § 1.2.3, decyzja D9).

Przedmiotem tego pliku jest **wpis**, a nie suma etapu: to, czy punkty z rozmowy wchodzą do tabeli
wyników i z jaką wagą, sprawdza ``apps/results/tests/test_interview_component.py``, bo tam mieszka
jego kod.

Cztery rzeczy, na których stoi ta sekcja:

- dla Konkursu #1 ekran zamiast ``/admin/`` **jest** zmianą widoczną (§ 0.1), więc przy wyłączonej
  fladze ``process_editor`` nie wolno zapisać ani jednego wiersza – i to jest pierwsza asercja,
- ocena z rozmowy należy do **tej samej** skali, co ocena pracy pisemnej (``allowed_scores``);
  konkurs ma jedną skalę na etap i rozmowa nie jest od niej wyjątkiem,
- jedna rozmowa to jeden wynik: powtórny wpis jest poprawką, a nie drugą prawdą o tym samym
  komponencie,
- każdy wpis zostawia ślad w audycie, a w ``diff`` nie ma ani jednej danej osobowej – w tym uwagi
  komisji, która bywa daną osobową sama z siebie.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.competitions.interviews import (
    MAX_NOTE_LENGTH,
    PROCESS_EDITOR_FLAG,
    interview_components_for,
    interview_score_rows,
    interview_scores_for,
    record_interview_score,
)
from apps.competitions.models import (
    ComponentKind,
    InterviewScore,
    StageComponent,
    StageEntryStatus,
)
from apps.core.api import DomainError
from apps.core.models import AuditLog

from .factories import (
    CurrentEditionFactory,
    InterviewSlotFactory,
    InterviewStageFactory,
    ScoringScaleFactory,
    StageEntryFactory,
    StageFactory,
)

pytestmark = pytest.mark.django_db

#: Dane uczestnika użyte w asercji „tego nie ma w audycie”. Muszą być **rozpoznawalne**: przy
#: „Jan Kowalski” asercja przechodzi także wtedy, gdy nazwisko tam jest.
PII_FIRST_NAME = "Bartłomiejusz"
PII_LAST_NAME = "Wróblewski-Zadrożny"


def enable(competition):
    """Flaga edytora przebiegu – bez niej cała sekcja jest zamknięta (§ 0.6)."""
    competition.feature_flags = {**(competition.feature_flags or {}), PROCESS_EDITOR_FLAG: True}
    competition.save(update_fields=["feature_flags"])
    return competition


def make_stage(factory=InterviewStageFactory, **kwargs):
    """Etap rozmowy ze skalą 0/2/5/6 – ta sama skala, którą etap niesie dla prac pisemnych.

    Każde wywołanie robi **własną edycję** (fabryka domyślna), bo bieżąca edycja jest w konkursie
    jedna (więz w bazie), a wpis punktów bieżącej edycji nie wymaga – wymaga jej dopiero zapis na
    termin rozmowy.
    """
    stage = factory(**kwargs)
    ScoringScaleFactory(stage=stage)
    return stage


def add_component(stage, kind=ComponentKind.INTERVIEW, **kwargs):
    """Komponent bez fabryki: ``tests/factories.py`` jest plikiem wspólnym kilku zadań naraz."""
    return StageComponent.objects.create(stage=stage, kind=kind, **kwargs)


def make_entry(stage, **kwargs):
    participant = ParticipantFactory(user__first_name=PII_FIRST_NAME, user__last_name=PII_LAST_NAME)
    return StageEntryFactory(
        stage=stage, participant=participant, status=StageEntryStatus.QUALIFIED, **kwargs
    )


@pytest.fixture
def coordinator_user():
    return CoordinatorFactory()


# --- bramka flagi ---------------------------------------------------------------------------------


def test_scoring_is_closed_while_the_process_editor_is_off(competition, coordinator_user):
    """Konkurs #1 bez flagi: ekran komisji nie zapisuje nic, a punkty zostają tam, gdzie były."""
    stage = make_stage()
    component = add_component(stage)
    entry = make_entry(stage)

    with pytest.raises(DomainError) as error:
        record_interview_score(entry, component, 5, actor=coordinator_user)

    assert error.value.machine_code == "PROCESS_EDITOR_DISABLED"
    assert not InterviewScore.objects.exists()


def test_competition_one_has_no_interview_scores(competition):
    """Migracja ``0031`` tworzy **pustą** tabelę – żaden istniejący etap nie dostaje wiersza."""
    assert not InterviewScore.objects.for_competition(competition).exists()


# --- wpis komisji ---------------------------------------------------------------------------------


def test_recording_a_score_stores_the_points_and_the_scale_maximum(competition, coordinator_user):
    enable(competition)
    stage = make_stage()
    component = add_component(stage)
    entry = make_entry(stage)

    score = record_interview_score(entry, component, 5, actor=coordinator_user, note="komisja A")

    assert (score.points, score.max_points) == (5, 6)
    assert (score.entry_id, score.component_id) == (entry.pk, component.pk)
    assert score.recorded_by == coordinator_user
    assert score.note == "komisja A"


def test_recording_a_score_works_on_a_written_stage(competition, coordinator_user):
    """Rozmowa jest **komponentem**, nie formą etapu – na tym polega § 1.2.3.

    Etap pisemny z dodatkową rozmową jest dokładnie tym, czego dzisiejsze ``Stage.format`` opisać
    nie umie; bramka „to nie jest etap rozmowy” zamknęłaby więc tę zdolność zaraz po dołożeniu.
    """
    enable(competition)
    stage = make_stage(StageFactory)
    component = add_component(stage, position=2)
    entry = make_entry(stage)

    assert record_interview_score(entry, component, 2, actor=coordinator_user).points == 2


def test_a_second_entry_is_a_correction_not_a_second_truth(competition, coordinator_user):
    enable(competition)
    stage = make_stage()
    component = add_component(stage)
    entry = make_entry(stage)

    record_interview_score(entry, component, 2, actor=coordinator_user)
    record_interview_score(entry, component, 6, actor=coordinator_user, note="po naradzie")

    score = InterviewScore.objects.get()
    assert (score.points, score.note) == (6, "po naradzie")
    assert AuditLog.objects.filter(action="interview.scored").count() == 2


def test_a_long_note_is_trimmed_instead_of_refused(competition, coordinator_user):
    """Komisja wpisuje punkty pod presją czasu – odmowa z powodu długości notatki kosztuje wynik."""
    enable(competition)
    stage = make_stage()
    component = add_component(stage)
    entry = make_entry(stage)

    score = record_interview_score(entry, component, 0, actor=coordinator_user, note="x" * 500)

    assert len(score.note) == MAX_NOTE_LENGTH


def test_a_score_outside_the_stage_scale_is_refused(competition, coordinator_user):
    """Ta sama skala i ten sam kod błędu, co przy ocenie pracy (``SCORE_NOT_IN_SCALE``)."""
    enable(competition)
    stage = make_stage()
    component = add_component(stage)
    entry = make_entry(stage)

    with pytest.raises(DomainError) as error:
        record_interview_score(entry, component, 4, actor=coordinator_user)

    assert error.value.machine_code == "SCORE_NOT_IN_SCALE"
    assert not InterviewScore.objects.exists()


def test_a_written_component_does_not_take_interview_points(competition, coordinator_user):
    enable(competition)
    stage = make_stage()
    written = add_component(stage, ComponentKind.SUBMISSIONS)
    entry = make_entry(stage)

    with pytest.raises(DomainError) as error:
        record_interview_score(entry, written, 5, actor=coordinator_user)

    assert error.value.machine_code == "COMPONENT_NOT_INTERVIEW"


def test_an_entry_from_another_stage_is_refused(competition, coordinator_user):
    """Punkty przypisane wpisowi z sąsiedniego etapu byłyby w tabeli liczbą znikąd."""
    enable(competition)
    stage = make_stage()
    component = add_component(stage)
    stranger = make_entry(make_stage())

    with pytest.raises(DomainError) as error:
        record_interview_score(stranger, component, 5, actor=coordinator_user)

    assert error.value.machine_code == "ENTRY_NOT_IN_STAGE"


# --- audyt ----------------------------------------------------------------------------------------


def test_the_audit_entry_carries_numbers_and_never_the_note(competition, coordinator_user):
    """W ``diff`` idą identyfikatory i liczby; uwaga komisji bywa daną osobową i nie wchodzi tam."""
    enable(competition)
    stage = make_stage()
    component = add_component(stage)
    entry = make_entry(stage)

    record_interview_score(entry, component, 5, actor=coordinator_user, note="mówił o chorobie")

    log = AuditLog.objects.get(action="interview.scored")
    assert log.diff == {"component": component.pk, "points": 5, "max_points": 6}
    assert log.target_id == str(entry.pk)
    serialized = str(log.diff)
    assert PII_LAST_NAME not in serialized
    assert "chorobie" not in serialized


# --- więzy wiersza ----------------------------------------------------------------------------------


def test_one_result_per_entry_and_component(competition):
    stage = make_stage()
    component = add_component(stage)
    entry = make_entry(stage)
    InterviewScore.objects.create(entry=entry, component=component, points=5, max_points=6)

    with pytest.raises(IntegrityError), transaction.atomic():
        InterviewScore.objects.create(entry=entry, component=component, points=2, max_points=6)


def test_points_above_the_maximum_do_not_reach_the_table(competition):
    """Ostatnia linia obrony przed „12 punktów z 10” – wynik spoza skali wchodziłby do sumy etapu."""
    stage = make_stage()
    component = add_component(stage)
    entry = make_entry(stage)

    with pytest.raises(IntegrityError), transaction.atomic():
        InterviewScore.objects.create(entry=entry, component=component, points=12, max_points=6)


def test_clean_refuses_a_component_from_another_stage(competition):
    stage = make_stage()
    entry = make_entry(stage)
    foreign = add_component(make_stage())

    with pytest.raises(ValidationError) as error:
        InterviewScore(entry=entry, component=foreign, points=5, max_points=6).full_clean()

    assert "component" in error.value.message_dict


def test_clean_refuses_a_component_that_is_not_an_interview(competition):
    stage = make_stage()
    entry = make_entry(stage)
    written = add_component(stage, ComponentKind.SUBMISSIONS)

    with pytest.raises(ValidationError) as error:
        InterviewScore(entry=entry, component=written, points=5, max_points=6).full_clean()

    assert "component" in error.value.message_dict


# --- odczyty dla ekranu i dla sumy etapu ------------------------------------------------------------


def test_scores_come_out_keyed_by_component(competition, coordinator_user):
    """Dwie rozmowy w jednym etapie to dwa niezależne wyniki – klucz po rodzaju dałby jeden."""
    enable(competition)
    stage = make_stage()
    first = add_component(stage, position=1)
    second = add_component(stage, position=2)
    entry = make_entry(stage)
    record_interview_score(entry, first, 5, actor=coordinator_user)
    record_interview_score(entry, second, 2, actor=coordinator_user)

    assert interview_scores_for(stage) == {first.pk: {entry.pk: 5}, second.pk: {entry.pk: 2}}


def test_an_entry_without_a_result_has_no_key_at_all(competition, coordinator_user):
    """„Komisja wpisała zero” i „komisja jeszcze nie wpisała” to dwie różne rzeczy."""
    enable(competition)
    stage = make_stage()
    component = add_component(stage)
    scored = make_entry(stage)
    waiting = make_entry(stage)
    record_interview_score(scored, component, 0, actor=coordinator_user)

    assert interview_scores_for(stage)[component.pk] == {scored.pk: 0}
    assert waiting.pk not in interview_scores_for(stage)[component.pk]


def test_only_interview_components_land_on_the_committee_screen(competition):
    stage = make_stage()
    interview = add_component(stage, position=1)
    add_component(stage, ComponentKind.SUBMISSIONS, position=2)

    assert interview_components_for(stage) == [interview]


def test_the_committee_rows_carry_the_booking_and_the_result(competition, coordinator_user):
    """Ekran komisji czyta termin i dotychczasowy wynik – bez zapytania na uczestnika."""
    from apps.competitions.interviews import book_slot

    enable(competition)
    stage = make_stage(edition=CurrentEditionFactory())
    component = add_component(stage)
    entry = make_entry(stage)
    slot = InterviewSlotFactory(stage=stage)
    book_slot(entry.participant, slot)
    record_interview_score(entry, component, 5, actor=coordinator_user)

    rows = interview_score_rows(stage, component)

    assert len(rows) == 1
    assert rows[0]["entry"] == entry
    assert rows[0]["booking"].slot_id == slot.pk
    assert rows[0]["score"].points == 5


def test_a_row_without_a_result_is_an_empty_field(competition):
    stage = make_stage()
    component = add_component(stage)
    make_entry(stage)

    assert interview_score_rows(stage, component)[0]["score"] is None


# --- izolacja konkursów ------------------------------------------------------------------------------


def test_the_score_belongs_to_the_competition_of_its_entry(competition, other_competition):
    """Droga do konkursu wiedzie przez wpis do etapu – ta sama, co przy zapisie na rozmowę."""
    stage = make_stage()
    component = add_component(stage)
    entry = make_entry(stage)
    score = InterviewScore.objects.create(entry=entry, component=component, points=5, max_points=6)

    assert list(InterviewScore.objects.for_competition(competition)) == [score]
    assert not InterviewScore.objects.for_competition(other_competition).exists()
