"""Narzędzia panelu recenzenta: wzorcówka zadania, porównanie ocen i wątek notatek.

Osobny moduł od ``reviewer.py`` z tego samego powodu, dla którego koordynator ma
``coordinator_stages.py``: tam jest ocenianie jednej pracy (formularz, PDF, adnotacje), tutaj –
materiały i rozmowa wokół niej. Reguły domenowe stoją w ``apps.grading`` (``comparison``,
``rubric``, ``deadlines``), widoki wyłącznie orkiestrują.

Najważniejsza reguła tego modułu dotyczy wzorcówki: ``Problem.model_solution_pdf`` nie ma i nie
może mieć publicznego adresu. Wydaje ją wyłącznie ``ProblemModelSolutionView`` – aktywnemu
członkowi komitetu albo koordynatorowi. Uczestnik dostaje 403 (zła rola), a brak pliku to 404.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.accounts.models import GROUP_COORDINATOR
from apps.accounts.services import active_reviewer_profile
from apps.competitions.models import Problem
from apps.core.api import DomainError
from apps.grading.comparison import add_review_note, can_read_notes, comparison_context
from apps.grading.services import reviews_for_reviewer
from apps.web.mixins import ReviewerRequiredMixin, RoleRequiredMixin


class CommitteeMaterialsMixin(RoleRequiredMixin):
    """Dostęp do materiałów oceniających: aktywny członek komitetu **albo** koordynator.

    Szersze niż ``ReviewerRequiredMixin`` o jedną rolę i to jest cała różnica: wzorcówkę wgrywa
    koordynator i musi móc sprawdzić, co wgrał, a czyta ją recenzent. Nikt inny – w szczególności
    uczestnik, który o istnieniu tego adresu i tak się nie dowie, ale trafiwszy na niego dostaje
    403, a nie plik z kluczem odpowiedzi w trakcie zawodów.
    """

    role_denied_message = "Rozwiązania wzorcowe są dostępne wyłącznie dla komitetu."

    def has_role(self, user) -> bool:
        if active_reviewer_profile(user) is not None:
            return True
        return bool(user.is_active and user.groups.filter(name=GROUP_COORDINATOR).exists())


class ProblemModelSolutionView(CommitteeMaterialsMixin, View):
    """``GET /review/problems/<id>/model-solution/`` – wzorcówka zadania w PDF.

    Plik idzie przez aplikację, a nie przez adres storage: leży w prywatnym buckecie i ma nie mieć
    żadnej drogi na zewnątrz poza tym widokiem. Brak pliku to 404 – zadanie bez wzorcówki jest
    zwyczajną sytuacją, a nie odmową dostępu.
    """

    def get(self, request, pk: int):
        problem = get_object_or_404(Problem.objects.select_related("stage"), pk=pk)
        if not problem.model_solution_pdf:
            raise Http404("Zadanie nie ma rozwiązania wzorcowego.")
        return FileResponse(
            problem.model_solution_pdf.open("rb"),
            content_type="application/pdf",
            as_attachment=False,
            filename=f"wzorcowka-zadanie-{problem.number}.pdf",
        )


class ReviewerScopedMixin(ReviewerRequiredMixin):
    """Własne przydziały zalogowanego recenzenta – cudza recenzja to 404, nie 403."""

    def get_review(self, pk: int):
        return get_object_or_404(reviews_for_reviewer(self.reviewer), pk=pk)


class ReviewCompareView(ReviewerScopedMixin, View):
    """``GET /review/<id>/compare/`` – porównanie ocen i wątek notatek na osobnej stronie.

    Ten sam materiał jest na stronie oceny (sekcja „Porównanie ocen”), ale po wystawieniu oceny
    recenzent wraca tu zwykle po jedno: przeczytać, co napisała druga strona, i odpowiedzieć.
    Osobny adres oszczędza mu ładowania PDF-a i całego formularza oceny, a przy okazji jest tym,
    co da się wysłać w linku koordynatorowi („zobacz wątek przy tej pracy”).

    Gdy ocen jeszcze nie odsłonięto, strona istnieje, ale mówi wprost, że jest za wcześnie –
    przekierowanie ukrywałoby fakt, że sekcja w ogóle jest przewidziana.
    """

    template_name = "web/reviewer/compare.html"

    def get(self, request, pk: int):
        review = self.get_review(pk)
        context = {
            "review": review,
            "submission": review.submission,
            "problem": review.submission.problem,
            "public_code": review.submission.entry.participant.public_code,
            "comparison": comparison_context(review),
        }
        return TemplateResponse(request, self.template_name, context)


class ReviewNoteCreateView(ReviewerScopedMixin, View):
    """``POST /review/<id>/notes/`` – dopisanie notatki do wątku przy pracy.

    Treść nie przechodzi przez formularz Django świadomie: jedyną regułą jest „niepusta i nie
    dłuższa niż limit”, a stoi ona w ``comparison.add_review_note`` razem z regułami, które
    formularz i tak musiałby powtórzyć (czy wolno pisać w tym stanie pracy, czy piszący jest jej
    recenzentem). Dwie kopie tej samej walidacji rozjeżdżają się przy pierwszej zmianie.

    Powrót idzie tam, skąd przyszło żądanie – ze strony oceny na stronę oceny, z porównania na
    porównanie: recenzent ma zobaczyć swoją notatkę w kontekście, w którym ją pisał.
    """

    def post(self, request, pk: int):
        review = self.get_review(pk)
        submission = review.submission
        if not can_read_notes(submission, self.reviewer):  # pragma: no cover - queryset już to bramkuje
            messages.error(request, "Nie recenzujesz tej pracy.")
            return redirect(reverse("web:review-detail", kwargs={"pk": pk}))
        try:
            add_review_note(submission, self.reviewer, request.POST.get("text", ""), request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, "Notatka została dopisana.")
        target = "web:review-compare" if request.POST.get("from") == "compare" else "web:review-detail"
        return redirect(reverse(target, kwargs={"pk": pk}))
