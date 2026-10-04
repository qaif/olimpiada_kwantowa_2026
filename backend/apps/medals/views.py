"""Ekrany medali: panel koordynatora (po polsku) i dwie strony publiczne (gettext) – MED-01.

Każdy widok zaczyna od bramki flagi konkursu (``services.require_enabled`` → 404) i etapu z querysetu
zawężonego do konkursu żądania (``services.stage_for`` → 404 dla cudzego). Rolę koordynatora
sprawdza ``CoordinatorRequiredMixin`` (403), a serwis jeszcze raz – sam.

Strony publiczne (``/results/<etap>/medals/`` i ``/results/<etap>/countries/``) czytają **wyłącznie**
zamrożone pola schematu, nigdy bazy wyników – ta sama zasada, co tabela wyników. Filtr kraju jest
zwykłym formularzem GET (bez JavaScriptu, CSP bez wyjątków).
"""

from __future__ import annotations

from django.contrib import messages
from django.http import FileResponse, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.text import slugify
from django.views.generic import View

from apps.core.api import DomainError
from apps.core.exports import csv_response
from apps.results.certificates import build_certificates_zip
from apps.web.mixins import CoordinatorRequiredMixin
from apps.web.throttle import ThrottledFormMixin

from . import exports
from . import services as service
from .awards import medal_order
from .documents import language_overview
from .forms import IssueForm, OverrideForm, SchemeForm, UnfreezeForm
from .models import Award

LIST_TEMPLATE = "medals/coordinator_list.html"
DETAIL_TEMPLATE = "medals/coordinator_detail.html"
PUBLIC_TEMPLATE = "medals/public_medals.html"
COUNTRIES_TEMPLATE = "medals/public_countries.html"


class MedalScreenMixin(CoordinatorRequiredMixin):
    """Flaga konkursu i etap z zakresu konkursu żądania."""

    def competition_or_404(self, request):
        competition = getattr(request, "competition", None)
        service.require_enabled(competition)
        return competition

    def stage_or_404(self, request, stage_id: int):
        return service.stage_for(self.competition_or_404(request), stage_id)

    def back(self, stage_id: int):
        return redirect(reverse("web:coordinator-medal", args=[stage_id]))


class MedalStageListView(MedalScreenMixin, View):
    """``/coordinator/medals/`` – etapy bieżącej edycji, stan medali i stan potoku składu."""

    def get(self, request):
        competition = self.competition_or_404(request)
        stages = service.stages_of(competition)
        schemes = {scheme.stage_id: scheme for scheme in service.MedalScheme.objects.filter(stage__in=stages)}
        context = {
            "competition": competition,
            "rows": [{"stage": stage, "scheme": schemes.get(stage.pk)} for stage in stages],
            "languages": language_overview(),
        }
        return TemplateResponse(request, LIST_TEMPLATE, context)


class MedalSchemeView(MedalScreenMixin, ThrottledFormMixin, View):
    """``/coordinator/medals/<etap>/`` – progi (POST), podgląd, ręczne zmiany, ogłoszenie i dokumenty."""

    throttle_scope = "medals"

    def get(self, request, stage_id: int):
        stage = self.stage_or_404(request, stage_id)
        scheme = service.scheme_for(stage)
        return self._render(request, scheme, SchemeForm(instance=scheme))

    def post(self, request, stage_id: int):
        stage = self.stage_or_404(request, stage_id)
        scheme = service.scheme_for(stage, create=True)
        form = SchemeForm(request.POST, instance=service.MedalScheme(stage=stage))
        if not form.is_valid():
            return self._render(request, scheme, form, status=400)
        try:
            service.update_scheme(scheme, values=form.cleaned_data, actor=request.user, request=request)
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self._render(request, scheme, form, status=400)
        messages.success(request, "Progi zapisane. Podgląd poniżej liczy się już z nowymi wartościami.")
        return self.back(stage.pk)

    def _render(self, request, scheme, form, *, status: int = 200):
        stage = scheme.stage
        result = service.preview(scheme)
        publication = service.publication_of(stage)
        rows = []
        for row in result.rows:
            entry_id = row["entry_id"]
            frozen = (scheme.awards or {}).get(str(entry_id)) if scheme.is_frozen else None
            rows.append(
                {
                    "row": row,
                    "computed": result.computed.get(entry_id, Award.NONE),
                    "final": frozen["award"] if frozen else result.final.get(entry_id, Award.NONE),
                    "override": result.overrides.get(entry_id),
                }
            )
        context = {
            "stage": stage,
            "scheme": scheme,
            "form": form,
            "rows": rows,
            "thresholds": result.thresholds,
            "final_counts": scheme.thresholds.get("final_counts")
            if scheme.is_frozen
            else result.final_counts,
            "awards": Award,
            "override_form": OverrideForm(),
            "unfreeze_form": UnfreezeForm(),
            "issue_form": IssueForm(),
            "publication": publication,
            "republished": service.republished_since_freeze(scheme, publication),
            "medal_choices": Award.choices,
            "shares": {award: result.thresholds.share(award) for award in Award.values},
        }
        return TemplateResponse(request, DETAIL_TEMPLATE, context, status=status)


class OverrideSetView(MedalScreenMixin, ThrottledFormMixin, View):
    """``POST /coordinator/medals/<etap>/overrides/`` – ręczna nagroda z uzasadnieniem."""

    throttle_scope = "medals"

    def post(self, request, stage_id: int):
        stage = self.stage_or_404(request, stage_id)
        form = OverrideForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Ręczna nagroda wymaga wyboru nagrody i uzasadnienia.")
            return self.back(stage.pk)
        scheme = service.scheme_for(stage, create=True)
        try:
            service.set_override(
                scheme,
                entry_id=form.cleaned_data["entry_id"],
                award=form.cleaned_data["award"],
                justification=form.cleaned_data["justification"],
                actor=request.user,
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, "Ręczna nagroda zapisana.")
        return self.back(stage.pk)


class OverrideRemoveView(MedalScreenMixin, View):
    """``POST /coordinator/medals/<etap>/overrides/<wpis>/remove/`` – powrót do nagrody wyliczonej."""

    def post(self, request, stage_id: int, entry_id: int):
        stage = self.stage_or_404(request, stage_id)
        scheme = service.scheme_for(stage, create=True)
        try:
            service.remove_override(scheme, entry_id=entry_id, actor=request.user, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, "Ręczna nagroda usunięta – obowiązuje nagroda wyliczona.")
        return self.back(stage.pk)


class FreezeView(MedalScreenMixin, ThrottledFormMixin, View):
    """``POST /coordinator/medals/<etap>/freeze/`` – ogłoszenie medali."""

    throttle_scope = "medals"

    def post(self, request, stage_id: int):
        stage = self.stage_or_404(request, stage_id)
        scheme = service.scheme_for(stage, create=True)
        try:
            service.freeze(scheme, actor=request.user, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, "Medale ogłoszone. Strona medali i ranking krajów są publiczne.")
        return self.back(stage.pk)


class UnfreezeView(MedalScreenMixin, ThrottledFormMixin, View):
    """``POST /coordinator/medals/<etap>/unfreeze/`` – zdjęcie ogłoszenia z uzasadnieniem."""

    throttle_scope = "medals"

    def post(self, request, stage_id: int):
        stage = self.stage_or_404(request, stage_id)
        form = UnfreezeForm(request.POST)
        scheme = service.scheme_for(stage, create=True)
        if not form.is_valid():
            messages.error(request, "Odmrożenie wymaga uzasadnienia.")
            return self.back(stage.pk)
        try:
            service.unfreeze(
                scheme, justification=form.cleaned_data["justification"], actor=request.user, request=request
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(
                request, "Medale odmrożone – strona publiczna jest niedostępna do ponownego ogłoszenia."
            )
        return self.back(stage.pk)


class IssueCertificatesView(MedalScreenMixin, ThrottledFormMixin, View):
    """``POST /coordinator/medals/<etap>/certificates/`` – dyplomy medalowe i zaświadczenia o udziale."""

    throttle_scope = "medals"

    def post(self, request, stage_id: int):
        stage = self.stage_or_404(request, stage_id)
        form = IssueForm(request.POST)
        form.is_valid()
        scheme = service.scheme_for(stage, create=True)
        try:
            report = service.issue_certificates(
                scheme,
                participation=bool(form.cleaned_data.get("participation")),
                actor=request.user,
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(
                request,
                f"Dokumenty gotowe: medalowe {report.medals}, zaświadczenia o udziale {report.participation} "
                f"(nowych: {report.created}).",
            )
            if report.stale:
                messages.warning(
                    request,
                    "Dyplomy niezgodne z ogłoszoną nagrodą (wystawione przed zmianą medali): "
                    + ", ".join(report.stale[:20]),
                )
        return self.back(stage.pk)


class CertificatesZipView(MedalScreenMixin, ThrottledFormMixin, View):
    """``GET /coordinator/medals/<etap>/certificates.zip`` – paczka dokumentów etapu."""

    throttle_scope = "medals"
    throttle_methods = ("GET",)

    def get(self, request, stage_id: int):
        stage = self.stage_or_404(request, stage_id)
        scheme = service.scheme_for(stage)
        certificates = service.stage_certificates(scheme)
        if not certificates:
            messages.error(request, "Nie ma jeszcze żadnych dokumentów – najpierw je wystaw.")
            return self.back(stage.pk)
        archive = build_certificates_zip(certificates)
        if scheme.pk is not None:
            service.audit_export(scheme, "zip", archive.count, actor=request.user, request=request)
        return FileResponse(
            archive.stream,
            as_attachment=True,
            filename=f"medale-{slugify(stage.display_name)}.zip",
            content_type="application/zip",
        )


class _ExportMixin(MedalScreenMixin):
    def frozen_or_back(self, request, stage_id: int):
        stage = self.stage_or_404(request, stage_id)
        scheme = service.scheme_for(stage)
        if not scheme.is_frozen:
            messages.error(request, "Eksport jest dostępny po ogłoszeniu medali.")
            return stage, scheme, self.back(stage.pk)
        return stage, scheme, None


class ExportCsvView(_ExportMixin, View):
    """``GET /coordinator/medals/<etap>/export.csv`` – pełna lista z nazwiskami (dane osobowe, audyt)."""

    def get(self, request, stage_id: int):
        stage, scheme, refusal = self.frozen_or_back(request, stage_id)
        if refusal is not None:
            return refusal
        rows = service.ceremony_rows(scheme)
        service.audit_export(scheme, "csv", len(rows), actor=request.user, request=request)
        return csv_response(exports.csv_dataset(rows, filename=f"medale-{slugify(stage.display_name)}"))


class ExportPdfView(_ExportMixin, ThrottledFormMixin, View):
    """``GET /coordinator/medals/<etap>/ceremony.pdf`` – lista wręczeń na galę."""

    throttle_scope = "medals"
    throttle_methods = ("GET",)

    def get(self, request, stage_id: int):
        stage, scheme, refusal = self.frozen_or_back(request, stage_id)
        if refusal is not None:
            return refusal
        rows = [row for row in service.ceremony_rows(scheme) if row["award"] != Award.NONE]
        competition = stage.edition.competition
        pdf = exports.ceremony_pdf(
            rows,
            title=f"{competition.name} – {stage.edition.year_label}",
            subtitle=f"Lista wręczeń: {stage.display_name}",
        )
        service.audit_export(scheme, "pdf", len(rows), actor=request.user, request=request)
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="gala-{slugify(stage.display_name)}.pdf"'
        return response


# --- strony publiczne ----------------------------------------------------------------------------


class PublicMedalsView(View):
    """``/results/<etap>/medals/`` – ogłoszone medale z filtrem kraju. Bez logowania."""

    def get(self, request, stage_id: int):
        scheme = service.published_scheme(getattr(request, "competition", None), stage_id)
        rows = list(scheme.public_rows or [])
        countries = sorted(
            {(row["country_code"], row["country"]) for row in rows if row.get("country_code")},
            key=lambda item: item[1].casefold(),
        )
        selected = (request.GET.get("country") or "").strip()
        if selected and selected not in {code for code, _name in countries}:
            selected = ""
        if selected:
            rows = [row for row in rows if row.get("country_code") == selected]
        summary = next((item for item in scheme.country_table or [] if item["code"] == selected), None)
        context = {
            "scheme": scheme,
            "stage": scheme.stage,
            "rows": rows,
            "countries": countries,
            "selected": selected,
            "summary": summary if selected else None,
            "awards": Award,
            "medal_rows": sum(1 for row in rows if row["award"] != Award.NONE),
        }
        return TemplateResponse(request, PUBLIC_TEMPLATE, context)


class PublicCountriesView(View):
    """``/results/<etap>/countries/`` – nieoficjalny ranking krajów: wyłącznie agregaty."""

    def get(self, request, stage_id: int):
        scheme = service.published_scheme(getattr(request, "competition", None), stage_id)
        table = list(scheme.country_table or [])
        by_medals = request.GET.get("sort") == "medals"
        context = {
            "scheme": scheme,
            "stage": scheme.stage,
            "rows": medal_order(table) if by_medals else table,
            "by_medals": by_medals,
            "awards": Award,
        }
        return TemplateResponse(request, COUNTRIES_TEMPLATE, context)
