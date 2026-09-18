"""Migracja ``competitions.0024``: dzisiejszy przebieg Olimpiady Kwantowej zapisany jako wiersze.

To jest test z § 0.7: migracja zamienia stałą w kodzie (``STAGE_ORDER``) i wiersz starego modelu
(``QualificationRule``) na nową tabelę, więc w tym samym commicie stoi porównanie **wyniku migracji
ze źródłem**, a nie sprawdzenie, że cokolwiek powstało. Pytamy o cztery rzeczy:

1. kolejność kroków jest tą samą kolejnością, co ``STAGE_ORDER`` – i tą samą, którą dziś oddaje
   ``next_stage_of``;
2. cztery tryby progu odwzorowują się jeden do jednego, razem z podziałem na regiony;
3. trening stoi poza torem i nie dostaje reguły – nawet wtedy, gdy ma wpisany próg;
4. nic w ``Stage`` ani w ``QualificationRule`` nie drgnęło, a flaga ``process_editor`` została
   wyłączona.

Kształt testu jest ten sam, co w ``test_migration_edition_competition.py``: przewijanie migracji to
DDL po DML, więc potrzebny jest ``transaction=True``, a fikstura przywraca czoło także wtedy, gdy
test przerwie się w połowie.
"""

import importlib
from datetime import timedelta

import pytest
from django.apps import apps as django_apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from apps.competitions.models import (
    TRAINING_DEADLINE,
    PipelineStep,
    QualificationMode,
    QualificationRule,
    Stage,
    StageFormat,
    StageKind,
    TransitionGroupBy,
    TransitionMode,
    TransitionRule,
)
from apps.results.services import STAGE_ORDER, next_stage_of

from .factories import CurrentEditionFactory, EditionFactory, QualificationRuleFactory, StageFactory

BEFORE = ("competitions", "0023_pipeline_step_transition_rule")
AFTER = ("competitions", "0024_pipeline_from_stages")

#: Nazwa modułu zaczyna się od cyfry, więc ``from … import …`` jest tu składniowo niemożliwe.
pipeline_from_stages = importlib.import_module(f"apps.competitions.migrations.{AFTER[1]}")


def migrate_to(target) -> None:
    """Przewija bazę do wskazanej migracji."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target])
    executor.loader.build_graph()


def migrate_to_head() -> None:
    """Przywraca czoło migracji **wszystkich** aplikacji, nie tylko przewijanej."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())
    executor.loader.build_graph()


@pytest.fixture
def before_pipeline(transactional_db, competition):  # noqa: ARG001 - baza, używana przez efekt uboczny
    """Baza cofnięta do stanu sprzed wpisania przebiegu: tabele są, wierszy nie ma.

    Fikstura ``competition`` idzie **przed** przewinięciem, bo Konkurs #1 zakłada migracja
    ``tenancy.0002``, a test transakcyjny bywa uruchomiony na bazie już raz wyczyszczonej.
    """
    migrate_to(BEFORE)
    yield competition
    migrate_to_head()


def build_todays_edition(competition, **kwargs) -> dict[str, Stage]:
    """Edycja o kształcie dzisiejszej Olimpiady Kwantowej: trening + trzy etapy zawodów (§ 1.2.1).

    Progi są takie, jakie wpisuje koordynator w panelu: minimum punktów na eliminacjach, „najlepszych
    N” na etapie okręgowym, finał bez progu (nie ma dokąd kwalifikować). Trening dostaje **próg
    mimo wszystko** – po to, żeby było widać, że migracja go nie przepisuje.
    """
    edition = kwargs.pop("edition", None) or CurrentEditionFactory(competition=competition)
    now = timezone.now()
    elim = StageFactory(
        edition=edition, kind=StageKind.ELIM, name="Etap I – eliminacje", format=StageFormat.SUBMISSIONS
    )
    QualificationRuleFactory(stage=elim, mode=QualificationMode.MIN_POINTS, min_points=40)
    district = StageFactory(
        edition=edition,
        kind=StageKind.DISTRICT,
        name="Etap II – rozmowy kwalifikacyjne",
        format=StageFormat.INTERVIEW,
    )
    QualificationRuleFactory(stage=district, mode=QualificationMode.TOP_N, min_points=None, top_n=30)
    final = StageFactory(edition=edition, kind=StageKind.FINAL, name="Etap III – finał")
    training = StageFactory(
        edition=edition,
        kind=StageKind.TRAINING,
        name="Zadania treningowe",
        opens_at=now - timedelta(days=1),
        deadline_at=TRAINING_DEADLINE,
        review_deadline_at=TRAINING_DEADLINE,
        appeal_window_opens_at=TRAINING_DEADLINE,
        appeal_window_closes_at=TRAINING_DEADLINE + timedelta(days=1),
    )
    QualificationRuleFactory(stage=training, mode=QualificationMode.MIN_POINTS, min_points=0)
    return {"edition": edition, "elim": elim, "district": district, "final": final, "training": training}


# --- przebieg Konkursu #1 --------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_the_migration_writes_todays_pipeline_of_competition_one(before_pipeline):
    """Trzy kroki toru w kolejności zawodów i jeden krok poza torem – nic więcej."""
    stages = build_todays_edition(before_pipeline)

    migrate_to(AFTER)

    steps = {step.stage_id: step for step in PipelineStep.objects.filter(edition=stages["edition"])}
    assert len(steps) == 4
    assert (steps[stages["elim"].pk].position, steps[stages["elim"].pk].off_pipeline) == (1, False)
    assert (steps[stages["district"].pk].position, steps[stages["district"].pk].off_pipeline) == (
        2,
        False,
    )
    assert (steps[stages["final"].pk].position, steps[stages["final"].pk].off_pipeline) == (3, False)
    assert (steps[stages["training"].pk].position, steps[stages["training"].pk].off_pipeline) == (
        0,
        True,
    )


@pytest.mark.django_db(transaction=True)
def test_pipeline_matches_stage_order(before_pipeline):
    """Kolejność z danych jest **tą samą** kolejnością, co krotka w serwisie i co ``next_stage_of``.

    Dwie asercje, bo to dwa różne sposoby na cichą rozbieżność: lista rodzajów odpowiada za zapis
    (czy migracja wpisała to, co trzeba), a porównanie z ``next_stage_of`` – za odczyt, czyli za
    to, że przy włączonej fladze T30 dostanie z kroków tę samą odpowiedź, co dziś ze stałej.
    """
    stages = build_todays_edition(before_pipeline)

    migrate_to(AFTER)

    on_track = list(
        PipelineStep.objects.filter(edition=stages["edition"], off_pipeline=False)
        .select_related("stage")
        .order_by("position")
    )
    assert [step.stage.kind for step in on_track] == list(STAGE_ORDER)
    for step, following in zip(on_track, [*on_track[1:], None], strict=True):
        expected = following.stage if following is not None else None
        assert next_stage_of(step.stage) == expected
    assert next_stage_of(stages["training"]) is None


@pytest.mark.django_db(transaction=True)
def test_the_training_stage_stays_outside_the_pipeline(before_pipeline):
    """``off_pipeline=True`` znaczy dokładnie to, co dziś znaczy nieobecność w ``STAGE_ORDER``.

    Trening ma w tej fiksturze wpisany próg – i właśnie dlatego jest tu asercja o jego braku:
    reguła przejścia dla etapu, który nikogo nie kwalifikuje, byłaby progiem do nikąd.
    """
    stages = build_todays_edition(before_pipeline)

    migrate_to(AFTER)

    step = PipelineStep.objects.get(stage=stages["training"])
    assert step.off_pipeline is True
    assert step.transition_rules.count() == 0
    assert QualificationRule.objects.filter(stage=stages["training"]).exists()


# --- odwzorowanie progów ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("mode", "fields", "expected"),
    [
        (
            QualificationMode.MIN_POINTS,
            {"min_points": 40, "top_n": None},
            (TransitionMode.MIN_POINTS, TransitionGroupBy.NONE, 40, None),
        ),
        (
            QualificationMode.TOP_N,
            {"min_points": None, "top_n": 30},
            (TransitionMode.TOP_N, TransitionGroupBy.NONE, None, 30),
        ),
        (
            QualificationMode.TOP_N_PER_DISTRICT,
            {"min_points": None, "top_n": 5},
            (TransitionMode.TOP_N_PER_GROUP, TransitionGroupBy.REGION, None, 5),
        ),
        (
            QualificationMode.HYBRID,
            {"min_points": 50, "top_n": 20},
            (TransitionMode.HYBRID, TransitionGroupBy.NONE, 50, 20),
        ),
    ],
)
def test_every_qualification_mode_maps_one_to_one(before_pipeline, mode, fields, expected):
    """Cztery tryby progu, cztery reguły przejścia – co do trybu, podziału i obu liczb."""
    edition = CurrentEditionFactory(competition=before_pipeline)
    stage = StageFactory(edition=edition, kind=StageKind.ELIM)
    QualificationRuleFactory(stage=stage, mode=mode, **fields)

    migrate_to(AFTER)

    rule = TransitionRule.objects.get(step__stage=stage)
    assert (rule.mode, rule.group_by, rule.min_points, rule.top_n) == expected
    assert rule.position == 0
    assert rule.category_id is None


@pytest.mark.django_db(transaction=True)
def test_the_mapping_covers_every_mode_of_today():
    """Tryb bez odwzorowania przerwałby wdrożenie, więc pilnujemy kompletu bez ruszania bazy."""
    assert set(pipeline_from_stages.MODE_MAP) == set(QualificationMode.values)


@pytest.mark.django_db(transaction=True)
def test_a_stage_without_a_threshold_gets_no_rule(before_pipeline):
    """Finał nie ma dokąd kwalifikować – i nie dostaje pustej reguły „na wszelki wypadek”."""
    stages = build_todays_edition(before_pipeline)

    migrate_to(AFTER)

    assert TransitionRule.objects.filter(step__stage=stages["final"]).count() == 0
    assert TransitionRule.objects.filter(step__edition=stages["edition"]).count() == 2


# --- co migracja zostawia w spokoju --------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_the_migration_changes_no_stage_and_no_threshold(before_pipeline):
    """Punkt 6 z § 0.2: migracja pisze **tylko** wiersze opisujące przebieg."""
    stages = build_todays_edition(before_pipeline)
    fields = ("kind", "name", "format", "opens_at", "deadline_at", "review_deadline_at")
    before_stages = sorted(Stage.objects.values_list("id", *fields))
    before_rules = sorted(
        QualificationRule.objects.values_list("id", "stage_id", "mode", "min_points", "top_n")
    )

    migrate_to(AFTER)

    assert sorted(Stage.objects.values_list("id", *fields)) == before_stages
    assert (
        sorted(QualificationRule.objects.values_list("id", "stage_id", "mode", "min_points", "top_n"))
        == before_rules
    )
    assert stages["district"].format == StageFormat.INTERVIEW


@pytest.mark.django_db(transaction=True)
def test_the_migration_does_not_switch_the_flag_on(before_pipeline):
    """Dane wchodzą, zachowanie nie: czytelnika tych wierszy włącza dopiero organizator."""
    build_todays_edition(before_pipeline)

    migrate_to(AFTER)
    before_pipeline.refresh_from_db()

    assert before_pipeline.has_feature("process_editor") is False


@pytest.mark.django_db(transaction=True)
def test_running_the_migration_twice_changes_nothing(before_pipeline):
    """Idempotencja: powtórzony przebieg nie duplikuje kroków ani nie przestawia reguł."""
    stages = build_todays_edition(before_pipeline)

    migrate_to(AFTER)
    snapshot = sorted(PipelineStep.objects.values_list("stage_id", "edition_id", "position", "off_pipeline"))
    rules = sorted(TransitionRule.objects.values_list("step_id", "mode", "group_by", "min_points", "top_n"))

    pipeline_from_stages.forwards(django_apps, connection.schema_editor())

    assert PipelineStep.objects.filter(edition=stages["edition"]).count() == 4
    assert (
        sorted(PipelineStep.objects.values_list("stage_id", "edition_id", "position", "off_pipeline"))
        == snapshot
    )
    assert (
        sorted(TransitionRule.objects.values_list("step_id", "mode", "group_by", "min_points", "top_n"))
        == rules
    )


@pytest.mark.django_db(transaction=True)
def test_archival_editions_get_their_own_pipeline(before_pipeline):
    """Migracja chodzi po **każdej** edycji – rocznik sprzed lat też ma opisany przebieg."""
    build_todays_edition(before_pipeline)
    archival = EditionFactory(competition=before_pipeline)
    old_elim = StageFactory(edition=archival, kind=StageKind.ELIM)

    migrate_to(AFTER)

    step = PipelineStep.objects.get(stage=old_elim)
    assert (step.edition_id, step.position, step.off_pipeline) == (archival.pk, 1, False)
