"""„Zasady Organizacji Zawodów” etapu w panelu koordynatora (ZOZ-01).

Ekran jest dzieckiem etapu w menu („Zasady (ZOZ)”): lista wersji ze stanem, formularz nowej
wersji, a przy każdej wersji – edycja projektu, podgląd, publikacja, wycofanie. Reguły (co wolno
zmienić, oczyszczanie HTML-a, audyt) trzyma serwis ``apps.competitions.stage_rules``; widok
wyłącznie orkiestruje, a odmowę serwisu oddaje z jego kodem, jak reszta panelu.

Wersję szukamy przez ``StageRules.objects.for_competition`` – wersja etapu cudzego konkursu
daje 404, tak samo jak cudzy etap (``_stage_for_edit``).
"""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.competitions.stage_rules import (
    StageRules,
    StageRulesStatus,
    create_rules,
    delete_rules,
    publish_rules,
    published,
    update_rules,
    withdraw_rules,
)
from apps.core.api import DomainError
from apps.web.mixins import CoordinatorRequiredMixin
from apps.web.stage_rules_forms import StageRulesForm

from .coordinator_stages import _stage_for_edit
from .stage_rules import pdf_response, render_rules

LIST_TEMPLATE = "web/coordinator/stage_rules.html"
EDIT_TEMPLATE = "web/coordinator/stage_rules_edit.html"


def _rules(request, pk: int) -> StageRules:
    return get_object_or_404(
        StageRules.objects.for_competition(request.competition).select_related("stage", "stage__edition"),
        pk=pk,
    )


def _list_url(rules: StageRules) -> str:
    return reverse("web:coordinator-stage-rules", args=[rules.stage_id])


def _render_list(request, stage, form, *, status: int = 200):
    versions = list(StageRules.objects.filter(stage=stage).order_by("-created_at", "-pk"))
    current = published(StageRules.objects.filter(stage=stage)).first()
    context = {
        "stage": stage,
        "versions": versions,
        "current": current,
        "form": form,
        "public_url": reverse("web:stage-rules", args=[stage.pk]),
    }
    return TemplateResponse(request, LIST_TEMPLATE, context, status=status)


class StageRulesListView(CoordinatorRequiredMixin, View):
    """``/coordinator/stages/<id>/rules/`` – wersje ZOZ etapu i formularz nowej wersji.

    ``?from=<pk>`` wypełnia formularz treścią wskazanej wersji – nowa wersja dokumentu to zwykle
    poprzednia z kilkoma poprawkami, a nie tekst pisany od zera.
    """

    def get(self, request, stage_id: int):
        stage = _stage_for_edit(request.competition, stage_id)
        initial = {}
        source_pk = request.GET.get("from", "")
        if source_pk.isdigit():
            source = StageRules.objects.filter(stage=stage, pk=int(source_pk)).first()
            if source is not None:
                initial = {"title": source.title, "body_html": source.body_html}
        return _render_list(request, stage, StageRulesForm(initial=initial))

    def post(self, request, stage_id: int):
        stage = _stage_for_edit(request.competition, stage_id)
        form = StageRulesForm(request.POST, request.FILES)
        if not form.is_valid():
            return _render_list(request, stage, form, status=400)
        try:
            rules = create_rules(
                stage,
                request.user,
                version=form.cleaned_data["version"],
                document_date=form.cleaned_data["document_date"],
                title=form.cleaned_data["title"],
                body_html=form.cleaned_data["body"],
                pdf=form.cleaned_data.get("pdf"),
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return _render_list(request, stage, form, status=exc.status_code)
        messages.success(request, f"Zapisano projekt wersji {rules.version}. Sprawdź podgląd i opublikuj.")
        return redirect(reverse("web:coordinator-stage-rules-edit", args=[rules.pk]))


class StageRulesEditView(CoordinatorRequiredMixin, View):
    """``/coordinator/stage-rules/<pk>/`` – edycja projektu (opublikowana wersja: tylko odczyt)."""

    def get(self, request, pk: int):
        rules = _rules(request, pk)
        return self._render(request, rules, self._form(rules))

    def post(self, request, pk: int):
        rules = _rules(request, pk)
        form = StageRulesForm(
            request.POST,
            request.FILES,
            initial={"version": rules.version},
            editing=True,
            has_pdf=bool(rules.pdf),
        )
        if not form.is_valid():
            return self._render(request, rules, form, status=400)
        try:
            rules, changed = update_rules(
                rules,
                request.user,
                document_date=form.cleaned_data["document_date"],
                title=form.cleaned_data["title"],
                body_html=form.cleaned_data["body"],
                pdf=form.cleaned_data.get("pdf"),
                remove_pdf=bool(form.cleaned_data.get("remove_pdf")),
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return self._render(request, _rules(request, pk), form, status=exc.status_code)
        messages.success(request, "Zapisano zmiany projektu." if changed else "Bez zmian.")
        return redirect(reverse("web:coordinator-stage-rules-edit", args=[rules.pk]))

    @staticmethod
    def _form(rules: StageRules) -> StageRulesForm:
        return StageRulesForm(
            initial={
                "version": rules.version,
                "document_date": rules.document_date,
                "title": rules.title,
                "body_html": rules.body_html,
            },
            editing=True,
            has_pdf=bool(rules.pdf),
        )

    def _render(self, request, rules: StageRules, form, *, status: int = 200):
        context = {
            "stage": rules.stage,
            "rules": rules,
            "form": form if rules.status == StageRulesStatus.DRAFT else None,
            "public_url": (
                reverse("web:stage-rules-version", args=[rules.stage_id, rules.version])
                if rules.status == StageRulesStatus.PUBLISHED
                else None
            ),
        }
        return TemplateResponse(request, EDIT_TEMPLATE, context, status=status)


class StageRulesPreviewView(CoordinatorRequiredMixin, View):
    """``/coordinator/stage-rules/<pk>/preview/`` – wersja w układzie strony publicznej, w każdym stanie."""

    def get(self, request, pk: int):
        rules = _rules(request, pk)
        versions = list(published(StageRules.objects.filter(stage=rules.stage)))
        return render_rules(request, rules.stage, rules, versions=versions, preview=True)


class StageRulesPdfView(CoordinatorRequiredMixin, View):
    """``/coordinator/stage-rules/<pk>/pdf/`` – plik PDF wersji w każdym stanie (także projektu)."""

    def get(self, request, pk: int):
        rules = _rules(request, pk)
        if not rules.pdf:
            raise Http404("Ta wersja nie ma pliku PDF.")
        return pdf_response(rules)


class _ActionView(CoordinatorRequiredMixin, View):
    """Czynność na wersji: POST → serwis → komunikat → powrót na listę wersji etapu."""

    success = ""

    def act(self, rules: StageRules, request):  # pragma: no cover - nadpisywane
        raise NotImplementedError

    def post(self, request, pk: int):
        rules = _rules(request, pk)
        target = _list_url(rules)
        try:
            self.act(rules, request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            stage = rules.stage
            return _render_list(request, stage, StageRulesForm(), status=exc.status_code)
        messages.success(request, self.success.format(version=rules.version))
        return redirect(target)


class StageRulesPublishView(_ActionView):
    success = "Opublikowano wersję {version}. Od tej chwili jest wersją obowiązującą."

    def act(self, rules, request):
        publish_rules(rules, request.user, request=request)


class StageRulesWithdrawView(_ActionView):
    success = "Wycofano wersję {version}. Nie jest już widoczna w serwisie."

    def act(self, rules, request):
        withdraw_rules(rules, request.user, request=request)


class StageRulesDeleteView(_ActionView):
    success = "Usunięto projekt wersji {version}."

    def act(self, rules, request):
        delete_rules(rules, request.user, request=request)
