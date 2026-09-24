"""Dowolne wartości ocen (wydanie 0.35.0): reguła oceny, przełącznik etapu i samo maksimum zadania.

Prośba organizatora z 2026-09-24: „Pozwól na dowolne wartości ocen” (każda liczba z dokładnością do
0,01 między minimum a maksimum skali, przełącznik per etap) oraz „zadania mogą mieć różną ilość
punktów” (samo maksimum zadania w trybie dowolnym). Tu stoją reguły domeny – ``scoring.score_rule``
i ``services.set_scoring_scale``; drogi zapisu ocen sprawdzają testy ``apps.grading`` i ``apps.web``.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.tests.factories import CoordinatorFactory
from apps.competitions.models import ScoringScale, StageKind
from apps.competitions.scoring import (
    ScoreRule,
    problem_maxima_by_number,
    problem_maximum,
    score_rule,
    stage_maximum_total,
    uses_own_range,
)
from apps.competitions.services import create_stage, scores_in_use, set_scoring_scale, stage_scoring
from apps.core.api import DomainError
from apps.grading.models import ReviewStatus
from apps.grading.tests.factories import FinalGradeFactory, ReviewFactory
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

from .factories import EditionFactory, ProblemFactory, ScoringScaleFactory, StageEntryFactory, StageFactory

pytestmark = pytest.mark.django_db

DEFAULT_VALUES = [
    {"value": 0, "label": "brak istotnego postępu"},
    {"value": 2, "label": "istotny postęp"},
    {"value": 5, "label": "drobne usterki"},
    {"value": 6, "label": "pełne"},
]
NEGATIVE_VALUES = [
    {"value": -3, "label": "odpowiedź błędna"},
    {"value": 0, "label": "brak odpowiedzi"},
    {"value": 5, "label": "odpowiedź poprawna"},
]


@pytest.fixture
def coordinator():
    return CoordinatorFactory()


@pytest.fixture
def stage():
    created = StageFactory(kind=StageKind.ELIM)
    ScoringScaleFactory(stage=created)
    return created


def enable_weights(stage) -> None:
    """Flaga ``weighted_scoring`` na **tym** obiekcie konkursu, który czyta serwis (przez etap)."""
    competition = stage.edition.competition
    competition.feature_flags = {**(competition.feature_flags or {}), "weighted_scoring": True}
    competition.save(update_fields=["feature_flags"])


def make_free(stage, coordinator, values=None, max_value=6):
    set_scoring_scale(stage, values or DEFAULT_VALUES, max_value, actor=coordinator, free_values=True)
    stage.refresh_from_db()
    return stage


def graded(stage, problem, score, *, review_score=None):
    """Praca z oceną końcową i jedną wystawioną recenzją (stan, który blokuje zmianę skali)."""
    submission = SubmissionFactory(
        entry=StageEntryFactory(stage=stage), problem=problem, status=SubmissionStatus.GRADED_PROVISIONAL
    )
    ReviewFactory(
        submission=submission,
        status=ReviewStatus.SUBMITTED,
        score=review_score if review_score is not None else score,
        submitted_at=timezone.now(),
    )
    FinalGradeFactory(submission=submission, score=score)
    return submission


# --- stan domyślny -------------------------------------------------------------------------------


def test_nowy_etap_startuje_w_trybie_tylko_ze_skali():
    edition = EditionFactory()
    opens = timezone.now() + timedelta(days=1)
    deadline = opens + timedelta(days=30)
    created = create_stage(
        edition=edition,
        kind=StageKind.ELIM,
        opens_at=opens,
        deadline_at=deadline,
        review_deadline_at=deadline + timedelta(days=14),
        appeal_window_opens_at=deadline + timedelta(days=16),
        appeal_window_closes_at=deadline + timedelta(days=23),
    )

    assert created.scoring_scale.free_values is False
    assert score_rule(created).free is False


def test_set_scoring_scale_bez_parametru_trybu_niczego_nie_przelacza(stage, coordinator):
    make_free(stage, coordinator)

    set_scoring_scale(stage, DEFAULT_VALUES, 6, actor=coordinator)

    assert ScoringScale.objects.get(stage=stage).free_values is True


# --- reguła w trybie skali: bez zmian ------------------------------------------------------------


def test_tryb_skali_przyjmuje_tylko_wartosci_skali_takze_z_kolumny_dziesietnej(stage):
    rule = score_rule(stage)

    assert rule.clean(5) == Decimal("5")
    assert rule.clean("5,00") == Decimal("5")
    assert rule.accepts(Decimal("6.00"))
    with pytest.raises(DomainError) as exc:
        rule.clean("4,5")
    assert exc.value.machine_code == "SCORE_NOT_IN_SCALE"
    assert str(exc.value.detail) == "Ocena 4,5 nie należy do skali [0, 2, 5, 6]."


def test_tryb_skali_komunikat_dla_liczby_calkowitej_jak_dotad(stage):
    with pytest.raises(DomainError) as exc:
        score_rule(stage).clean(4)

    assert str(exc.value.detail) == "Ocena 4 nie należy do skali [0, 2, 5, 6]."


# --- reguła w trybie dowolnym --------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["4,25", "4.25", 4.25, Decimal("4.25"), 0, 6, "0,01", "5,99", "3"])
def test_tryb_dowolny_przyjmuje_kazda_liczbe_z_zakresu_co_0_01(stage, coordinator, raw):
    make_free(stage, coordinator)

    value = score_rule(stage).clean(raw)

    assert Decimal(0) <= value <= Decimal(6)
    assert value == value.quantize(Decimal("0.01"))


@pytest.mark.parametrize("raw", ["6,01", "-0,01", 7, "-1"])
def test_tryb_dowolny_odmawia_poza_zakresem(stage, coordinator, raw):
    make_free(stage, coordinator)

    with pytest.raises(DomainError) as exc:
        score_rule(stage).clean(raw)

    assert exc.value.machine_code == "SCORE_NOT_IN_SCALE"
    assert "zakres 0–6" in str(exc.value.detail)


@pytest.mark.parametrize("raw", ["4,255", 4.255, "abc", "", True])
def test_tryb_dowolny_odmawia_trzeciego_miejsca_i_nieliczb(stage, coordinator, raw):
    make_free(stage, coordinator)

    with pytest.raises(DomainError) as exc:
        score_rule(stage).clean(raw)

    assert exc.value.machine_code == "SCORE_INVALID"


def test_tryb_dowolny_z_wlasna_skala_zadania_bierze_jej_granice(stage, coordinator):
    make_free(stage, coordinator)
    problem = ProblemFactory(
        stage=stage,
        scoring_values=[{"value": 0, "label": "nie"}, {"value": 10, "label": "tak"}],
        max_points=10,
    )

    rule = score_rule(stage, problem)

    assert rule.free is True
    assert rule.clean("9,75") == Decimal("9.75")
    with pytest.raises(DomainError):
        rule.clean("10,5")


def test_zadanie_z_wlasna_skala_w_etapie_skali_zostaje_przy_liscie(stage):
    problem = ProblemFactory(
        stage=stage,
        scoring_values=[{"value": 0, "label": "nie"}, {"value": 10, "label": "tak"}],
        max_points=10,
    )

    with pytest.raises(DomainError):
        score_rule(stage, problem).clean(5)


def test_skala_ujemna_z_przesunieciem_w_trybie_dowolnym(stage, coordinator):
    """Punkty ujemne (flaga ``weighted_scoring``): zakres idzie w postaci przechowywanej 0–8."""
    enable_weights(stage)
    make_free(stage, coordinator, NEGATIVE_VALUES, 5)

    rule = score_rule(stage)

    assert rule.offset == 3
    assert (rule.minimum, rule.maximum) == (Decimal(0), Decimal(8))
    assert (rule.display_minimum, rule.display_maximum) == (Decimal(-3), Decimal(5))
    # „-1,5” recenzenta leży w bazie jako 1,5 i wraca z niej jako -1,5.
    assert rule.to_stored("-1,5") == Decimal("1.5")
    assert rule.clean(rule.to_stored("-1,5")) == Decimal("1.50")
    assert rule.to_display(Decimal("1.50")) == Decimal("-1.50")
    with pytest.raises(DomainError):
        rule.clean(Decimal("-0.5"))


# --- samo maksimum zadania -----------------------------------------------------------------------


def test_samo_maksimum_zadania_w_etapie_dowolnym(stage, coordinator):
    make_free(stage, coordinator)
    problem = ProblemFactory(stage=stage, max_points=Decimal("12.5"))
    problem.full_clean()

    rule = score_rule(stage, problem)

    assert uses_own_range(problem, free=True)
    assert (rule.minimum, rule.maximum, rule.offset) == (Decimal(0), Decimal("12.5"), 0)
    assert rule.items == ()
    assert rule.clean("12,5") == Decimal("12.50")
    with pytest.raises(DomainError):
        rule.clean("12,51")
    assert problem_maximum(stage, problem) == Decimal("12.5")


def test_samo_maksimum_odrzucone_w_etapie_tylko_ze_skali(stage):
    problem = ProblemFactory(stage=stage, max_points=Decimal("7"))

    with pytest.raises(ValidationError) as exc:
        problem.full_clean()

    assert "scoring_values" in exc.value.message_dict


def test_samo_maksimum_musi_byc_dodatnie(stage, coordinator):
    make_free(stage, coordinator)
    problem = ProblemFactory(stage=stage, max_points=Decimal("0"))

    with pytest.raises(ValidationError) as exc:
        problem.full_clean()

    assert "max_points" in exc.value.message_dict


def test_samo_maksimum_nie_dziedziczy_przesuniecia_etapu(stage, coordinator):
    enable_weights(stage)
    make_free(stage, coordinator, NEGATIVE_VALUES, 5)
    problem = ProblemFactory(stage=stage, max_points=Decimal("7"))

    assert score_rule(stage, problem).offset == 0
    assert stage_scoring(stage).offsets[problem.pk] == 0


def test_maksima_zadan_i_maksimum_sumy(stage, coordinator):
    make_free(stage, coordinator)
    first = ProblemFactory(stage=stage, number=1)
    second = ProblemFactory(stage=stage, number=2, max_points=Decimal("12.5"))

    assert problem_maxima_by_number(stage) == {"1": Decimal(6), "2": Decimal("12.5")}
    assert stage_maximum_total(stage, [first, second]) == Decimal("18.5")


def test_maksimum_sumy_nie_dla_etapu_bez_zadan(stage):
    assert stage_maximum_total(stage, []) is None


# --- przełącznik trybu ---------------------------------------------------------------------------


def test_wlaczenie_trybu_dowolnego_jest_zawsze_wolne_i_idzie_do_audytu(stage, coordinator):
    from apps.core.models import AuditLog

    problem = ProblemFactory(stage=stage)
    graded(stage, problem, 5)

    make_free(stage, coordinator)

    assert stage.scoring_scale.free_values is True
    entry = AuditLog.objects.filter(action="stage.scale_updated").latest("id")
    assert entry.diff["from"]["free_values"] is False
    assert entry.diff["to"]["free_values"] is True


def test_powrot_do_skali_odmowiony_przy_ocenach_spoza_skali_z_licznikiem(stage, coordinator):
    make_free(stage, coordinator)
    problem = ProblemFactory(stage=stage)
    graded(stage, problem, Decimal("4.25"))  # recenzja 4,25 i ocena końcowa 4,25 – dwie oceny
    graded(stage, problem, Decimal("5"))

    with pytest.raises(DomainError) as exc:
        set_scoring_scale(stage, DEFAULT_VALUES, 6, actor=coordinator, free_values=False)

    assert exc.value.machine_code == "FREE_VALUES_IN_USE"
    assert exc.value.status_code == 409
    assert "oceny spoza skali: 2" in str(exc.value.detail)
    assert ScoringScale.objects.get(stage=stage).free_values is True


def test_powrot_do_skali_odmowiony_przy_zadaniu_z_samym_maksimum(stage, coordinator):
    make_free(stage, coordinator)
    ProblemFactory(stage=stage, max_points=Decimal("12.5"))

    with pytest.raises(DomainError) as exc:
        set_scoring_scale(stage, DEFAULT_VALUES, 6, actor=coordinator, free_values=False)

    assert "zadania z samym maksimum punktów: 1" in str(exc.value.detail)


def test_powrot_do_skali_liczy_takze_reklamacje_i_skale_zadania(stage, coordinator):
    from apps.appeals.models import Appeal, AppealDecision, AppealStatus

    make_free(stage, coordinator)
    own = ProblemFactory(
        stage=stage,
        scoring_values=[{"value": 0, "label": "nie"}, {"value": 10, "label": "tak"}],
        max_points=10,
    )
    submission = graded(stage, own, Decimal("10"))
    appeal = Appeal.objects.create(
        submission=submission, filed_by=submission.entry.participant, argument="x" * 50
    )
    AppealDecision.objects.create(appeal=appeal, new_score=Decimal("7.5"), justification="uzasadnienie")
    appeal.status = AppealStatus.PARTIALLY_ACCEPTED
    appeal.save(update_fields=["status"])

    with pytest.raises(DomainError) as exc:
        set_scoring_scale(stage, DEFAULT_VALUES, 6, actor=coordinator, free_values=False)

    assert "oceny spoza skali: 1" in str(exc.value.detail)


def test_powrot_do_skali_przechodzi_gdy_oceny_sa_ze_skali(stage, coordinator):
    make_free(stage, coordinator)
    problem = ProblemFactory(stage=stage)
    graded(stage, problem, Decimal("5.00"))

    set_scoring_scale(stage, DEFAULT_VALUES, 6, actor=coordinator, free_values=False)

    assert ScoringScale.objects.get(stage=stage).free_values is False


def test_powrot_do_skali_z_dopisana_wartoscia_w_tym_samym_zapisie(stage, coordinator):
    """Ocena 4 przestaje blokować powrót, gdy ten sam zapis dopisuje 4 do skali."""
    make_free(stage, coordinator)
    problem = ProblemFactory(stage=stage)
    graded(stage, problem, Decimal("4"))

    set_scoring_scale(
        stage,
        [*DEFAULT_VALUES[:2], {"value": 4, "label": "cztery"}, *DEFAULT_VALUES[2:]],
        6,
        actor=coordinator,
        free_values=False,
    )

    assert ScoringScale.objects.get(stage=stage).free_values is False


def test_w_trybie_dowolnym_wolno_zdjac_wartosc_ale_nie_obnizyc_maksimum(stage, coordinator):
    make_free(stage, coordinator)
    problem = ProblemFactory(stage=stage)
    graded(stage, problem, Decimal("5"))

    # Wartość 5 znika ze skali, ale ocena 5 dalej mieści się w 0–6.
    set_scoring_scale(stage, [DEFAULT_VALUES[0], DEFAULT_VALUES[3]], 6, actor=coordinator)
    with pytest.raises(DomainError) as exc:
        set_scoring_scale(stage, DEFAULT_VALUES[:2], 2, actor=coordinator)

    assert exc.value.machine_code == "SCALE_LOCKED"
    assert "5" in str(exc.value.detail)


def test_scores_in_use_zwraca_decimal_i_pomija_zadania_z_samym_maksimum(stage, coordinator):
    make_free(stage, coordinator)
    inheriting = ProblemFactory(stage=stage)
    max_only = ProblemFactory(stage=stage, max_points=Decimal("12.5"))
    graded(stage, inheriting, Decimal("4.25"))
    graded(stage, max_only, Decimal("11.75"))

    assert scores_in_use(stage) == {Decimal("4.25")}
    assert scores_in_use(stage, problem=max_only) == {Decimal("11.75")}


def test_score_rule_jest_niezmienny():
    rule = ScoreRule(free=True, minimum=Decimal(0), maximum=Decimal(6))
    with pytest.raises(AttributeError):
        rule.free = False  # type: ignore[misc]
