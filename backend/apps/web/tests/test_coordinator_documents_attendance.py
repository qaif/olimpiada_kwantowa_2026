"""Skutek ekranu „Szablony dokumentów” na liście obecności (szew T48/T12 w ``apps.integrations``).

Tytuł listy obecności był stałą w kodzie (``LOGISTICS_LIST_TITLES``) i od etapu 2 jest jej
odwrotem: przy wyłączonej fladze ``document_templates`` wychodzi **dokładnie** dzisiejszy napis,
a przy włączonej – tekst szablonu rodzaju ``ATTENDANCE_LIST``, opublikowanego na ekranie T13.

Dwa zdania, których ten plik pilnuje, są nie do sprawdzenia w ``apps/integrations/tests`` bez
wiedzy o ekranie, i nie do sprawdzenia w ``test_coordinator_documents.py`` bez wiedzy o listach:

- publikacja tekstu **zmienia to, co wychodzi na papierze** – inaczej ekran obiecywałby zmianę,
  której nikt nie zobaczy,
- lista noclegowa i żywieniowa **nie** biorą tytułu z tego szablonu: są wydrukami roboczymi dla
  hotelu i dla kuchni, a jeden wspólny szablon dałby trzy wydruki „Lista obecności”.
"""

from __future__ import annotations

import pytest

from apps.integrations.exports import (
    LIST_ACCOMMODATION,
    LIST_ATTENDANCE,
    LIST_MEAL,
    LOGISTICS_LIST_TITLES,
    logistics_list_title,
)
from apps.tenancy.documents import DOCUMENTS_FLAG, DocumentKind, set_current_template

pytestmark = pytest.mark.django_db


def enable(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), DOCUMENTS_FLAG: True}
    competition.save(update_fields=["feature_flags"])
    return competition


def publish(competition, title: str):
    return set_current_template(
        competition,
        DocumentKind.ATTENDANCE_LIST,
        version="1.0",
        title=title,
        statement="Podpis potwierdza obecność na sali.",
    )


def test_without_the_flag_the_title_is_the_constant(competition, elim_stage):
    """Olimpiada Kwantowa po wdrożeniu: ten sam napis, co przed nim, co do znaku."""
    for kind, expected in LOGISTICS_LIST_TITLES.items():
        assert logistics_list_title(elim_stage, kind) == expected


def test_a_template_is_ignored_while_the_flag_is_off(competition, elim_stage):
    """Wiersz w bazie nie jest włącznikiem – włącznikiem jest flaga konkursu."""
    publish(competition, "Lista obecności konkursu")

    assert logistics_list_title(elim_stage, LIST_ATTENDANCE) == LOGISTICS_LIST_TITLES[LIST_ATTENDANCE]


def test_the_published_text_reaches_the_attendance_list(competition, elim_stage):
    enable(competition)
    publish(competition, "Lista obecności – {stage}")

    assert logistics_list_title(elim_stage, LIST_ATTENDANCE) == (
        f"Lista obecności – {elim_stage.display_name}"
    )


def test_the_working_lists_keep_their_own_titles(competition, elim_stage):
    """Szablon ``ATTENDANCE_LIST`` opisuje jeden dokument – ten, który podpisuje uczestnik."""
    enable(competition)
    publish(competition, "Lista obecności konkursu")

    assert logistics_list_title(elim_stage, LIST_ACCOMMODATION) == LOGISTICS_LIST_TITLES[LIST_ACCOMMODATION]
    assert logistics_list_title(elim_stage, LIST_MEAL) == LOGISTICS_LIST_TITLES[LIST_MEAL]


def test_an_unknown_kind_of_list_is_still_an_error(elim_stage):
    """Odwrót nie może zamienić literówki w cichy pusty tytuł – to jest błąd wołającego."""
    with pytest.raises(KeyError):
        logistics_list_title(elim_stage, "grafik")
