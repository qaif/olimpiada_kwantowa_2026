"""Wagi zadań, przesunięcie skali i punkty ujemne (``docs/UNIWERSALNY-ETAP-2.md`` § 1.2.6).

Cztery rzeczy, których pilnuje ten plik, w kolejności wagi:

- **Konkurs #1 nie zmienia ani jednej sumy.** Bez flagi ``weighted_scoring`` skala nie przyjmuje
  punktu ujemnego (i odpada z dzisiejszym komunikatem), przesunięcie zostaje zerem, a suma etapu
  jest dosłownie dzisiejszym sumowaniem ``int``. Co więcej – **także z włączoną flagą** waga
  ``1/1`` i przesunięcie ``0`` dają tę samą liczbę, bo tylko to pozwala włączyć funkcję bez
  przeliczania ogłoszonych tabel (§ 5.2).
- **Ułamek zwykły, nie ``float``.** Trzy zadania po ``1/3`` dają sumę równą wynikowi jednego
  zadania z wagą ``1/1``, a nie „prawie równą”. To jest cały powód, dla którego waga jest parą
  liczb całkowitych.
- **Przesunięcie jest wyliczane, nigdy wpisywane.** Skala i przesunięcie nie mogą się rozjechać,
  bo rozjazd znaczy ocenę zapisaną w innej skali niż odczytywaną. Zmiana przesunięcia po
  wystawieniu pierwszej oceny jest odmową.
- **Kolumny wyników zostają nieujemne** (decyzja organizatora D10): ocena leży w bazie
  przesunięta, a suma etapu nie schodzi poniżej zera.
"""

from __future__ import annotations

from fractions import Fraction

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.accounts.tests.factories import CoordinatorFactory
from apps.competitions.models import (
    Problem,
    ScoringScale,
    required_scale_offset,
    validate_scoring_values,
)
from apps.competitions.services import (
    PLAIN_SCORING,
    set_scoring_scale,
    stage_scoring,
    weighted_scoring_enabled,
)
from apps.core.api import DomainError

from .factories import ProblemFactory, ScoringScaleFactory, StageFactory

pytestmark = pytest.mark.django_db

FEATURE = "weighted_scoring"

#: Skala z punktem ujemnym: „minus jeden” za odpowiedź błędną. Najniższa wartość to ``-3``, więc
#: przesunięcie takiej skali wynosi ``3``.
NEGATIVE_VALUES = [
    {"value": -3, "label": "odpowiedź błędna"},
    {"value": 0, "label": "brak odpowiedzi"},
    {"value": 5, "label": "odpowiedź poprawna"},
]


def enable_weights(competition):
    """Włącza flagę obszaru tą samą drogą, którą zrobi to wdrożenie: wartością w bazie."""
    competition.feature_flags = {**(competition.feature_flags or {}), FEATURE: True}
    competition.save(update_fields=["feature_flags"])
    return competition


@pytest.fixture
def coordinator():
    return CoordinatorFactory()


@pytest.fixture
def stage(competition):
    return StageFactory(competition=competition)


@pytest.fixture
def scaled_stage(stage):
    ScoringScaleFactory(stage=stage)
    return stage


# --- waga zadania ------------------------------------------------------------------------------


def test_zadanie_bez_wagi_wazy_jeden(competition):
    problem = ProblemFactory(competition=competition)

    assert (problem.weight_numerator, problem.weight_denominator) == (1, 1)
    assert problem.weight == Fraction(1)


def test_waga_jest_ulamkiem_zwyklym(competition):
    problem = ProblemFactory(competition=competition, weight_numerator=1, weight_denominator=3)

    assert problem.weight == Fraction(1, 3)


def test_zerowy_mianownik_odpada_w_walidacji(competition):
    problem = ProblemFactory(competition=competition)
    problem.weight_denominator = 0

    with pytest.raises(ValidationError) as exc:
        problem.full_clean()

    assert "weight_denominator" in exc.value.message_dict


def test_zerowy_mianownik_odpada_takze_w_bazie(competition):
    """Więz, a nie sam ``clean()`` – dzielenie przez zero w przeliczeniu wyników byłoby błędem 500."""
    problem = ProblemFactory(competition=competition)

    with pytest.raises(IntegrityError), transaction.atomic():
        Problem.objects.filter(pk=problem.pk).update(weight_denominator=0)


# --- przesunięcie skali ------------------------------------------------------------------------


def test_skala_bez_ujemnych_nie_wymaga_przesuniecia():
    assert required_scale_offset([{"value": 0, "label": "x"}, {"value": 6, "label": "y"}]) == 0


def test_przesuniecie_sprowadza_najnizsza_wartosc_do_zera():
    assert required_scale_offset(NEGATIVE_VALUES) == 3


def test_walidacja_bez_zgody_na_ujemne_mowi_dokladnie_to_co_dzis():
    with pytest.raises(ValidationError) as exc:
        validate_scoring_values(NEGATIVE_VALUES, 5, values_field="values", max_field="max_value")

    assert exc.value.message_dict["values"] == ["Pole 'value' musi być nieujemną liczbą całkowitą."]


def test_walidacja_ze_zgoda_na_ujemne_przepuszcza_minus():
    validate_scoring_values(
        NEGATIVE_VALUES, 5, values_field="values", max_field="max_value", allow_negative=True
    )


def test_wymog_zera_zostaje_takze_przy_punktach_ujemnych():
    values = [{"value": -3, "label": "błąd"}, {"value": 5, "label": "poprawnie"}]

    with pytest.raises(ValidationError) as exc:
        validate_scoring_values(values, 5, values_field="values", max_field="max_value", allow_negative=True)

    assert exc.value.message_dict["values"] == ["Skala musi zawierać wartość 0."]


def test_maksimum_nadal_rowna_sie_najwiekszej_wartosci():
    with pytest.raises(ValidationError) as exc:
        validate_scoring_values(
            NEGATIVE_VALUES, 8, values_field="values", max_field="max_value", allow_negative=True
        )

    assert "max_value" in exc.value.message_dict


def test_przesuniecie_niezgodne_ze_skala_odpada(stage):
    scale = ScoringScale(stage=stage, values=NEGATIVE_VALUES, max_value=5, offset=1)

    with pytest.raises(ValidationError) as exc:
        scale.full_clean()

    assert "offset" in exc.value.message_dict


def test_skala_przechowuje_oceny_przesuniete(stage):
    scale = ScoringScale(stage=stage, values=NEGATIVE_VALUES, max_value=5, offset=3)
    scale.full_clean()

    assert scale.allowed_values() == {-3, 0, 5}
    assert scale.stored_allowed_values() == {0, 3, 8}


# --- zapis skali przez koordynatora -------------------------------------------------------------


def test_bez_flagi_punkty_ujemne_odpadaja(scaled_stage, coordinator):
    with pytest.raises(DomainError) as exc:
        set_scoring_scale(scaled_stage, NEGATIVE_VALUES, 5, actor=coordinator)

    assert exc.value.machine_code == "SCORING_SCALE_INVALID"
    assert "nieujemną" in str(exc.value.detail)


def test_z_flaga_skala_dostaje_przesuniecie(scaled_stage, competition, coordinator):
    enable_weights(competition)

    scale = set_scoring_scale(scaled_stage, NEGATIVE_VALUES, 5, actor=coordinator)

    assert scale.offset == 3
    assert scale.values == NEGATIVE_VALUES
    assert scale.stored_allowed_values() == {0, 3, 8}


def test_z_flaga_skala_bez_ujemnych_zostaje_bez_przesuniecia(scaled_stage, competition, coordinator):
    enable_weights(competition)

    scale = set_scoring_scale(
        scaled_stage, [{"value": 0, "label": "nie"}, {"value": 1, "label": "tak"}], 1, actor=coordinator
    )

    assert scale.offset == 0


def test_zmiana_przesuniecia_po_wystawieniu_oceny_jest_odmowa(
    scaled_stage, competition, coordinator, monkeypatch
):
    """Bramka osobna od ``SCALE_LOCKED``: tam chodzi o zdjętą wartość, tu o zmianę znaczenia liczb."""
    enable_weights(competition)
    monkeypatch.setattr("apps.competitions.services.scores_in_use", lambda *a, **kw: {0, 5})

    with pytest.raises(DomainError) as exc:
        set_scoring_scale(scaled_stage, NEGATIVE_VALUES, 5, actor=coordinator)

    assert exc.value.machine_code == "SCALE_OFFSET_LOCKED"


# --- suma etapu --------------------------------------------------------------------------------


def test_flaga_jest_domyslnie_wylaczona(competition):
    assert weighted_scoring_enabled(competition) is False
    assert weighted_scoring_enabled(None) is False


def test_bez_flagi_punktacja_jest_stala_bez_zapytan(scaled_stage, competition, django_assert_num_queries):
    with django_assert_num_queries(0):
        scoring = stage_scoring(scaled_stage, competition=competition)

    assert scoring is PLAIN_SCORING


def test_bez_flagi_suma_jest_dzisiejszym_sumowaniem(scaled_stage, competition):
    scoring = stage_scoring(scaled_stage, competition=competition)

    assert scoring.total({1: 2, 2: 5, 3: 6}) == 13
    assert scoring.score(1, 6) == 6


def test_waga_jeden_na_jeden_daje_te_sama_liczbe_co_suma_int(scaled_stage, competition, coordinator):
    """``test_weight_one_over_one_equals_integer_sum`` z § 5.2 – po stronie serwisu punktacji."""
    enable_weights(competition)
    problems = [ProblemFactory(competition=competition, stage=scaled_stage) for _ in range(3)]
    scores = dict(zip((problem.pk for problem in problems), (2, 5, 6), strict=True))

    scoring = stage_scoring(scaled_stage, competition=competition, problems=problems)

    assert scoring.total(scores) == sum(scores.values()) == 13


def test_przesuniecie_zero_niczego_nie_zmienia(scaled_stage, competition):
    """``test_scoring_offset_zero_changes_nothing`` z § 5.2."""
    enable_weights(competition)
    problem = ProblemFactory(competition=competition, stage=scaled_stage)

    scoring = stage_scoring(scaled_stage, competition=competition, problems=[problem])

    assert scoring.score(problem.pk, 6) == 6
    assert scoring.total({problem.pk: 6}) == 6


def test_wagi_ulamkowe_nie_zaleza_od_kolejnosci_dodawania(scaled_stage, competition):
    """Trzy trzecie to jedna całość – przy ``float`` byłoby „0,999…”, czyli 1 albo 0 po zaokrągleniu."""
    enable_weights(competition)
    problems = [
        ProblemFactory(competition=competition, stage=scaled_stage, weight_denominator=3) for _ in range(3)
    ]

    scoring = stage_scoring(scaled_stage, competition=competition, problems=problems)

    assert scoring.total({problem.pk: 5 for problem in problems}) == 5


def test_zaokraglenie_zapada_raz_na_koncu_w_gore(scaled_stage, competition):
    enable_weights(competition)
    problem = ProblemFactory(
        competition=competition, stage=scaled_stage, weight_numerator=1, weight_denominator=2
    )

    scoring = stage_scoring(scaled_stage, competition=competition, problems=[problem])

    # 5/2 = 2,5 → 3 (ROUND_HALF_UP), a nie 2 (bankierskie zaokrąglenie ``round``).
    assert scoring.total({problem.pk: 5}) == 3


def test_zaokraglenie_nie_zapada_po_kazdym_zadaniu(scaled_stage, competition):
    enable_weights(competition)
    problems = [
        ProblemFactory(competition=competition, stage=scaled_stage, weight_numerator=1, weight_denominator=2)
        for _ in range(2)
    ]

    scoring = stage_scoring(scaled_stage, competition=competition, problems=problems)

    # 2,5 + 2,5 = 5. Zaokrąglenie po każdym zadaniu dałoby 6.
    assert scoring.total({problem.pk: 5 for problem in problems}) == 5


def test_waga_zero_wyjmuje_zadanie_z_sumy(scaled_stage, competition):
    enable_weights(competition)
    counted = ProblemFactory(competition=competition, stage=scaled_stage)
    training = ProblemFactory(competition=competition, stage=scaled_stage, weight_numerator=0)

    scoring = stage_scoring(scaled_stage, competition=competition, problems=[counted, training])

    assert scoring.total({counted.pk: 5, training.pk: 6}) == 5


def test_suma_liczy_sie_po_odjeciu_przesuniecia(stage, competition, coordinator):
    enable_weights(competition)
    ScoringScaleFactory(stage=stage)
    set_scoring_scale(stage, NEGATIVE_VALUES, 5, actor=coordinator)
    stage.refresh_from_db()
    problems = [ProblemFactory(competition=competition, stage=stage) for _ in range(2)]

    scoring = stage_scoring(stage, competition=competition, problems=problems)

    # W bazie leżą 8 i 3, czyli „5” i „0” – suma to 5, a nie 11.
    assert scoring.score(problems[0].pk, 8) == 5
    assert scoring.total({problems[0].pk: 8, problems[1].pk: 3}) == 5


def test_waga_mnozy_ocene_dopiero_po_odjeciu_przesuniecia(stage, competition, coordinator):
    """Kolejność działań jest częścią wyniku: ``(8-3)/2`` to 2,5 → 3, a ``8/2-3`` to 1."""
    enable_weights(competition)
    ScoringScaleFactory(stage=stage)
    set_scoring_scale(stage, NEGATIVE_VALUES, 5, actor=coordinator)
    stage.refresh_from_db()
    problem = ProblemFactory(competition=competition, stage=stage, weight_numerator=1, weight_denominator=2)

    scoring = stage_scoring(stage, competition=competition, problems=[problem])

    assert scoring.total({problem.pk: 8}) == 3


def test_suma_nie_schodzi_ponizej_zera(stage, competition, coordinator):
    """``StageEntry.total_points`` zostaje ``PositiveIntegerField`` (decyzja D10)."""
    enable_weights(competition)
    ScoringScaleFactory(stage=stage)
    set_scoring_scale(stage, NEGATIVE_VALUES, 5, actor=coordinator)
    stage.refresh_from_db()
    problems = [ProblemFactory(competition=competition, stage=stage) for _ in range(2)]

    scoring = stage_scoring(stage, competition=competition, problems=problems)

    assert scoring.total({problem.pk: 0 for problem in problems}) == 0


def test_zadanie_z_wlasna_skala_nie_podlega_przesunieciu_etapu(stage, competition, coordinator):
    enable_weights(competition)
    ScoringScaleFactory(stage=stage)
    set_scoring_scale(stage, NEGATIVE_VALUES, 5, actor=coordinator)
    stage.refresh_from_db()
    own = ProblemFactory(
        competition=competition,
        stage=stage,
        scoring_values=[{"value": 0, "label": "nie"}, {"value": 2, "label": "tak"}],
        max_points=2,
    )
    inherited = ProblemFactory(competition=competition, stage=stage)

    scoring = stage_scoring(stage, competition=competition, problems=[own, inherited])

    assert scoring.score(own.pk, 2) == 2
    assert scoring.score(inherited.pk, 3) == 0
