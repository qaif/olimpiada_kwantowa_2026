"""Ekrany STATS-02: lejek uczestnika (z CSV) i raport wysyłki komunikatu.

Widoki tylko orkiestrują – liczby liczą ``funnel`` i ``broadcasts``. Wszystkie są GET-ami za rolą
koordynatora **tego** konkursu; edycja i komunikat są szukane w jego obrębie, a cudzy numer to 404
(nie 403 – z zewnątrz nie da się nawet sprawdzić, czy taki obiekt istnieje).
"""

from __future__ import annotations

from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _
from django.views.generic import TemplateView, View

from apps.accounts.models import MessageBroadcast
from apps.competitions.models import Edition
from apps.competitions.services import current_edition
from apps.core import exports
from apps.core.models import audit
from apps.school_stats.services import K_ANONYMITY
from apps.web.mixins import CoordinatorRequiredMixin

from . import broadcasts, funnel
from .forms import FunnelFilterForm

FUNNEL_TEMPLATE = "web/coordinator_stats/funnel.html"
REPORT_TEMPLATE = "web/coordinator_stats/broadcast_report.html"


class _EditionMixin:
    def chosen_edition(self) -> Edition | None:
        """Edycja z ``?edition=<id>`` – wyłącznie spośród edycji **tego** konkursu – albo bieżąca.

        Ta sama reguła, co na ekranie statystyk szkół (STAT-01): cudzy numer to 404, a nie cicha
        zamiana na bieżącą.
        """
        raw = self.request.GET.get("edition", "")
        editions = Edition.objects.filter(competition=self.competition)
        if raw:
            if not raw.isdigit():
                raise Http404("Nie ma takiej edycji.")
            return get_object_or_404(editions, pk=int(raw))
        return current_edition(self.competition) or editions.order_by("-created_at", "-id").first()

    def funnel_data(self):
        edition = self.chosen_edition()
        # Formularz zawsze związany (pusty ``GET`` to „bez filtrów”), więc ``cleaned_data`` istnieje.
        form = FunnelFilterForm(self.request.GET, competition=self.competition)
        if not form.is_valid():
            return edition, form, None
        result = funnel.cached(self.competition, edition, form.filters()) if edition is not None else None
        return edition, form, result


class FunnelView(_EditionMixin, CoordinatorRequiredMixin, TemplateView):
    """``/coordinator/stats/funnel/`` – lejek, filtry i „gdzie odpadają”."""

    template_name = FUNNEL_TEMPLATE

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        edition, form, result = self.funnel_data()
        context.update(
            {
                "edition": edition,
                "editions": list(
                    Edition.objects.filter(competition=self.competition).order_by("-created_at", "-id")
                ),
                "form": form,
                "result": result,
                "k_anonymity": K_ANONYMITY,
                "export_query": self.request.GET.urlencode(),
            }
        )
        return context


class FunnelExportView(_EditionMixin, CoordinatorRequiredMixin, View):
    """``/coordinator/stats/funnel/export.csv`` – te same agregaty, co ekran; bez danych osobowych."""

    def get(self, request):
        edition, form, result = self.funnel_data()
        if edition is None or result is None:
            raise Http404("Nie ma edycji albo filtry są niepoprawne.")
        rows = _csv_rows(edition, form, result)
        dataset = exports.Dataset(
            header=[
                _("Sekcja"),
                _("Pozycja"),
                _("Liczba"),
                _("Odsetek względem rejestracji (%)"),
                _("Odsetek aktywowanych kont w grupie (%)"),
            ],
            rows=iter(rows),
            count=len(rows),
            title=_("Lejek uczestnika"),
            filename=f"lejek-uczestnika-{edition.pk}",
        )
        audit(
            request.user,
            "export.generated",
            edition,
            {"kind": "participant_funnel", "format": "csv", "rows": dataset.count},
            request=request,
        )
        return exports.build_response(dataset, "csv")


def _csv_rows(edition, form, result) -> list[list]:
    """Wiersze CSV: filtry, kroki (każdy niezależnie), potem obie tabele „gdzie odpadają”.

    Kroki mają odsetek względem rejestracji (czwarta kolumna), grupy „gdzie odpadają” – odsetek
    aktywowanych wśród zarejestrowanych **w grupie** (piąta), żeby jedna kolumna nie znaczyła dwóch rzeczy.
    """
    filters_section, steps_section = _("Filtry"), _("Kroki aktywności")
    rows: list[list] = [[filters_section, _("Edycja"), edition.year_label, None, None]]
    rows += [[filters_section, label, value, None, None] for label, value in form.description()]
    for step in result.steps:
        label = ("— " if step["sub"] else "") + step["label"]
        rows.append([steps_section, label, step["count"], step["vs_registered"], None])
    for section, items in (
        (_("Województwa – najniższa aktywacja"), result.regions),
        (_("Szkoły – najniższa aktywacja"), result.schools),
    ):
        for item in items:
            rows.append([section, item["label"], item["registered"], None, item["activation"]])
    return rows


class BroadcastReportView(CoordinatorRequiredMixin, TemplateView):
    """``/coordinator/messages/<id>/report/`` – raport wysyłki jednego komunikatu tego konkursu."""

    template_name = REPORT_TEMPLATE

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        broadcast = get_object_or_404(
            MessageBroadcast.objects.for_competition(self.competition).select_related("created_by"),
            pk=kwargs["pk"],
        )
        context.update({"broadcast": broadcast, "report": broadcasts.broadcast_report(broadcast)})
        return context
