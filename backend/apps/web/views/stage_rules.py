"""Publiczne strony „Zasad Organizacji Zawodów” etapu (ZOZ-01): ``/dokumenty/zasady/<etap>/``.

Trzy adresy, wszystkie pod sekcją dokumentów (``dokumenty/zasady`` jest w manifeście tras djcms
adresem aplikacji pod stroną redakcyjną):

- ``/dokumenty/zasady/<etap>/`` – wersja **obowiązująca** (ostatnio opublikowana),
- ``/dokumenty/zasady/<etap>/wersja/<wersja>/`` – konkretna opublikowana wersja (historia); stara
  wersja dostaje ramkę „to nie jest wersja obowiązująca” z odnośnikiem do bieżącej,
- ``/dokumenty/zasady/<etap>/wersja/<wersja>/pdf/`` – plik PDF wersji, jeśli go wgrano.

Etap szukamy **w konkursie żądania** (``Stage.objects.for_competition``): etap cudzego konkursu,
projekt i wersja wycofana dają to samo 404 – odpowiedź nie mówi, czy taki dokument istnieje.
Treść jest oczyszczana także przy wyświetleniu (pierwszy raz – przy zapisie w serwisie), więc
wiersz zmieniony z pominięciem serwisu nie wniesie na stronę niczego poza strukturą dokumentu.
"""

from __future__ import annotations

import os

from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.utils.safestring import mark_safe
from django.views.generic import View

from apps.competitions.models import Stage, StageKind
from apps.competitions.stage_rules import (
    StageRules,
    chapters,
    current_rules_by_stage,
    published,
    sanitize_html,
)

TEMPLATE = "web/stage_rules/document.html"


def _stage(request, stage_id: int) -> Stage:
    return get_object_or_404(
        Stage.objects.for_competition(request.competition).select_related("edition"), pk=stage_id
    )


def render_rules(request, stage: Stage, rules: StageRules, *, versions=None, preview: bool = False):
    """Strona dokumentu – wspólna dla widoku publicznego i podglądu koordynatora."""
    body = sanitize_html(rules.body_html)
    versions = versions if versions is not None else []
    current = versions[0] if versions else None
    # „Dokumenty powiązane”: ZOZ pozostałych etapów tej samej edycji – dwa zapytania niezależnie
    # od liczby etapów.
    siblings = list(
        Stage.objects.filter(edition_id=stage.edition_id)
        .exclude(pk=stage.pk)
        .exclude(kind=StageKind.TRAINING)
        .order_by("opens_at", "id")
    )
    other_rules = current_rules_by_stage([item.pk for item in siblings])
    other_stages = [item for item in siblings if item.pk in other_rules]
    context = {
        "stage": stage,
        "rules": rules,
        "body": mark_safe(body),  # noqa: S308 - wynik ``sanitize_html``: lista dozwolonych znaczników
        "chapters": chapters(body),
        "versions": versions,
        "current": current,
        "is_current": current is not None and current.pk == rules.pk,
        "other_rules": [{"stage": item, "rules": other_rules[item.pk]} for item in other_stages],
        "preview": preview,
    }
    return TemplateResponse(request, TEMPLATE, context)


class StageRulesView(View):
    """Wersja obowiązująca albo – z ``version`` w adresie – wskazana wersja opublikowana."""

    def get(self, request, stage_id: int, version: str | None = None):
        stage = _stage(request, stage_id)
        versions = list(published(StageRules.objects.filter(stage=stage)))
        if not versions:
            raise Http404("Ten etap nie ma opublikowanych Zasad Organizacji Zawodów.")
        if version is None:
            rules = versions[0]
        else:
            rules = next((item for item in versions if item.version == version), None)
            if rules is None:
                raise Http404("Nie ma takiej wersji dokumentu.")
        return render_rules(request, stage, rules, versions=versions)


class StageRulesPdfView(View):
    """Plik PDF opublikowanej wersji – strumieniowany z prywatnego storage przez aplikację."""

    def get(self, request, stage_id: int, version: str):
        stage = _stage(request, stage_id)
        rules = published(StageRules.objects.filter(stage=stage, version=version)).first()
        if rules is None or not rules.pdf:
            raise Http404("Ta wersja dokumentu nie ma pliku PDF.")
        return pdf_response(rules)


def pdf_response(rules: StageRules) -> FileResponse:
    return FileResponse(
        rules.pdf.open("rb"),
        content_type="application/pdf",
        as_attachment=False,
        filename=os.path.basename(rules.pdf.name) or f"zoz-{rules.version}.pdf",
    )
