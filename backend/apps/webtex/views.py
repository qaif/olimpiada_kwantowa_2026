"""Ekrany edytora LaTeX: otwarcie (302 z tokenem), złożenie PDF-u z karty zadania, przełącznik etapu.

Kolejność bramek jak w notatnikach: najpierw rola (mixin – anonim 302, obca rola 403), potem flaga
konkursu i konfiguracja (404 – adresu „nie ma”), potem bramki uczestnika (``services.check_access``).
Obiekty zawsze przez ``for_competition`` – etap sąsiedniego konkursu daje 404.
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.generic import View

from apps.competitions.models import Problem, Stage
from apps.core.api import DomainError
from apps.web.mixins import CoordinatorRequiredMixin, ParticipantRequiredMixin
from apps.web.throttle import ThrottledFormMixin

from . import client, services
from .forms import LatexSubmitForm, StageLatexSettingsForm

logger = logging.getLogger(__name__)


class _LatexParticipantMixin(ParticipantRequiredMixin):
    def resolve(self, stage_id: int, number: int) -> tuple[Stage, Problem]:
        if not services.available(self.competition):
            raise Http404("Edytor LaTeX jest w tym konkursie wyłączony.")
        stage = get_object_or_404(
            Stage.objects.for_competition(self.competition).select_related("edition__competition"),
            pk=stage_id,
        )
        problem = get_object_or_404(
            Problem.objects.for_competition(self.competition), stage=stage, number=number
        )
        return stage, problem


class ProblemLatexOpenView(_LatexParticipantMixin, View):
    """``GET /me/stages/<id>/problems/<n>/latex/`` – bramki, potem 302 na ``{WEBTEX_URL}/sso#token=…``.

    Link na karcie otwiera ten adres w **nowej karcie** (TEX-01 D2). Token żyje 120 s i jest
    jednorazowy, więc każde kliknięcie wystawia nowy; odpowiedź nie trafia do żadnej pamięci
    podręcznej (``no-store``), a token stoi we fragmencie – nie jedzie do serwera ani w ``Referer``.
    """

    template_name = "webtex/refused.html"

    def get(self, request, stage_id: int, number: int):
        stage, problem = self.resolve(stage_id, number)
        try:
            services.check_access(request.user, self.participant, stage, problem, self.competition)
        except DomainError as exc:
            if exc.status_code == 404:
                raise Http404(str(exc.detail)) from exc
            return TemplateResponse(
                request,
                self.template_name,
                {"message": str(exc.detail), "back_url": services.tasks_tab_url(request, problem)},
                status=exc.status_code,
            )
        response = HttpResponseRedirect(
            services.open_url(request, self.participant, stage, problem, self.competition)
        )
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response


class ProblemLatexSubmitView(_LatexParticipantMixin, ThrottledFormMixin, View):
    """``POST /me/stages/<id>/problems/<n>/latex/submit/`` – PDF z edytora jako zwykłe rozwiązanie.

    Platforma sama pobiera świeżo skompilowany PDF z webTEX (serwer–serwer) i woła
    ``create_submission`` – edytorowi nie ufamy ani w treści pliku, ani w terminie. Odpowiedź HTMX
    to odświeżona karta zadania, jak po uploadzie (``ProblemUploadView``); scope limitu ten sam.
    """

    template_name = "web/participant/_problem_card.html"
    throttle_scope = "upload"

    def post(self, request, stage_id: int, number: int):
        from apps.competitions.models import StageEntry
        from apps.time_windows.access import enabled as time_windows_enabled
        from apps.time_windows.access import personal_stage
        from apps.web.forms import SubmissionUploadForm
        from apps.web.views.participant import _problem_row

        stage, problem = self.resolve(stage_id, number)
        participant = self.participant
        entry = get_object_or_404(StageEntry, participant=participant, stage=stage)
        form = LatexSubmitForm(request.POST)
        error = ""
        issues: list[dict] = []
        notice = ""
        try:
            services.check_access(request.user, participant, stage, problem, self.competition)
        except DomainError as exc:
            if exc.status_code == 404:
                # Jak upload: przed startem okna odpowiedź nie może nieść karty z tytułem zadania.
                raise Http404(str(exc.detail)) from exc
            error = str(exc.detail)
        window_stage = (
            personal_stage(stage, participant, self.competition)
            if time_windows_enabled(self.competition)
            else stage
        )
        if not error and form.is_valid():
            try:
                services.precheck_submission(window_stage, problem)
                submission = services.submit_from_editor(request, stage, problem, self.competition)
            except client.WebtexError as exc:
                logger.info(
                    "Złożenie z edytora LaTeX odrzucone (%s): etap %s, zadanie %s", exc.code, stage.pk, number
                )
                error, issues = exc.message, exc.issues
            except DomainError as exc:
                error = str(exc.detail)
            else:
                notice = _("PDF z edytora został złożony jako wersja %(version)s.") % {
                    "version": submission.version
                }
        now = timezone.now()
        entry.stage = window_stage
        context = {
            "row": _problem_row(request.user, entry, problem, self.competition),
            "stage": window_stage,
            "entry": entry,
            "now": now,
            "upload_open": window_stage.is_open_for_submissions(now) and window_stage.closed_at is None,
            "upload_form": SubmissionUploadForm(),
            # Wynik tej czynności pokazuje panel edytora na karcie (``webtex_tags.latex_card_panel``),
            # a nie komunikat nad kartą: błąd dotyczy złożenia z edytora, nie pola pliku obok.
            "latex_form": form if form.errors else LatexSubmitForm(),
            "latex_error": error,
            "latex_issues": issues,
            "latex_notice": notice,
        }
        return TemplateResponse(request, self.template_name, context)


#: Komunikat po zmianie przełącznika: (włączony, wyłączony).
OPTION_MESSAGES = {
    "sharing_enabled": (
        "Udostępnianie linków w edytorze LaTeX włączone dla etapu {stage}.",
        "Udostępnianie linków w edytorze LaTeX wyłączone dla etapu {stage} – "
        "współpracownicy zostaną odcięci.",
    ),
    "ai_enabled": (
        "Asystent AI w edytorze LaTeX włączony dla etapu {stage}.",
        "Asystent AI w edytorze LaTeX wyłączony dla etapu {stage}.",
    ),
}


class StageLatexSettingsView(CoordinatorRequiredMixin, View):
    """``/coordinator/stages/<id>/latex/`` – przełączniki „Udostępnianie linków” i „Asystent AI”."""

    template_name = "webtex/coordinator_stage.html"

    def _stage(self, stage_id: int) -> Stage:
        if not services.is_enabled(self.competition):
            raise Http404("Edytor LaTeX jest w tym konkursie wyłączony.")
        return get_object_or_404(
            Stage.objects.for_competition(self.competition).select_related("edition"), pk=stage_id
        )

    def _render(self, request, stage: Stage, form: StageLatexSettingsForm, *, status: int = 200):
        from apps.webtex import keys

        return TemplateResponse(
            request,
            self.template_name,
            {"stage": stage, "form": form, "configured": keys.configured()},
            status=status,
        )

    def get(self, request, stage_id: int):
        stage = self._stage(stage_id)
        return self._render(request, stage, StageLatexSettingsForm(initial=services.stage_options(stage)))

    def post(self, request, stage_id: int):
        stage = self._stage(stage_id)
        form = StageLatexSettingsForm(request.POST)
        if not form.is_valid():
            return self._render(request, stage, form, status=400)
        changed = False
        for name, (on, off) in OPTION_MESSAGES.items():
            enabled = form.cleaned_data[name]
            if services.set_option(stage, name, enabled, actor=request.user, request=request):
                changed = True
                messages.success(request, (on if enabled else off).format(stage=stage.display_name))
        if not changed:
            messages.info(request, "Bez zmian.")
        return redirect(reverse("web:coordinator-stage-latex", args=[stage.pk]))
