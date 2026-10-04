"""Ekrany opiekuna drużyny: języki delegacji, edytor tłumaczenia i języki uczniów (TR-01 § 2).

Uprawnienie ma te same dwa piętra, co panel delegacji (``apps.web.views.delegation``): rola
``team_leader`` (inaczej 403) i wiersz ``DelegationLeader`` w bieżącej edycji (inaczej 404). Trzecie
piętro – **otwarte okno tłumaczeń** – rozstrzyga ``services.problem_for_leader`` przy każdym adresie
zadania: poza oknem zadanie nie istnieje (404), także dla opiekuna.

Odpowiedzi z treścią zadania mają ``Cache-Control: no-store`` – przeglądarka wspólnego komputera
w sali tłumaczeń nie może podać arkusza następnej osobie z pamięci podręcznej.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import add_never_cache_headers
from django.utils.translation import gettext as _
from django.views.generic import View

from apps.accounts import delegation_services
from apps.core.api import DomainError
from apps.web.throttle import ThrottledFormMixin
from apps.web.views.delegation import TeamLeaderRequiredMixin

from . import languages
from . import services as service
from .forms import DraftForm, LanguagesForm, PdfForm
from .markup import render
from .models import SharingMode, Translation, TranslationStatus

DASHBOARD_TEMPLATE = "problem_translations/leader/dashboard.html"
EDITOR_TEMPLATE = "problem_translations/leader/editor.html"
AUTOSAVE_TEMPLATE = "problem_translations/leader/_autosave.html"


def private(response):
    """Treść poufna: bez pamięci podręcznej i bez indeksowania."""
    add_never_cache_headers(response)
    response["Cache-Control"] = "no-store, private, max-age=0"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def pdf_response(data: bytes, filename: str) -> HttpResponse:
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return private(response)


class LeaderMixin(TeamLeaderRequiredMixin):
    """Opiekun z delegacją (patrz ``TeamLeaderRequiredMixin``) i tryb delegacji konkursu."""


class TranslationsDashboardView(LeaderMixin, View):
    """``/delegation/translations/`` – języki, etapy z oknami, zadania × języki i języki uczniów."""

    def get(self, request):
        return self.render(request, LanguagesForm(initial=self._initial()))

    def _initial(self) -> dict:
        codes = service.languages_of(self.delegation)
        return {"primary": codes[0] if codes else "", "secondary": codes[1] if len(codes) > 1 else ""}

    def render(self, request, form, *, status: int = 200):
        delegation = self.delegation
        codes = service.languages_of(delegation)
        now = timezone.now()
        stages = []
        for stage in service.current_stages(self.competition):
            window = service.window_for(stage)
            row = {
                "stage": stage,
                "window": window,
                "state": window.state(now) if window else None,
                "problems": [],
            }
            if window is not None and window.is_open(now) and codes:
                owner = delegation if window.sharing_mode == SharingMode.SEPARATE else None
                found = {
                    (t.problem_id, t.language): t
                    for t in Translation.objects.filter(
                        problem__stage=stage, language__in=codes, delegation=owner
                    )
                }
                for problem in stage.problems.order_by("number"):
                    row["problems"].append(
                        {
                            "problem": problem,
                            "cells": [
                                {
                                    "code": code,
                                    "label": languages.english_name(code),
                                    "t": found.get((problem.pk, code)),
                                }
                                for code in codes
                            ],
                        }
                    )
            stages.append(row)
        overrides = service.student_overrides(delegation)
        students = [
            {"participant": p, "override": overrides.get(p.pk, "")}
            for p in delegation_services.students_of(delegation)
        ]
        context = {
            "delegation": delegation,
            "codes": codes,
            "language_options": [(code, languages.label(code)) for code in codes],
            "form": form,
            "stages": stages,
            "students": students,
        }
        return private(TemplateResponse(request, DASHBOARD_TEMPLATE, context, status=status))


class LanguagesView(LeaderMixin, ThrottledFormMixin, View):
    throttle_scope = "translation"

    def post(self, request):
        form = LanguagesForm(request.POST)
        if not form.is_valid():
            return TranslationsDashboardView(request=request, leader=self.leader).render(
                request, form, status=400
            )
        try:
            service.declare_languages(self.leader, form.codes(), actor=request.user, request=request)
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return TranslationsDashboardView(request=request, leader=self.leader).render(
                request, form, status=400
            )
        messages.success(request, _("Języki delegacji zostały zapisane."))
        return redirect(reverse("web:delegation-translations"))


class StudentLanguageView(LeaderMixin, ThrottledFormMixin, View):
    throttle_scope = "translation"

    def post(self, request, pk: int):
        try:
            service.set_student_language(
                self.leader, pk, request.POST.get("language", ""), actor=request.user, request=request
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, _("Język ucznia został zapisany."))
        return redirect(reverse("web:delegation-translations") + "#students")


class ProblemMixin(LeaderMixin):
    """Zadanie w otwartym oknie i język delegacji – oba z serwisu, oba 404 poza zakresem."""

    def load(self, pk: int, language: str):
        self.problem = service.problem_for_leader(self.leader, pk)
        self.language = language
        self.translation = service.find_translation(self.leader, self.problem, language)


class EditorView(ProblemMixin, ThrottledFormMixin, View):
    """``/delegation/translations/problems/<pk>/<language>/`` – źródło obok tłumaczenia."""

    throttle_scope = "translation"

    def get(self, request, pk: int, language: str):
        self.load(pk, language)
        service.record_view(
            request.user,
            "source_viewed",
            self.problem,
            request=request,
            delegation=self.delegation.pk,
            language=language,
        )
        translation = self.translation
        form = DraftForm(
            initial={
                "title": translation.title if translation else "",
                "body_md": translation.body_md if translation else "",
            }
        )
        return self.render(request, form)

    def post(self, request, pk: int, language: str):
        """Zapis bez JavaScriptu – ten sam serwis, co autozapis."""
        self.load(pk, language)
        form = DraftForm(request.POST)
        if form.is_valid():
            try:
                service.save_draft(
                    self.leader, self.problem, language, actor=request.user, **form.cleaned_data
                )
            except DomainError as exc:
                messages.error(request, str(exc.detail))
            else:
                messages.success(request, _("Szkic zapisany."))
        return redirect(reverse("web:delegation-translation", args=[pk, language]))

    def render(self, request, form, *, status: int = 200):
        problem, translation = self.problem, self.translation
        source = service.ensure_source(problem)
        context = {
            "delegation": self.delegation,
            "problem": problem,
            "language": self.language,
            "language_label": languages.label(self.language),
            "rtl": languages.is_rtl(self.language),
            "source": source,
            "source_html": render(source.body_md),
            "translation": translation,
            "preview_html": render(translation.body_md) if translation else "",
            "editable": translation is None or translation.is_editable_status,
            "form": form,
            "pdf_form": PdfForm(),
            "window": service.window_for(problem.stage),
            "shared": service.sharing_mode_of(problem.stage) == SharingMode.SHARED,
            "revisions": list(translation.revisions.all()) if translation else [],
            "source_changes": (
                service.source_diff(problem, translation.source_version)
                if translation is not None and translation.is_outdated
                else []
            ),
            "statuses": TranslationStatus,
        }
        return private(TemplateResponse(request, EDITOR_TEMPLATE, context, status=status))


class AutosaveView(ProblemMixin, ThrottledFormMixin, View):
    """HTMX: zapis szkicu i podgląd. Odpowiedź to fragment (stan zapisu + podgląd z formułami)."""

    throttle_scope = "translation"

    def post(self, request, pk: int, language: str):
        self.load(pk, language)
        form = DraftForm(request.POST)
        context = {"language": language, "rtl": languages.is_rtl(language)}
        if not form.is_valid():
            context["error"] = " ".join(str(e) for errors in form.errors.values() for e in errors)
            return private(TemplateResponse(request, AUTOSAVE_TEMPLATE, context, status=400))
        try:
            translation = service.save_draft(
                self.leader, self.problem, language, actor=request.user, **form.cleaned_data
            )
        except DomainError as exc:
            context["error"] = str(exc.detail)
            return private(TemplateResponse(request, AUTOSAVE_TEMPLATE, context, status=exc.status_code))
        context.update({"saved_at": translation.updated_at, "preview_html": render(translation.body_md)})
        return private(TemplateResponse(request, AUTOSAVE_TEMPLATE, context))


class _ActionView(ProblemMixin, ThrottledFormMixin, View):
    throttle_scope = "translation"
    success_message = ""

    def perform(self, request):  # pragma: no cover - klasa abstrakcyjna
        raise NotImplementedError

    def post(self, request, pk: int, language: str):
        self.load(pk, language)
        try:
            self.perform(request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, self.success_message)
        return redirect(reverse("web:delegation-translation", args=[pk, language]))


class UploadView(_ActionView):
    def post(self, request, pk: int, language: str):
        self.load(pk, language)
        form = PdfForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, _("Wybierz plik PDF."))
        else:
            try:
                service.upload_pdf(
                    self.leader,
                    self.problem,
                    language,
                    form.cleaned_data["file"],
                    title=form.cleaned_data.get("title") or "",
                    actor=request.user,
                    request=request,
                )
            except DomainError as exc:
                messages.error(request, str(exc.detail))
            else:
                messages.success(request, _("Plik PDF został zapisany jako tłumaczenie."))
        return redirect(reverse("web:delegation-translation", args=[pk, language]))


class SubmitView(_ActionView):
    """„Wyślij do akceptacji”. Z formularza edytora przychodzi też tekst – zapisujemy go najpierw,
    żeby wysłać to, co widać w polu, a nie to, co zdążył zapisać autozapis."""

    def perform(self, request):
        self.success_message = _("Tłumaczenie wysłane do akceptacji.")
        if "body_md" in request.POST:
            form = DraftForm(request.POST)
            if not form.is_valid():
                raise DomainError(_("Tekst jest za długi."), "TRANSLATION_TOO_LONG")
            service.save_draft(
                self.leader, self.problem, self.language, actor=request.user, **form.cleaned_data
            )
        service.submit(self.leader, self.problem, self.language, actor=request.user, request=request)


class WithdrawView(_ActionView):
    def perform(self, request):
        self.success_message = _("Wysłanie cofnięte – tłumaczenie jest znowu szkicem.")
        service.withdraw(self.leader, self.problem, self.language, actor=request.user, request=request)


class ReopenView(_ActionView):
    def perform(self, request):
        self.success_message = _(
            "Tłumaczenie otwarte do aktualizacji. Uczniowie widzą dotąd zatwierdzoną wersję."
        )
        service.reopen(self.leader, self.problem, self.language, actor=request.user, request=request)


class SourcePdfView(LeaderMixin, View):
    """PDF wersji oficjalnej ze znakiem wodnym delegacji – w otwartym oknie, z audytem pobrania."""

    def get(self, request, pk: int):
        problem = service.problem_for_leader(self.leader, pk)
        data = service.source_pdf_for_leader(self.leader, problem, request=request)
        return pdf_response(data, f"problem-{problem.number}-official.pdf")


class TranslationPdfView(ProblemMixin, View):
    """Wgrany PDF tłumaczenia ze znakiem wodnym – ten sam zakres, co edytor."""

    def get(self, request, pk: int, language: str):
        self.load(pk, language)
        if self.translation is None:
            raise Http404
        data = service.translation_pdf_for_leader(self.leader, self.translation, request=request)
        return pdf_response(data, f"problem-{self.problem.number}-{language}.pdf")
