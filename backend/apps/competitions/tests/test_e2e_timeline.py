"""``manage.py e2e_timeline`` – przesuwanie osi czasu etapu na potrzeby scenariusza E2E (T-10).

Sedno tych testów to bezpiecznik: bez ``E2E_MODE`` komenda musi odmówić. Reszta pilnuje, że
przesunięta oś czasu naprawdę oznacza to, co mówi nazwa fazy – sprawdzane predykatami modelu
(``is_open_for_submissions``, ``is_appeal_window_open``), a nie porównaniem samych liczb.
"""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.competitions.management.commands.e2e_timeline import (
    PHASE_APPEALS_CLOSED,
    PHASE_APPEALS_OPEN,
    PHASE_CLOSED,
)
from apps.competitions.models import Stage
from apps.competitions.tests.factories import StageFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def stage():
    return StageFactory()


def test_bez_e2e_mode_komenda_odmawia(settings, stage):
    """Bezpiecznik: w normalnej instalacji przesunięcie deadline'u jest niedostępne z powłoki."""
    settings.E2E_MODE = False
    before = Stage.objects.get(pk=stage.pk).deadline_at

    with pytest.raises(CommandError, match="E2E_MODE"):
        call_command("e2e_timeline", "--stage", str(stage.pk), "--phase", PHASE_CLOSED)

    assert Stage.objects.get(pk=stage.pk).deadline_at == before


def test_faza_closed_zamyka_upload_i_trzyma_okno_reklamacji_zamkniete(settings, stage):
    settings.E2E_MODE = True

    call_command("e2e_timeline", "--stage", str(stage.pk), "--phase", PHASE_CLOSED)

    stage.refresh_from_db()
    now = timezone.now()
    assert stage.has_opened(now)
    assert not stage.is_open_for_submissions(now)
    assert not stage.is_appeal_window_open(now)
    # Tolerancja po deadline nie może „odmrozić” uploadu po przesunięciu osi czasu.
    assert stage.grace_seconds == 0


def test_faza_appeals_open_otwiera_okno_reklamacji(settings, stage):
    settings.E2E_MODE = True

    call_command("e2e_timeline", "--stage", str(stage.pk), "--phase", PHASE_APPEALS_OPEN)

    stage.refresh_from_db()
    now = timezone.now()
    assert not stage.is_open_for_submissions(now)
    assert stage.is_appeal_window_open(now)


def test_faza_appeals_closed_zamyka_okno_reklamacji(settings, stage):
    settings.E2E_MODE = True

    call_command("e2e_timeline", "--stage", str(stage.pk), "--phase", PHASE_APPEALS_CLOSED)

    stage.refresh_from_db()
    now = timezone.now()
    assert not stage.is_appeal_window_open(now)
    assert stage.appeal_window_closes_at < now


@pytest.mark.parametrize("phase", [PHASE_CLOSED, PHASE_APPEALS_OPEN, PHASE_APPEALS_CLOSED])
def test_kazda_faza_zachowuje_porzadek_wymagany_przez_constrainty(settings, stage, phase):
    """``full_clean`` w komendzie i ``CheckConstraint`` w bazie mówią to samo – tu sprawdzamy skutek."""
    settings.E2E_MODE = True

    call_command("e2e_timeline", "--stage", str(stage.pk), "--phase", phase)

    stage.refresh_from_db()
    assert stage.opens_at < stage.deadline_at
    assert stage.deadline_at <= stage.review_deadline_at
    assert stage.review_deadline_at <= stage.appeal_window_opens_at
    assert stage.appeal_window_opens_at < stage.appeal_window_closes_at


def test_nieistniejacy_etap_konczy_sie_bledem_komendy(settings):
    settings.E2E_MODE = True

    with pytest.raises(CommandError, match="Nie ma etapu"):
        call_command("e2e_timeline", "--stage", "999999", "--phase", PHASE_CLOSED)
