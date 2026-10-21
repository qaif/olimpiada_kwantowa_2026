"""Wejścia z menu do narzędzi przy zadaniach: notatniki Qiskit (QC-01) i edytor LaTeX (TEX-01).

Oba narzędzia istnieją wyłącznie **przy zadaniu** – notatnik ma testy i plik startowy zadania, a projekt
w edytorze LaTeX jest projektem zadania. Te strony nie wymyślają więc „wolnego notatnika” ani
„pustego projektu” (D11): wypisują zadania z etapów, do których uczestnik jest zapisany i które już się
zaczęły, z przyciskiem otwarcia. Każda dalsza bramka (okno ucznia, nadzór zdalny, rola kadry,
dyskwalifikacja) zostaje w widoku docelowym – ta strona niczego nie otwiera sama.

Bez flagi (albo bez skonfigurowanej integracji webTEX) adres odpowiada 404, jak widoki docelowe.
"""

from __future__ import annotations

from django.http import Http404
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView

from apps.competitions.models import Problem, StageEntry, StageEntryStatus
from apps.competitions.services import current_edition
from apps.web.mixins import ParticipantRequiredMixin

from .nav import latex_available, notebooks_available


def _open_stages(participant, competition, now):
    """Etapy bieżącej edycji z wpisem uczestnika (bez dyskwalifikacji), które już się zaczęły."""
    edition = current_edition(competition)
    if edition is None:
        return []
    entries = (
        StageEntry.objects.filter(participant=participant, stage__edition=edition)
        .exclude(status=StageEntryStatus.DISQUALIFIED)
        .select_related("stage")
        .order_by("stage__opens_at", "stage_id")
    )
    from apps.time_windows.access import enabled as time_windows_enabled
    from apps.time_windows.access import statements_visible

    windows = time_windows_enabled(competition)

    def opened(stage) -> bool:
        # Okno ucznia (TZ-01): tytuły zadań są jawne od startu **jego** okna, nie ramy etapu – ta sama
        # reguła, co bramka edytora LaTeX (``webtex.services.check_access``).
        if windows:
            return statements_visible(stage, participant=participant, competition=competition, now=now)
        return stage.has_opened(now)

    return [entry.stage for entry in entries if not entry.stage.is_interview and opened(entry.stage)]


class _ToolHubView(ParticipantRequiredMixin, TemplateView):
    template_name = "participant_panel/tools.html"
    tool = ""

    def available(self) -> bool:  # pragma: no cover - nadpisywane
        raise NotImplementedError

    def problems(self, stages) -> list[Problem]:
        return list(
            Problem.objects.filter(stage__in=stages).select_related("stage").order_by("stage_id", "number")
        )

    def open_url(self, problem) -> str:  # pragma: no cover - nadpisywane
        raise NotImplementedError

    def get_context_data(self, **kwargs):
        if not self.available():
            raise Http404
        context = super().get_context_data(**kwargs)
        stages = _open_stages(self.participant, self.competition, timezone.now())
        # Jedno zapytanie o zadania wszystkich etapów, grupowanie w Pythonie – etapów jest kilka.
        by_stage: dict[int, list[dict]] = {}
        for problem in self.problems(stages) if stages else []:
            by_stage.setdefault(problem.stage_id, []).append(
                {"problem": problem, "url": self.open_url(problem)}
            )
        groups = [{"stage": stage, "rows": by_stage[stage.pk]} for stage in stages if stage.pk in by_stage]
        context.update({"tool": self.tool, "groups": groups})
        return context


class NotebooksHubView(_ToolHubView):
    """``/me/notebooks/`` – zadania z notatnikiem (``NotebookTask``) w otwartych etapach uczestnika."""

    tool = "notebooks"

    def available(self) -> bool:
        return notebooks_available(self.competition)

    def problems(self, stages) -> list[Problem]:
        return list(
            Problem.objects.filter(stage__in=stages, notebook_task__isnull=False)
            .select_related("stage")
            .order_by("stage_id", "number")
        )

    def open_url(self, problem) -> str:
        return reverse("web:participant-notebook", args=[problem.pk])


class LatexHubView(_ToolHubView):
    """``/me/latex/`` – zadania otwartych etapów uczestnika z przyciskiem „Pisz w LaTeX”."""

    tool = "latex"

    def available(self) -> bool:
        return latex_available(self.competition)

    def open_url(self, problem) -> str:
        return reverse("web:problem-latex", args=[problem.stage_id, problem.number])
