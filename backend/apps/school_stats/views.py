"""Ekrany statystyk szkół: panel opiekuna (``/supervisor/statistics/``) i koordynatora.

Widoki wyłącznie orkiestrują – reguły publikacji, progu k-anonimowości i weryfikacji szkoły stoją
w ``apps.school_stats.services`` i ekran nie ma jak ich obejść, nawet dopisując kolumnę.

Bramka flagi jest w ``dispatch`` każdego widoku (**404**, nie 403): przy wyłączonym przełączniku
adres ma nie istnieć, tak samo jak pozostałe ekrany za flagami (``docs/UNIWERSALNY-ETAP-2.md``
§ 2.1). Kolejność: najpierw rola (mixin – niezalogowany dostaje przekierowanie na logowanie),
potem flaga – dzięki temu z zewnątrz nie da się nawet sprawdzić, czy konkurs funkcję włączył.

Wszystkie widoki są GET-ami i niczego nie zmieniają. Pobrania (CSV, PDF) zostawiają wpis w audycie
bez danych osobowych – tak samo jak pozostałe eksporty panelu.
"""

from __future__ import annotations

from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _
from django.views.generic import TemplateView, View

from apps.competitions.models import Edition
from apps.competitions.services import current_edition
from apps.core import exports
from apps.core.models import audit
from apps.schools.models import School
from apps.web.mixins import CoordinatorRequiredMixin
from apps.web.supervisor_mixins import SupervisorRequiredMixin

from . import services
from .charts import line_chart
from .grouping import axis_for
from .pdf import compose_pdf, pdf_filename

SUPERVISOR_TEMPLATE = "web/school_stats/supervisor.html"
COORDINATOR_TEMPLATE = "web/school_stats/coordinator.html"


class _FlagMixin:
    """Bramka flagi ``school_statistics`` – wspólna dla obu paneli. Wołana po bramce roli."""

    def flag_or_404(self):
        if not services.enabled(self.competition):
            raise Http404("Ten konkurs nie ma statystyk szkół.")

    def chosen_edition(self) -> Edition | None:
        """Edycja z ``?edition=<id>`` – wyłącznie spośród edycji **tego** konkursu – albo bieżąca.

        Identyfikator cudzej edycji to 404, a nie cicha zamiana na bieżącą: adres z cudzym numerem
        jest albo pomyłką, albo próbą, i w obu przypadkach odpowiedź „nie ma” jest uczciwsza.
        """
        raw = self.request.GET.get("edition", "")
        editions = Edition.objects.filter(competition=self.competition)
        if raw:
            if not raw.isdigit():
                raise Http404("Nie ma takiej edycji.")
            return get_object_or_404(editions, pk=int(raw))
        return current_edition(self.competition) or editions.order_by("-created_at", "-id").first()


class SupervisorStatisticsView(_FlagMixin, SupervisorRequiredMixin, TemplateView):
    """``/supervisor/statistics/`` – uczniowie opiekuna w edycji, porównania i postęp przez edycje."""

    template_name = SUPERVISOR_TEMPLATE

    def get(self, request, *args, **kwargs):
        self.flag_or_404()
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        supervisor = self.supervisor
        edition = self.chosen_edition()
        axis = axis_for(self.competition)
        data = services.supervisor_statistics(supervisor, self.competition, edition, axis=axis)
        progress = data["progress"]
        labels = {
            "mine": _("Moi uczniowie"),
            "school": _("Szkoła"),
            "region": axis.region_label(data["scope"]["region"]) or _("Region"),
            "all": _("Wszyscy uczestnicy"),
        }
        chart = line_chart(
            progress["labels"],
            [
                {"key": key, "label": labels[key], "values": values}
                for key, values in progress["series"].items()
                if any(value is not None for value in values)
            ],
        )
        context.update(
            {
                **data,
                "supervisor": supervisor,
                "chart": chart,
                "series_labels": labels,
                "k_anonymity": services.K_ANONYMITY,
                "report_available": edition is not None and data["scope"]["school_key"] is not None,
            }
        )
        return context


class SupervisorReportView(_FlagMixin, SupervisorRequiredMixin, View):
    """``/supervisor/statistics/report.pdf`` – raport **własnej, zweryfikowanej** szkoły opiekuna."""

    def get(self, request):
        self.flag_or_404()
        supervisor = self.supervisor
        edition = self.chosen_edition()
        # Profil z innego konkursu (albo bez konkursu) nie jest zweryfikowany **tutaj** (M2) – ta sama
        # reguła, co ``services.supervisor_scope``, więc przycisk i adres nie mogą się rozjechać.
        verified = supervisor.verified and supervisor.competition_id == getattr(self.competition, "pk", None)
        if edition is None or not (supervisor.school_ref_id and verified):
            raise Http404("Raport szkoły wymaga szkoły z wykazu zweryfikowanej przez organizatora.")
        from apps.accounts.supervisors import students_of

        report = services.school_report(
            supervisor.school_ref,
            edition,
            own_participant_ids=[student.pk for student in students_of(supervisor)],
            axis=axis_for(self.competition),
        )
        return _pdf_response(request, report, self.competition)


class CoordinatorStatisticsView(_FlagMixin, CoordinatorRequiredMixin, TemplateView):
    """``/coordinator/school-stats/`` – ranking szkół, województwa i szkoły „do odzyskania”."""

    template_name = COORDINATOR_TEMPLATE

    def get(self, request, *args, **kwargs):
        self.flag_or_404()
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        edition = self.chosen_edition()
        sort = self.request.GET.get("sort", "participants")
        sort = sort if sort in services.SORTS else "participants"
        data = (
            services.coordinator_statistics(edition, sort=sort, axis=axis_for(self.competition))
            if edition is not None
            else {}
        )
        context.update(
            {
                **data,
                "edition": edition,
                "editions": list(
                    Edition.objects.filter(competition=self.competition).order_by("-created_at", "-id")
                ),
                "sort": sort,
                "sorts": [
                    ("participants", _("liczba uczestników")),
                    ("results", _("wyniki")),
                    ("name", _("nazwa")),
                ],
                "k_anonymity": services.K_ANONYMITY,
            }
        )
        return context


class CoordinatorExportView(_FlagMixin, CoordinatorRequiredMixin, View):
    """``/coordinator/school-stats/export.csv`` – ranking szkół w CSV, z tym samym progiem co ekran."""

    def get(self, request):
        self.flag_or_404()
        edition = self.chosen_edition()
        if edition is None:
            raise Http404("Ten konkurs nie ma jeszcze żadnej edycji.")
        dataset = services.ranking_dataset(edition, axis=axis_for(self.competition))
        audit(
            request.user,
            "export.generated",
            edition,
            {"kind": "school_statistics", "format": "csv", "rows": dataset.count},
            request=request,
        )
        return exports.build_response(dataset, "csv")


class CoordinatorLostSchoolsExportView(_FlagMixin, CoordinatorRequiredMixin, View):
    """``/coordinator/school-stats/lost.csv`` – szkoły „do odzyskania” (adresaci akcji promocyjnej)."""

    def get(self, request):
        self.flag_or_404()
        edition = self.chosen_edition()
        if edition is None:
            raise Http404("Ten konkurs nie ma jeszcze żadnej edycji.")
        dataset = services.lost_schools_dataset(edition, axis=axis_for(self.competition))
        audit(
            request.user,
            "export.generated",
            edition,
            {"kind": "school_statistics_lost", "format": "csv", "rows": dataset.count},
            request=request,
        )
        return exports.build_response(dataset, "csv")


class CoordinatorReportView(_FlagMixin, CoordinatorRequiredMixin, View):
    """``/coordinator/school-stats/schools/<id>/report.pdf`` – raport szkoły z rankingu.

    Szkoła musi mieć w tej edycji uczestników **tego** konkursu: wykaz szkół jest wspólny dla
    instalacji, więc bez tego warunku adres z dowolnym numerem dawałby raport szkoły, której ten
    organizator w ogóle nie zna (choćby pusty – sam fakt odpowiedzi byłby informacją).
    """

    def get(self, request, school_id: int):
        self.flag_or_404()
        edition = self.chosen_edition()
        if edition is None:
            raise Http404("Ten konkurs nie ma jeszcze żadnej edycji.")
        axis = axis_for(self.competition)
        if f"s{school_id}" not in services.edition_summary(edition, axis).groups:
            raise Http404("Ta szkoła nie ma uczestników w tej edycji.")
        school = get_object_or_404(School, pk=school_id)
        # M4: raport trafia do szkoły, więc odbiorca zna wyniki uczniów **wszystkich** jej opiekunów –
        # wobec ich sumy liczymy dopełnienie i zagnieżdżenie.
        report = services.school_report(
            school,
            edition,
            own_participant_ids=services.school_supervisor_participants(self.competition.pk, school.pk),
            axis=axis,
        )
        return _pdf_response(request, report, self.competition)


def _pdf_response(request, report: dict, competition):
    """Plik PDF raportu i wpis audytowy (szkoła, edycja) – bez danych osobowych."""
    school, edition = report["school"], report["edition"]
    audit(
        request.user,
        "school_stats.report_downloaded",
        edition,
        {"school_id": school.pk, "edition_id": edition.pk},
        request=request,
    )
    return FileResponse(
        iter([compose_pdf(report, competition)]),
        content_type="application/pdf",
        as_attachment=True,
        filename=pdf_filename(school, edition),
    )
