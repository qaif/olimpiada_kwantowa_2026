"""Nazwy etapu i edycji są w PDF-ach tekstem, a nie znacznikami ReportLaba (audyt 10.10.2026, niskie).

``Paragraph`` czyta treść jak mini-HTML. Etap nazwany „Finał <b” albo edycja „2026 & 2027”
wywracały pobranie protokołu i list logistycznych pięćsetką, a ``<img src="/ścieżka">`` kazał
otworzyć plik z dysku serwera.
"""

from __future__ import annotations

import pytest

from apps.integrations.exports import LIST_ATTENDANCE, LIST_MEAL, render_logistics_list, render_stage_protocol
from apps.results.tests.conftest import make_stage

pytestmark = pytest.mark.django_db

MARKUP_NAME = 'Finał <b & <img src="/etc/hostname" width="5" height="5"/>'


@pytest.fixture
def paragraphs(monkeypatch):
    from reportlab.platypus import Paragraph

    seen: list[str] = []
    original_init = Paragraph.__init__

    def spy(self, text, *args, **kwargs):
        seen.append(text)
        original_init(self, text, *args, **kwargs)

    monkeypatch.setattr(Paragraph, "__init__", spy)
    return seen


@pytest.fixture
def markup_stage(edition):
    edition.year_label = "2026 < 2027 & co"
    edition.save(update_fields=["year_label"])
    stage = make_stage(edition=edition, min_points=6)
    stage.name = MARKUP_NAME
    stage.save(update_fields=["name"])
    return stage


def test_the_stage_protocol_escapes_the_stage_and_edition_names(markup_stage, paragraphs):
    pdf = render_stage_protocol(markup_stage)

    assert pdf.startswith(b"%PDF")
    composed = " ".join(paragraphs)
    assert "<img" not in composed
    assert "<b " not in composed
    assert "Finał &lt;b &amp; &lt;img" in composed
    assert "2026 &lt; 2027 &amp; co" in composed


@pytest.mark.parametrize("kind", [LIST_ATTENDANCE, LIST_MEAL])
def test_the_logistics_lists_escape_the_stage_and_edition_names(markup_stage, paragraphs, kind):
    pdf = render_logistics_list(markup_stage, kind)

    assert pdf.startswith(b"%PDF")
    composed = " ".join(paragraphs)
    assert "<img" not in composed
    assert "2026 &lt; 2027 &amp; co" in composed
