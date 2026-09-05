"""Fixture'y testów rozwiązań.

Klient clamd jest zamockowany autouse: żaden test nie może przypadkiem otworzyć gniazda TCP
do prawdziwego ClamAV (testy muszą działać także bez uruchomionego compose).
"""

from types import SimpleNamespace

import pytest

from apps.submissions.antivirus import VERDICT_CLEAN


@pytest.fixture(autouse=True)
def clamd(monkeypatch):
    """Podmienia ``scan_stream`` używany w zadaniu Celery. ``clamd.verdict`` steruje werdyktem."""
    state = SimpleNamespace(verdict=(VERDICT_CLEAN, ""), scanned=[], error=None)

    def _scan_stream(fileobj, **kwargs):
        state.scanned.append(fileobj.read())
        if state.error is not None:
            raise state.error
        return state.verdict

    monkeypatch.setattr("apps.submissions.tasks.scan_stream", _scan_stream)
    return state
