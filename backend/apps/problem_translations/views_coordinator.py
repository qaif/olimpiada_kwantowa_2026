"""Ekrany koordynatora (komisji tłumaczeń): okna, wersja oficjalna, przegląd i eksport (TR-01 § 2).

Po polsku bez gettext – jak cały panel koordynatora (I18N-01 § 0). Ekrany istnieją wyłącznie
w konkursie z delegacjami (404 w innym, brak pozycji menu). Rolę sprawdza ``CoordinatorRequiredMixin``
(403 dla każdego innego), obiekt wybiera serwis z querysetu konkursu żądania (404 dla cudzego).
Każdy przegląd tłumaczenia i każdy eksport zostawia wpis w dzienniku zdarzeń.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.generic import View

from apps.accounts.delegations import Delegation
from apps.competitions.models import Problem
from apps.core.api import DomainError
from apps.web.mixins import CoordinatorRequiredMixin
from apps.web.throttle import ThrottledFormMixin

from . import languages
from . import services as service
from .forms import ReturnForm, SourceForm, WindowForm
from .markup import render
from .models import SharingMode, Translation, TranslationKind, TranslationRevision, TranslationStatus
from .views_leader import pdf_response, private

OVERVIEW_TEMPLATE = "problem_translations/coordinator/overview.html"
STAGE_TEMPLATE = "problem_translations/coordinator/stage.html"
SOURCE_TEMPLATE = "problem_translations/coordinator/source.html"
REVIEW_TEMPLATE = "problem_translations/coordinator/review.html"
PRINT_TEMPLATE = "problem_translations/coordinator/print.html"


class CoordinatorMixin(CoordinatorRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and self.has_role(request.user):
            service.require_enabled(self.competition)
        return super().dispatch(request, *args, **kwargs)


class OverviewView(CoordinatorMixin, View):
    """``/coordinator/translations/`` – etapy bieżącej edycji i kolejka do przeglądu."""

    def get(self, request):
        now = timezone.now()
        stages = []
        for stage in service.current_stages(self.competition):
            window = service.window_for(stage)
            stages.append({"stage": stage, "window": window, "state": window.state(now) if window else None})
        queue = (
            Translation.objects.for_competition(self.competition)
            .filter(status=TranslationStatus.SUBMITTED, problem__stage__in=[row["stage"] for row in stages])
            .select_related("problem", "problem__stage", "delegation__country")
            .order_by("updated_at")
        )
        context = {"stages": stages, "queue": queue}
        return TemplateResponse(request, OVERVIEW_TEMPLATE, context)


def _matrix(stage):
    """Zadania × warianty (język, a w trybie osobnym – język i delegacja) ze stanem tłumaczenia."""
    mode = service.sharing_mode_of(stage)
    delegations = list(
        Delegation.objects.filter(edition=stage.edition)
        .select_related("country")
        .prefetch_related("translation_languages")
    )
    columns: list[tuple[str, Delegation | None]] = []
    if mode == SharingMode.SEPARATE:
        for delegation in delegations:
            for row in delegation.translation_languages.all():
                columns.append((row.code, delegation))
    else:
        codes = sorted(
            {row.code for d in delegations for row in d.translation_languages.all()},
            key=languages.english_name,
        )
        columns = [(code, None) for code in codes]
    found = {
        (t.problem_id, t.language, t.delegation_id): t
        for t in Translation.objects.filter(problem__stage=stage)
    }
    rows = []
    problems = list(stage.problems.order_by("number"))
    sources = {s.problem_id: s for s in service.ProblemSource.objects.filter(problem__in=problems)}
    for problem in problems:
        rows.append(
            {
                "problem": problem,
                "source": sources.get(problem.pk),
                "cells": [found.get((problem.pk, code, d.pk if d else None)) for code, d in columns],
            }
        )
    headers = [{"code": code, "label": languages.english_name(code), "delegation": d} for code, d in columns]
    return headers, rows


class StageView(CoordinatorMixin, ThrottledFormMixin, View):
    """``/coordinator/translations/stages/<pk>/`` – okno, wersje oficjalne, macierz, eksport."""

    throttle_scope = "translation"

    def get(self, request, pk: int):
        stage = service.stage_for(self.competition, pk)
        window = service.window_for(stage)
        initial = (
            {
                "opens_at": timezone.localtime(window.opens_at),
                "closes_at": timezone.localtime(window.closes_at),
                "sharing_mode": window.sharing_mode,
            }
            if window
            else {"sharing_mode": SharingMode.SEPARATE}
        )
        return self.render(request, stage, WindowForm(initial=initial))

    def post(self, request, pk: int):
        stage = service.stage_for(self.competition, pk)
        form = WindowForm(request.POST)
        if not form.is_valid():
            return self.render(request, stage, form, status=400)
        try:
            service.set_window(stage, **form.cleaned_data, actor=request.user, request=request)
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self.render(request, stage, form, status=400)
        messages.success(request, "Okno tłumaczeń zostało zapisane.")
        return redirect(reverse("web:coordinator-translations-stage", args=[stage.pk]))

    def render(self, request, stage, form, *, status: int = 200):
        window = service.window_for(stage)
        headers, rows = _matrix(stage)
        context = {
            "stage": stage,
            "window": window,
            "state": window.state() if window else None,
            "form": form,
            "headers": headers,
            "rows": rows,
            "variants": service.export_variants(stage),
            "has_translations": any(cell for row in rows for cell in row["cells"]),
        }
        return TemplateResponse(request, STAGE_TEMPLATE, context, status=status)


def _problem(competition, pk: int) -> Problem:
    problem = (
        Problem.objects.for_competition(competition)
        .select_related("stage", "stage__edition")
        .filter(pk=pk, stage__in=service.current_stages(competition))
        .first()
    )
    if problem is None:
        raise Http404("Nie ma takiego zadania.")
    return problem


class SourceView(CoordinatorMixin, ThrottledFormMixin, View):
    """``/coordinator/translations/problems/<pk>/source/`` – tekst wersji oficjalnej i jej wersje."""

    throttle_scope = "translation"

    def get(self, request, pk: int):
        problem = _problem(self.competition, pk)
        source = service.ensure_source(problem)
        return self.render(request, problem, source, SourceForm(initial={"body_md": source.body_md}))

    def post(self, request, pk: int):
        problem = _problem(self.competition, pk)
        form = SourceForm(request.POST)
        source = service.ensure_source(problem)
        if not form.is_valid():
            return self.render(request, problem, source, form, status=400)
        try:
            updated = service.update_source_text(
                problem, form.cleaned_data["body_md"], actor=request.user, request=request
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self.render(request, problem, source, form, status=400)
        if updated.version != source.version:
            messages.success(
                request,
                f"Zapisano wersję {updated.version}. "
                "Tłumaczenia starszych wersji są oznaczone jako nieaktualne.",
            )
        else:
            messages.info(request, "Tekst bez zmian – wersja oficjalna się nie zmieniła.")
        return redirect(reverse("web:coordinator-translations-source", args=[problem.pk]))

    def render(self, request, problem, source, form, *, status: int = 200):
        context = {
            "problem": problem,
            "source": source,
            "source_html": render(source.body_md),
            "revisions": source.revisions.order_by("-version"),
            "form": form,
        }
        return private(TemplateResponse(request, SOURCE_TEMPLATE, context, status=status))


class ReviewView(CoordinatorMixin, View):
    """``/coordinator/translations/<pk>/`` – tłumaczenie obok źródła, różnice, decyzja."""

    def get(self, request, pk: int):
        translation = service.translation_for_coordinator(self.competition, pk)
        return self.render(request, translation, ReturnForm())

    def render(self, request, translation, form, *, status: int = 200):
        service.record_view(request.user, "reviewed", translation, request=request)
        problem = translation.problem
        source = service.ensure_source(problem)
        latest = service.latest_revision(translation)
        previous, diff = service.revision_diff(latest) if latest else (None, [])
        context = {
            "translation": translation,
            "problem": problem,
            "source": source,
            "source_html": render(source.body_md),
            "latest": latest,
            "latest_html": render(latest.body_md) if latest and latest.kind == TranslationKind.TEXT else "",
            "previous": previous,
            "diff": diff,
            "revisions": translation.revisions.order_by("-number"),
            "form": form,
            "rtl": languages.is_rtl(translation.language),
            "outdated": latest is not None and latest.source_version < source.version,
            "statuses": TranslationStatus,
        }
        return private(TemplateResponse(request, REVIEW_TEMPLATE, context, status=status))


def form_revision(data) -> int:
    """Numer wersji, którą komisja miała na ekranie (ukryte pole). Brak = ``-1``, czyli zawsze konflikt."""
    try:
        return int(data.get("revision", ""))
    except TypeError, ValueError:
        return -1


def _refused(request, translation, exc: DomainError, form=None):
    """Odmowa decyzji komisji: ekran przeglądu z komunikatem i kodem odpowiedzi odmowy (409 przy wyścigu).

    Ekran pokazuje już **bieżącą** wersję – komisja czyta to, co faktycznie leży do decyzji.
    """
    messages.error(request, str(exc.detail))
    translation.refresh_from_db()
    return ReviewView(request=request).render(
        request, translation, form or ReturnForm(), status=exc.status_code
    )


class ApproveView(CoordinatorMixin, ThrottledFormMixin, View):
    throttle_scope = "translation"

    def post(self, request, pk: int):
        translation = service.translation_for_coordinator(self.competition, pk)
        try:
            revision = service.approve(
                translation,
                actor=request.user,
                request=request,
                expected_revision=form_revision(request.POST),
            )
        except DomainError as exc:
            return _refused(request, translation, exc)
        messages.success(request, f"Zatwierdzono wersję {revision.number}.")
        return redirect(reverse("web:coordinator-translation", args=[pk]))


class ReturnView(CoordinatorMixin, ThrottledFormMixin, View):
    throttle_scope = "translation"

    def post(self, request, pk: int):
        translation = service.translation_for_coordinator(self.competition, pk)
        form = ReturnForm(request.POST)
        if not form.is_valid():
            return ReviewView(request=request).render(request, translation, form, status=400)
        try:
            service.return_translation(
                translation,
                form.cleaned_data["comment"],
                actor=request.user,
                request=request,
                expected_revision=form_revision(request.POST),
            )
        except DomainError as exc:
            return _refused(request, translation, exc, form)
        messages.success(request, "Tłumaczenie zwrócone do poprawy – opiekunowie dostali list.")
        return redirect(reverse("web:coordinator-translation", args=[pk]))


class RevisionFileView(CoordinatorMixin, View):
    """PDF wysłanej wersji – bez znaku wodnego (komisja), z wpisem w dzienniku."""

    def get(self, request, pk: int):
        revision = (
            TranslationRevision.objects.for_competition(self.competition)
            .select_related("translation__problem")
            .filter(pk=pk)
            .first()
        )
        if revision is None or not revision.pdf:
            raise Http404
        service.record_view(
            request.user, "file_reviewed", revision.translation, request=request, revision=revision.number
        )
        with revision.pdf.open("rb") as handle:
            data = handle.read()
        problem = revision.translation.problem
        return pdf_response(
            data, f"problem-{problem.number}-{revision.translation.language}-v{revision.number}.pdf"
        )


class ExportPrintView(CoordinatorMixin, View):
    """Widok do druku: zatwierdzone wersje w jednym języku, KaTeX w przeglądarce → „Zapisz jako PDF”."""

    def get(self, request, pk: int, language: str):
        stage = service.stage_for(self.competition, pk)
        delegation = service.export_delegation(stage, request.GET.get("delegation"))
        rows = [
            {
                "problem": problem,
                "revision": revision,
                "html": render(revision.body_md)
                if revision and revision.kind == TranslationKind.TEXT
                else "",
                "stale": revision is not None and service.is_stale(revision, problem),
            }
            for problem, revision in service.export_rows(stage, language, delegation)
        ]
        service.record_view(
            request.user,
            "exported",
            stage,
            request=request,
            language=language,
            delegation=delegation.pk if delegation else None,
            format="print",
        )
        context = {
            "stage": stage,
            "language": language,
            "language_label": languages.label(language),
            "rtl": languages.is_rtl(language),
            "delegation": delegation,
            "rows": rows,
            "stale_note": service.STALE_NOTE,
        }
        return private(TemplateResponse(request, PRINT_TEMPLATE, context))


class ExportPdfView(CoordinatorMixin, View):
    """PDF do druku (pypdf + reportlab) – patrz ``pdf.py`` o tym, co serwer umie złożyć."""

    def get(self, request, pk: int, language: str):
        stage = service.stage_for(self.competition, pk)
        delegation = service.export_delegation(stage, request.GET.get("delegation"))
        try:
            data = service.export_pdf(stage, language, delegation, actor=request.user, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect(reverse("web:coordinator-translations-stage", args=[stage.pk]))
        suffix = f"-{delegation.country.code}" if delegation is not None else ""
        response: HttpResponse = pdf_response(data, f"stage-{stage.pk}-{language}{suffix}.pdf")
        response["Content-Disposition"] = response["Content-Disposition"].replace("inline", "attachment")
        return response
