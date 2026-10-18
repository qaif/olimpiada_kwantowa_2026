"""Zapis do etapu treningowego z ekranu etapu koordynatora (REG-UX-01 § 3.3).

Dwie akcje POST obok osi czasu etapu (``/coordinator/stages/<id>/edit/``): przełącznik
automatycznego zapisu nowych kont i „Zapisz wszystkich obecnych uczestników”. Osobny moduł, bo to
inny rodzaj ekranu niż formularz terminów – akcja → serwis → komunikat → powrót na ekran etapu
(``ActionViewMixin``). Reguła mieszka w ``apps.competitions.auto_registration``; etap cudzego
konkursu daje 404 przez ``_stage_for_edit``, tak jak pozostałe ekrany etapu.
"""

from __future__ import annotations

from django.urls import reverse
from django.views.generic import View

from apps.competitions.auto_registration import register_all_current, set_auto_registration
from apps.web.mixins import ActionViewMixin, CoordinatorRequiredMixin

from .coordinator_stages import _stage_for_edit


class _TrainingActionView(ActionViewMixin, CoordinatorRequiredMixin, View):
    def get_success_url(self, stage_id: int, *args, **kwargs) -> str:
        return reverse("web:coordinator-stage-edit", args=[stage_id])


class StageAutoRegistrationView(_TrainingActionView):
    """POST ``/coordinator/stages/<id>/training/auto/`` – ``enabled=1`` włącza, brak pola wyłącza."""

    def perform(self, request, stage_id: int) -> str:
        stage = _stage_for_edit(request.competition, stage_id)
        enabled = request.POST.get("enabled") == "1"
        set_auto_registration(stage, enabled, actor=request.user, request=request)
        if enabled:
            return f"Nowe konta uczestników będą zapisywane do etapu {stage.display_name} automatycznie."
        return f"Automatyczny zapis nowych kont do etapu {stage.display_name} jest wyłączony."


class StageRegisterAllView(_TrainingActionView):
    """POST ``/coordinator/stages/<id>/training/register-all/`` – zapis wszystkich obecnych uczestników."""

    def perform(self, request, stage_id: int) -> str:
        stage = _stage_for_edit(request.competition, stage_id)
        summary = register_all_current(stage, actor=request.user, request=request)
        return (
            "Zapis do etapu {name}: zapisano {registered}, już zapisanych {already}, pominięto {skipped} "
            "(bez zgody RODO albo z kontem nieaktywnym – zapiszą się automatycznie, gdy przełącznik "
            "jest włączony, albo sami).".format(name=stage.display_name, **summary)
        )


def training_context(stage) -> dict:
    """Kontekst bloku „Zapis do treningu” na ekranie etapu – pusty dla etapu innego niż treningowy."""
    from apps.accounts.models import Participant
    from apps.competitions.models import StageKind

    if stage is None or stage.kind != StageKind.TRAINING:
        return {}
    return {
        "training": {
            "auto": stage.auto_register_new_participants,
            "entries": stage.entries.filter(participant__isnull=False).count(),
            "participants": Participant.objects.filter(competition_id=stage.edition.competition_id)
            .exclude_anonymised()
            .count(),
        }
    }
