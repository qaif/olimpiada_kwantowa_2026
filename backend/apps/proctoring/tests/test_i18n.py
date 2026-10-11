"""Napisy nadzoru w katalogu aplikacji (``apps/proctoring/locale``) – działają we wszystkich 10 językach."""

from __future__ import annotations

import pytest
from django.conf import settings
from django.utils import translation

from apps.proctoring import services
from apps.web.views.proctoring import console_strings, grid_strings, incident_categories

LANGUAGES = [code for code, _label in settings.LANGUAGES if code != "pl"]


@pytest.mark.parametrize("language", LANGUAGES)
def test_console_and_grid_strings_are_translated(language):
    with translation.override("pl"):
        polish = (str(services.CONSENT_STATEMENT), console_strings()["live"], grid_strings()["incident"])
        polish_categories = dict(incident_categories())
    with translation.override(language):
        translated = (str(services.CONSENT_STATEMENT), console_strings()["live"], grid_strings()["incident"])
        categories = dict(incident_categories())
    assert all(a != b for a, b in zip(polish, translated, strict=True)), language
    assert categories["other_person"] != polish_categories["other_person"]


def test_placeholders_survive_translation():
    with translation.override("en"):
        assert "Ala" in services._("uczniowie: %(name)s") % {"name": "Ala"}
        assert "%(page)s" in grid_strings()["page"]
