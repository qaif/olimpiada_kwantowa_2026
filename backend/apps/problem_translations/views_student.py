"""Zadanie w języku ucznia (TR-01 § 2) – wyłącznie po otwarciu etapu i wyłącznie wersja zatwierdzona.

Bramka jest w ``services.problem_for_student``: zadanie z konkursu żądania, etap otwarty, edycja
delegacji ucznia, język ucznia (nadpisanie opiekuna albo język 1 delegacji) i zatwierdzona migawka.
Każda z tych rzeczy z osobna daje 404 – uczeń nie dowiaduje się, czy tłumaczenie istnieje, zanim
zawody się zaczną. Wersja oficjalna zostaje tam, gdzie była (``competitions:problem-statement``).
"""

from __future__ import annotations

from django.http import Http404
from django.template.response import TemplateResponse
from django.views.generic import View

from apps.web.mixins import ParticipantRequiredMixin

from . import languages
from . import services as service
from .markup import render
from .models import TranslationKind
from .views_leader import pdf_response, private

STUDENT_TEMPLATE = "problem_translations/student/problem.html"


class StudentMixin(ParticipantRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and self.has_role(request.user):
            service.require_enabled(self.competition)
        return super().dispatch(request, *args, **kwargs)


class StudentProblemView(StudentMixin, View):
    """``/me/translations/problems/<pk>/`` – treść zadania w języku ucznia."""

    def get(self, request, pk: int):
        problem, revision = service.problem_for_student(self.participant, pk)
        service.record_view(
            request.user, "student_viewed", revision.translation, request=request, revision=revision.number
        )
        language = revision.translation.language
        context = {
            "problem": problem,
            "revision": revision,
            "language": language,
            "language_label": languages.native_name(language),
            "rtl": languages.is_rtl(language),
            "html": render(revision.body_md) if revision.kind == TranslationKind.TEXT else "",
            # Uczeń dostał drugi język drużyny, bo w przypisanym nie ma zatwierdzonego tłumaczenia.
            "fallback_from": (
                languages.native_name(service.student_language(self.participant))
                if service.is_fallback(self.participant, revision)
                else ""
            ),
            # Wersja oficjalna zmieniła się po zatwierdzeniu tego tłumaczenia (TR-01, M5).
            "stale": service.is_stale(revision, problem),
        }
        return private(TemplateResponse(request, STUDENT_TEMPLATE, context))


class StudentProblemPdfView(StudentMixin, View):
    """PDF tłumaczenia dla ucznia (po otwarciu etapu – bez znaku wodnego, z wpisem w dzienniku)."""

    def get(self, request, pk: int):
        problem, revision = service.problem_for_student(self.participant, pk)
        if revision.kind != TranslationKind.PDF or not revision.pdf:
            raise Http404
        service.record_view(
            request.user,
            "student_downloaded",
            revision.translation,
            request=request,
            revision=revision.number,
        )
        with revision.pdf.open("rb") as handle:
            data = handle.read()
        return pdf_response(data, f"problem-{problem.number}-{revision.translation.language}.pdf")
