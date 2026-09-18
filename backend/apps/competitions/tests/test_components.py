"""Komponenty etapu jako model: więzy, waga jako ułamek i zakresowanie (§ 1.2.3).

Przedmiotem tego pliku jest **wiersz**, a nie suma punktów: arytmetykę sumowania po komponentach
sprawdza ``apps/results/tests/test_components.py``, bo tam mieszka jej kod. Tutaj pilnujemy tego,
czego nie widać w wyniku: że mianownik zerowy nie wejdzie do bazy, że dwa komponenty nie staną na
tym samym miejscu i że komponent należy do konkursu swojego etapu.

Konkurs #1 nie ma ani jednego komponentu i mieć nie musi – migracja ``0029_stage_component``
tworzy pustą tabelę i na tym kończy. Etap bez komponentów czyta ``Stage.format`` tak, jak czytał
przed etapem 2, i to jest pierwsza asercja tego pliku.
"""

from __future__ import annotations

from fractions import Fraction

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.competitions.models import ComponentKind, StageComponent

from .factories import StageFactory

pytestmark = pytest.mark.django_db


def make_component(stage, kind=ComponentKind.SUBMISSIONS, **kwargs) -> StageComponent:
    """Komponent bez fabryki: ``tests/factories.py`` jest plikiem wspólnym kilku zadań naraz."""
    return StageComponent.objects.create(stage=stage, kind=kind, **kwargs)


# --- Konkurs #1: pusta tabela ---------------------------------------------------------------------


def test_competition_one_has_no_components(competition):
    """Migracja tworzy **pustą** tabelę – żaden istniejący etap nie dostaje ani jednego wiersza."""
    assert not StageComponent.objects.for_competition(competition).exists()


# --- waga jako ułamek zwykły ----------------------------------------------------------------------


def test_the_weight_is_an_exact_fraction(competition):
    """Ułamek zwykły, a nie ``float``: ``1/3`` zapisane jako 0,333… dawałoby sumę zależną od kolejności."""
    component = make_component(StageFactory(), weight_numerator=1, weight_denominator=3)

    assert component.weight == Fraction(1, 3)
    assert component.weight * 3 == 1


def test_the_default_weight_is_neutral(competition):
    """Domyślne ``1/1`` znaczy „bez wagi” – i daje liczbę identyczną z dzisiejszym sumowaniem."""
    assert make_component(StageFactory()).weight == Fraction(1, 1)


def test_a_zero_denominator_is_refused_by_the_database(competition):
    """Mianownik zerowy nie jest wagą neutralną, tylko wywróconym przeliczeniem całego etapu."""
    stage = StageFactory()

    with (
        pytest.raises(IntegrityError, match="competitions_stagecomponent_denominator_positive"),
        transaction.atomic(),
    ):
        make_component(stage, weight_denominator=0)


def test_a_zero_denominator_is_refused_with_a_readable_message(competition):
    component = StageComponent(stage=StageFactory(), kind=ComponentKind.QUIZ, weight_denominator=0)

    with pytest.raises(ValidationError) as error:
        component.full_clean()

    assert "weight_denominator" in error.value.message_dict


# --- więzy i porządek -----------------------------------------------------------------------------


def test_two_components_cannot_share_a_place_in_one_stage(competition):
    """Dwa razy ten sam rodzaj na tym samym miejscu to dwie kolumny bez porządku między nimi."""
    stage = StageFactory()
    make_component(stage, ComponentKind.QUIZ, position=1)

    with pytest.raises(IntegrityError, match="competitions_stagecomponent_unique"), transaction.atomic():
        make_component(stage, ComponentKind.QUIZ, position=1)


def test_the_same_kind_twice_is_allowed_on_different_places(competition):
    """„Test wstępny” i „test finałowy” w jednym etapie są sensowne – rozróżnia je kolejność."""
    stage = StageFactory()
    make_component(stage, ComponentKind.QUIZ, position=1, name="Test wstępny")
    make_component(stage, ComponentKind.QUIZ, position=2, name="Test finałowy")

    assert [component.name for component in stage.components.all()] == ["Test wstępny", "Test finałowy"]


def test_a_component_is_never_left_without_a_label(competition):
    """Puste ``name`` znaczy „nazwą jest etykieta rodzaju”, a nie „komponent bez podpisu”."""
    stage = StageFactory()

    assert make_component(stage, ComponentKind.INTERVIEW).display_name == "rozmowa"
    assert make_component(stage, ComponentKind.INTERVIEW, position=2, name="Obrona").display_name == "Obrona"


def test_components_disappear_with_their_stage(competition):
    """``CASCADE``: komponent bez etapu nie jest wierszem do uratowania, tylko śmieciem."""
    stage = StageFactory()
    make_component(stage)
    stage.delete()

    assert not StageComponent.objects.exists()


# --- izolacja (§ 5.7) -----------------------------------------------------------------------------


def test_a_component_belongs_to_the_competition_of_its_stage(competition, other_competition):
    """Droga do konkursu wiedzie etapem i edycją – własnej kolumny komponent nie ma i mieć nie ma po co."""
    mine = make_component(StageFactory(competition=competition))
    theirs = make_component(StageFactory(competition=other_competition))

    assert list(StageComponent.objects.for_competition(competition)) == [mine]
    assert list(StageComponent.objects.for_competition(other_competition)) == [theirs]
