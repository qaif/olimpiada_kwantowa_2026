"""Etap treningowy: piaskownica poza zawodami (``StageKind.TRAINING``).

Przedmiotem tych testów jest **jedno zdanie**: trening przechodzi całą ścieżkę portalu, ale nie
jest zawodami. Rozpada się ono na cztery reguły, z których każda pilnowana jest gdzie indziej,
więc każdą sprawdzamy osobno:

- nie jest „etapem bieżącym” (``services.current_stage``) ani nie stoi na publicznej osi czasu,
- nie kwalifikuje i nie jest następnym etapem dla nikogo (``results.services``),
- przyjmuje samodzielne zgłoszenia, tak jak eliminacje (``services.register_for_stage``),
- nie ma terminu, choć ma daty w bazie (``Stage.has_deadline``).

Osobno sprawdzamy narzędzia wokół treningu: generator PDF-ów i komendę, która je wgrywa.
"""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.accounts.tests.factories import ParticipantFactory
from apps.competitions.models import (
    TRAINING_DEADLINE,
    Edition,
    Problem,
    Stage,
    StageEntryStatus,
    StageFormat,
    StageKind,
)
from apps.competitions.services import (
    SELF_REGISTRATION_KINDS,
    current_stage,
    register_for_stage,
    training_stage,
)
from apps.competitions.training import TRAINING_PROBLEMS
from apps.core.api import DomainError
from apps.results.services import STAGE_ORDER, next_stage_of

from .factories import (
    CurrentEditionFactory,
    EditionFactory,
    QualificationRuleFactory,
    ScoringScaleFactory,
    StageFactory,
)

pytestmark = pytest.mark.django_db


def make_training_stage(edition: Edition, **kwargs) -> Stage:
    """Etap treningowy taki, jaki zakłada ``seed_training_problems``: otwarty, z datą 2099.

    Fabryka, a nie komenda: testy reguł mają widzieć dokładnie jeden etap, który zadeklarowały,
    i nie zależeć od tego, czy w drzewie leżą PDF-y.
    """
    now = timezone.now()
    stage = StageFactory(
        edition=edition,
        kind=StageKind.TRAINING,
        name="Zadania treningowe",
        format=StageFormat.SUBMISSIONS,
        opens_at=kwargs.pop("opens_at", now - timedelta(days=1)),
        deadline_at=TRAINING_DEADLINE,
        review_deadline_at=TRAINING_DEADLINE,
        appeal_window_opens_at=TRAINING_DEADLINE,
        appeal_window_closes_at=TRAINING_DEADLINE + timedelta(days=1),
        **kwargs,
    )
    ScoringScaleFactory(stage=stage)
    QualificationRuleFactory(stage=stage, min_points=0)
    return stage


# --- trening a „etap bieżący” --------------------------------------------------------------------


def test_current_stage_pomija_trening():
    """Otwarty bez końca trening nie może przesłaniać najbliższych zawodów."""
    edition = CurrentEditionFactory()
    elim = StageFactory(edition=edition, kind=StageKind.ELIM)
    make_training_stage(edition)

    assert current_stage(edition) == elim


def test_current_stage_jest_none_gdy_edycja_ma_tylko_trening():
    """Edycja z samym treningiem nie ma etapu bieżącego – a nie „ma trening”.

    Gdyby trening wchodził do tej listy jako etap ostatni (gałąź „nic nie jest otwarte”), strona
    główna ogłosiłaby piaskownicę jako zawody.
    """
    edition = CurrentEditionFactory()
    make_training_stage(edition)

    assert current_stage(edition) is None


def test_training_stage_zwraca_etap_albo_none():
    edition = CurrentEditionFactory()
    other = EditionFactory()
    stage = make_training_stage(edition)

    assert training_stage(edition) == stage
    assert training_stage(other) is None
    assert training_stage(None) is None


def test_has_deadline_jest_falszem_dla_treningu():
    """Data w bazie jest, terminu nie ma – i to ``has_deadline`` mówi szablonom, który to przypadek."""
    edition = CurrentEditionFactory()
    stage = make_training_stage(edition)

    assert stage.is_training is True
    assert stage.has_deadline is False
    assert stage.deadline_at == TRAINING_DEADLINE
    assert StageFactory(edition=edition, kind=StageKind.ELIM).has_deadline is True


# --- trening a kwalifikacja ----------------------------------------------------------------------


def test_trening_nie_jest_w_kolejnosci_etapow():
    assert StageKind.TRAINING not in STAGE_ORDER


def test_next_stage_of_treningu_jest_none():
    edition = CurrentEditionFactory()
    StageFactory(edition=edition, kind=StageKind.DISTRICT)
    stage = make_training_stage(edition)

    assert next_stage_of(stage) is None


def test_trening_nigdy_nie_jest_nastepnym_etapem():
    """Nawet gdy jest jedynym etapem obok eliminacji – kwalifikacja nie ma dokąd nikogo przenieść."""
    edition = CurrentEditionFactory()
    elim = StageFactory(edition=edition, kind=StageKind.ELIM)
    make_training_stage(edition)

    assert next_stage_of(elim) is None


# --- zapisy ---------------------------------------------------------------------------------------


def test_register_for_stage_przyjmuje_trening():
    edition = CurrentEditionFactory()
    stage = make_training_stage(edition)
    participant = ParticipantFactory()

    entry = register_for_stage(participant, stage)

    assert entry.stage == stage
    assert entry.status == StageEntryStatus.REGISTERED


@pytest.mark.parametrize("kind", [StageKind.DISTRICT, StageKind.FINAL])
def test_register_for_stage_nadal_odmawia_dalszym_etapom(kind):
    """Dołożenie treningu do zapisów otwartych nie mogło otworzyć etapów, które kwalifikują."""
    edition = CurrentEditionFactory()
    stage = StageFactory(edition=edition, kind=kind)
    participant = ParticipantFactory()

    with pytest.raises(DomainError) as exc:
        register_for_stage(participant, stage)

    assert exc.value.machine_code == "STAGE_NOT_OPEN_FOR_REGISTRATION"
    assert kind not in SELF_REGISTRATION_KINDS


# --- komenda seed_training_problems ---------------------------------------------------------------


def test_seed_training_problems_wymaga_biezacej_edycji():
    EditionFactory()  # edycja jest, ale nie bieżąca

    with pytest.raises(CommandError, match="Brak bieżącej edycji"):
        call_command("seed_training_problems")


def test_seed_training_problems_tworzy_etap_i_cztery_zadania():
    edition = CurrentEditionFactory()

    call_command("seed_training_problems", verbosity=0)

    stage = training_stage(edition)
    assert stage is not None
    assert stage.kind == StageKind.TRAINING
    assert stage.format == StageFormat.SUBMISSIONS
    assert stage.has_deadline is False
    assert stage.is_open_for_submissions(timezone.now())
    # Skala i próg idą z ``create_stage`` – bez nich etapu nie dałoby się przeliczyć.
    assert stage.scoring_scale.allowed_values() == {0, 2, 5, 6}
    assert stage.qualification_rule.min_points == 0

    problems = list(stage.problems.order_by("number"))
    assert [problem.number for problem in problems] == [1, 2, 3, 4]
    assert all(problem.statement_pdf for problem in problems)
    # Cztery zadania organizatora, każde z własnym plikiem (zadanie-P1.pdf … zadanie-P4.pdf).
    assert [problem.title[:3] for problem in problems] == ["P1.", "P2.", "P3.", "P4."]
    assert len({problem.statement_pdf.read() for problem in problems}) == 4
    for problem in problems:
        with problem.statement_pdf.open("rb") as handle:
            assert handle.read(5) == b"%PDF-"


def test_seed_training_problems_jest_idempotentna():
    """Drugi przebieg nie duplikuje zadań i **nie podmienia** plików – bajty się nie zmieniły.

    Podmiana zapisałaby PDF pod nową nazwą w prywatnym buckecie i unieważniła odnośnik, który
    uczestnik ma otwarty. Dlatego sprawdzamy nazwę pliku, a nie samą liczbę zadań.
    """
    edition = CurrentEditionFactory()
    call_command("seed_training_problems", verbosity=0)
    stage = training_stage(edition)
    before = {problem.number: problem.statement_pdf.name for problem in stage.problems.all()}

    call_command("seed_training_problems", verbosity=0)

    assert Stage.objects.filter(edition=edition, kind=StageKind.TRAINING).count() == 1
    assert Problem.objects.filter(stage=stage).count() == len(TRAINING_PROBLEMS)
    after = {problem.number: problem.statement_pdf.name for problem in stage.problems.all()}
    assert after == before


def test_seed_training_problems_nie_rusza_etapu_zawodow():
    """Etap bieżący zostaje etapem bieżącym – komenda dokłada trening obok, a nie zamiast."""
    edition = CurrentEditionFactory()
    elim = StageFactory(edition=edition, kind=StageKind.ELIM)

    call_command("seed_training_problems", verbosity=0)

    assert current_stage(edition) == elim
