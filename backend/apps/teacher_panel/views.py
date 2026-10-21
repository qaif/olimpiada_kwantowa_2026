"""Ekrany panelu nauczyciela: „Postęp klasy”, jego eksport i „Szkoła” (UX-PANEL-02 § 4–5, D3).

Pulpit „Start” (``/supervisor/``) stoi dalej w ``apps.web.views.supervisor.SupervisorDashboardView`` –
pod tym adresem i nazwą prowadzą przekierowanie po zalogowaniu i pasek konta – a karmi go
:func:`apps.teacher_panel.services.start_summary`.

Dostęp: ``SupervisorRequiredMixin`` (rola **i** profil opiekuna w konkursie z żądania), lista uczniów
wyłącznie z ``students_of`` (w serwisie). Widoki niczego nie zapisują poza wpisem audytu eksportu.
"""

from __future__ import annotations

from django.http import Http404
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView

from apps.accounts.supervisors import has_confirmed
from apps.competitions.services import current_edition
from apps.core import exports
from apps.core.models import audit
from apps.web.supervisor_mixins import SupervisorRequiredMixin

from . import services

SORT_CHOICES = (
    ("name", _("nazwisko: A–Z")),
    ("-name", _("nazwisko: Z–A")),
    ("grade", _("klasa: rosnąco")),
    ("-grade", _("klasa: malejąco")),
    ("account", _("konto: najpierw bez konta")),
    ("-work", _("najwięcej oddanych prac")),
    ("work", _("najmniej oddanych prac")),
)


class _ProgressMixin(SupervisorRequiredMixin):
    def progress_and_rows(self):
        progress = services.class_progress(self.supervisor, self.competition)
        filters = services.parse_filters(self.request.GET, progress)
        return progress, filters, services.apply_filters(progress, filters)


class ClassProgressView(_ProgressMixin, TemplateView):
    """``/supervisor/progress/`` – tabela uczniów z etapami, filtry, sortowanie, przyciski eksportu."""

    template_name = "teacher_panel/progress.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        progress, filters, rows = self.progress_and_rows()
        query = filters.query()
        suffix = f"?{query}" if query else ""
        context.update(
            {
                "supervisor": self.supervisor,
                "progress": progress,
                "filters": filters,
                "rows": rows,
                "account_choices": list(services.ACCOUNT_LABELS.items()),
                "certificate_choices": list(services.CERTIFICATE_LABELS.items()),
                "sort_choices": SORT_CHOICES,
                "export_csv": reverse("web:supervisor-progress-export", args=["csv"]) + suffix,
                "export_xlsx": reverse("web:supervisor-progress-export", args=["xlsx"]) + suffix,
            }
        )
        return context


class ClassProgressExportView(_ProgressMixin, View):
    """``/supervisor/progress/export.<csv|xlsx>`` – te same kolumny i ten sam filtr, co ekran (D4, D8).

    Wpis ``export.generated`` powstaje **przed** oddaniem pliku (``apps.core.exports``) i niesie rodzaj,
    format i liczbę wierszy – nigdy dane. Celem wpisu jest profil opiekuna: to jego lista wyszła z serwisu.
    """

    def get(self, request, fmt: str):
        if fmt not in exports.FORMATS:
            raise Http404("Nieznany format eksportu.")
        progress, filters, rows = self.progress_and_rows()
        dataset = services.export_dataset(progress, rows)
        audit(
            request.user,
            "export.generated",
            self.supervisor,
            {
                "kind": "supervisor_class_progress",
                "format": fmt,
                "rows": dataset.count,
                "filtered": filters.active,
                "details": progress.details,
            },
            request=request,
        )
        return exports.build_response(dataset, fmt)


class SchoolView(SupervisorRequiredMixin, TemplateView):
    """``/supervisor/school/`` – udział szkoły w edycji, własne zaświadczenia, statystyki i plakaty."""

    template_name = "teacher_panel/school.html"

    def get_context_data(self, **kwargs):
        from apps.results.models import Certificate
        from apps.school_stats.services import enabled as school_stats_enabled

        context = super().get_context_data(**kwargs)
        supervisor = self.supervisor
        edition = current_edition(self.competition)
        context.update(
            {
                "supervisor": supervisor,
                "edition": edition,
                "confirmed": has_confirmed(supervisor, edition),
                "statistics": school_stats_enabled(self.competition),
                "certificates": list(
                    Certificate.objects.for_competition(self.competition)
                    .filter(supervisor=supervisor)
                    .select_related("edition")
                    .order_by("-issued_at", "-id")
                ),
            }
        )
        return context
