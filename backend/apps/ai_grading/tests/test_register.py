"""Rejestr czynności przetwarzania a ocena AI: wiersz warunkowy, z odbiorcą i przekazaniem poza EOG."""

from __future__ import annotations

import pytest

from apps.accounts.processing_register import (
    AI_GRADING_ACTIVITY,
    REGISTER_VERSION,
    activities_for,
    ai_grading_activity,
)

from .conftest import enable_ai, with_key

pytestmark = pytest.mark.django_db


def test_competition_without_ai_grading_has_no_ai_row(competition):
    assert "ocena_ai" not in {activity.key for activity in activities_for(competition)}


def test_competition_with_ai_grading_gets_the_row(competition):
    enable_ai(competition)

    assert "ocena_ai" in {activity.key for activity in activities_for(competition)}


def test_the_row_names_anthropic_as_processor_and_the_transfer(competition):
    enable_ai(competition)
    with_key(competition)
    recipients = " ".join(ai_grading_activity(competition).recipients)

    assert "Anthropic" in recipients
    assert "podmiot przetwarzający" in recipients
    assert "państwa trzeciego" in recipients
    assert "art. 22" in AI_GRADING_ACTIVITY.legal_basis
    for element in ("purpose", "legal_basis", "subjects", "retention"):
        assert getattr(AI_GRADING_ACTIVITY, element)
    assert AI_GRADING_ACTIVITY.categories and AI_GRADING_ACTIVITY.measures


#: Wersja rejestru, w której weszła ocena AI (``apps.accounts.processing_register``, 1.7 z 24.09.2026).
AI_GRADING_REGISTER_VERSION = (1, 7)


def test_register_version_was_bumped_for_the_new_processing():
    """Rejestr z wierszem oceny AI ma wersję **co najmniej** tę, w której ten wiersz wszedł.

    Porównanie „nie mniejsza niż”, a nie równość: do 25.09.2026 stało tu ``== "1.9"``, więc każda
    kolejna materialna zmiana rejestru (forum, plakaty, …) musiała poprawiać test oceny AI, który
    z nią nie miał nic wspólnego. Reguła tego testu brzmi „ocena AI podbiła wersję” – i tyle
    sprawdza; to, że wersja rośnie przy każdej zmianie, jest regułą rejestru, nie tego pliku.
    """
    assert tuple(int(part) for part in REGISTER_VERSION.split(".")) >= AI_GRADING_REGISTER_VERSION
