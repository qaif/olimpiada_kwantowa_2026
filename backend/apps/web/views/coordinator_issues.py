"""Zgłoszenia problemów z pracami w panelu koordynatora ``/coordinator/issues/``.

Ekran jest **kolejką do obsłużenia**, a nie archiwum: otwarte zgłoszenia stoją na górze, każde
z odnośnikami do tego, co się zwykle po nim robi – do przydziałów etapu (żeby zobaczyć pracę
w kontekście) i do „Odbierz pracę”, czyli istniejącego ``unassign_reviewer``. Skróty są tu
istotne: zgłoszenie „rozwiązanie innego zadania” kończy się prawie zawsze odebraniem pracy
recenzentowi, a klikanie do tego przez trzy ekrany zniechęcałoby do korzystania z całego narzędzia.

Rozstrzygnięcie wymaga zdania uzasadnienia (``apps.grading.issues.resolve_issue``) – zamknięte
zgłoszenie bez ani jednego słowa nie mówi w aktach nic poza tym, że ktoś kliknął przycisk.

Osobny moduł od ``coordinator.py`` i ``coordinator_reports.py``, bo to własny ekran z własną
kolejką; wspólna jest wyłącznie bramka roli (``CoordinatorRequiredMixin``).
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import TemplateView, View

from apps.competitions.models import Stage
from apps.competitions.services import current_edition
from apps.grading.issues import issue_rows, resolve_issue
from apps.grading.models import WorkIssue, WorkIssueStatus
from apps.grading.services import unassign_reviewer
from apps.web.mixins import ActionViewMixin, CoordinatorRequiredMixin

#: Ile zgłoszeń pokazuje ekran. Kolejka ma odpowiadać na pytanie „co jest do zrobienia”, a pełną
#: historię zgłoszeń ma audyt (``issue.opened`` / ``issue.resolved``).
ISSUE_LIMIT = 200


class CoordinatorIssuesView(CoordinatorRequiredMixin, TemplateView):
    """``GET /coordinator/issues/`` – kolejka zgłoszeń, z filtrem po etapie.

    Domyślnie widać **całą bieżącą edycję**, a nie wybrany etap: problem z pracą nie czeka na to,
    aż koordynator trafi na właściwą kartę, a etapów w edycji są trzy. Filtr jest zwykłym
    parametrem ``?stage=<id>`` – adres z filtrem ma dać się zapisać i wysłać dalej.
    """

    template_name = "web/coordinator/issues.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        edition = current_edition()
        stages = list(Stage.objects.filter(edition=edition).order_by("opens_at", "id")) if edition else []
        raw_stage = (self.request.GET.get("stage") or "").strip()
        selected = next((stage for stage in stages if str(stage.pk) == raw_stage), None)
        rows = list(issue_rows(selected)[:ISSUE_LIMIT])
        context.update(
            {
                "edition": edition,
                "stages": stages,
                "selected_stage": selected,
                "rows": rows,
                "open_count": sum(1 for issue in rows if issue.status == WorkIssueStatus.OPEN),
            }
        )
        return context


class ResolveIssueView(ActionViewMixin, CoordinatorRequiredMixin, View):
    """``POST /coordinator/issues/<id>/resolve/`` – zamknięcie zgłoszenia z uzasadnieniem.

    Powrót zachowuje filtr etapu z formularza: koordynator przechodzący kolejkę jednego etapu ma
    wrócić do tej samej kolejki, a nie do widoku całej edycji.
    """

    def get_success_url(self, pk: int, *args, **kwargs) -> str:
        stage_id = (self.request.POST.get("stage") or "").strip()
        base = reverse("web:coordinator-issues")
        return f"{base}?stage={stage_id}" if stage_id else base

    def perform(self, request, pk: int) -> str:
        issue = get_object_or_404(WorkIssue, pk=pk)
        resolve_issue(issue, request.POST.get("resolution", ""), actor=request.user, request=request)
        return "Zgłoszenie zostało rozwiązane."


class IssueUnassignView(ActionViewMixin, CoordinatorRequiredMixin, View):
    """``POST /coordinator/issues/<id>/unassign/`` – „Odbierz pracę” prosto z kolejki zgłoszeń.

    Skrót, a nie nowa reguła: woła istniejące ``grading.services.unassign_reviewer``, z tymi samymi
    bramkami i tym samym wpisem audytowym, co przycisk na ekranie przydziałów. Zgłoszenie zostaje
    **otwarte** – odebranie pracy jest czynnością, a nie rozstrzygnięciem sprawy; koordynator
    zamyka je osobno, wpisując, co się z pracą stało.
    """

    def get_success_url(self, pk: int, *args, **kwargs) -> str:
        stage_id = (self.request.POST.get("stage") or "").strip()
        base = reverse("web:coordinator-issues")
        return f"{base}?stage={stage_id}" if stage_id else base

    def perform(self, request, pk: int) -> str:
        issue = get_object_or_404(WorkIssue.objects.select_related("review"), pk=pk)
        unassign_reviewer(issue.review, actor=request.user, request=request)
        return "Praca została odebrana recenzentowi. Zgłoszenie nadal czeka na rozstrzygnięcie."
