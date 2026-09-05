"""Panel komisji odwoławczej ``/appeals/``.

Kolejka pochodzi z ``appeals.services.appeals_queue`` – reklamacja, przy której członek komisji
jest w konflikcie interesów (recenzował tę pracę), w ogóle się w niej nie pojawia. Decyzję
podejmuje ``decide_appeal``; widok nie zna ani reguł punktacji, ani konfliktu interesów.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import TemplateView, View

from apps.appeals.models import Appeal
from apps.appeals.services import appeals_queue, decide_appeal
from apps.core.api import DomainError
from apps.grading.models import ROUND_BLIND, ReviewStatus
from apps.web.forms import AppealDecideForm
from apps.web.mixins import ActionViewMixin, AppealsCommitteeRequiredMixin


def _round_one_reviews(appeal: Appeal) -> list[dict]:
    """Oceny rundy 1 pokazywane komisji: punkty i argumentacja, bez tożsamości recenzentów."""
    return [
        {"score": review.score, "comment_internal": review.comment_internal}
        for review in appeal.submission.reviews.all()
        if review.round == ROUND_BLIND and review.status == ReviewStatus.SUBMITTED
    ]


class AppealsQueueView(AppealsCommitteeRequiredMixin, TemplateView):
    """Lista reklamacji do rozpatrzenia wraz z argumentem uczestnika i ocenami rundy 1."""

    template_name = "web/appeals/list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        rows = []
        for appeal in appeals_queue(self.member):
            grade = getattr(appeal.submission, "final_grade", None)
            rows.append(
                {
                    "appeal": appeal,
                    "submission": appeal.submission,
                    "public_code": appeal.submission.entry.participant.public_code,
                    "current_score": grade.score if grade is not None else None,
                    "reviews": _round_one_reviews(appeal),
                }
            )
        context["rows"] = rows
        context["decide_form"] = AppealDecideForm()
        return context


class AppealDecideView(ActionViewMixin, AppealsCommitteeRequiredMixin, View):
    """Rozstrzygnięcie reklamacji (odrzucona / uwzględniona / częściowo)."""

    success_url = reverse_lazy("web:appeals")

    def perform(self, request, pk: int) -> str:
        appeal = get_object_or_404(Appeal.objects.select_related("submission"), pk=pk)
        form = AppealDecideForm(request.POST)
        if not form.is_valid():
            raise DomainError("Decyzja wymaga rozstrzygnięcia i uzasadnienia.", "INVALID_DECISION")
        decide_appeal(
            appeal,
            self.member,
            form.cleaned_data["status"],
            form.cleaned_data["new_score"],
            form.cleaned_data["justification"],
            request=request,
        )
        return "Reklamacja została rozstrzygnięta."
