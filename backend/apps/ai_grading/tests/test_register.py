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


def test_register_version_was_bumped_for_the_new_processing():
    # 1.9 (25.09.2026): powiadomienia z forum dołożyły odbiorcę w wierszu forum – wersja poszła dalej.
    assert REGISTER_VERSION == "1.9"
