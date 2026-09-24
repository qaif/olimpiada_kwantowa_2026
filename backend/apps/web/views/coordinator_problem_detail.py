"""Karta zadania w panelu koordynatora – jeden ekran zamiast pięciu.

Widok jest wyłącznie odczytem. Każda czynność na karcie (blokada pracy do oceny, przydział
recenzenta, korekta punktów i oceny końcowej, reguły zadania, paczka ZIP, edycja treści, skala
etapu) idzie przez adres akcji, który już istnieje – karta niczego nie dubluje i nie zna ani
jednej reguły domenowej. Dane składa ``apps.competitions.problem_card``.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView

from apps.ai_grading import services as ai_grading
from apps.competitions.models import Problem
from apps.competitions.problem_card import problem_card
from apps.grading.models import ReviewStatus
from apps.student_status.models import enabled as student_status_enabled
from apps.web.mixins import CoordinatorRequiredMixin

PROBLEM_DETAIL_TEMPLATE = "web/coordinator/problem_detail.html"


class ProblemCardView(CoordinatorRequiredMixin, TemplateView):
    """``/coordinator/problems/<pk>/`` – treść, skala, reguły, prace i statystyki jednego zadania.

    Wyszukiwarka prac jedzie w adresie (``?q=``), tak samo jak na ekranie przydziałów etapu:
    zawężona lista ma dać się odświeżyć i wysłać odnośnikiem, a „wstecz” ma wracać tam, gdzie się
    było. Szuka po kodzie uczestnika **i** po nazwisku, bo koordynator zna jedno i drugie i raz
    przychodzi do niego kod z listy pominiętych prac, raz telefon od opiekuna z nazwiskiem.
    """

    template_name = PROBLEM_DETAIL_TEMPLATE

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        problem = get_object_or_404(
            Problem.objects.for_competition(self.competition).select_related(
                "stage", "stage__edition", "stage__scoring_scale"
            ),
            pk=self.kwargs["pk"],
        )
        context.update(problem_card(problem, query=self.request.GET.get("q", "")))
        # Sekcja „Ocena AI” istnieje wyłącznie w konkursie z włączoną flagą ``ai_grading`` – przy
        # wyłączonej nie ma tu ani jednego zapytania więcej (``has_feature`` czyta pole wiersza).
        if ai_grading.is_enabled(self.competition):
            context["ai"] = ai_grading.problem_overview(problem)
            context["ai_settings"] = ai_grading.settings_for(self.competition)
        context.update(
            {
                "assigned_status": ReviewStatus.ASSIGNED,
                # Odebrać wolno recenzję w każdym stanie poza anulowaną – także wystawioną.
                # Czy w tej konkretnej sprawie wolno, rozstrzyga serwis: ekran nie powiela reguły,
                # tylko nie chowa przycisku (ta sama zasada, co na ekranie przydziałów etapu).
                "withdrawable_statuses": (
                    ReviewStatus.ASSIGNED,
                    ReviewStatus.DRAFT,
                    ReviewStatus.SUBMITTED,
                ),
                # Wybór zakresu paczki ZIP zadania („wszystkie prace” / „tylko potwierdzony status
                # ucznia”) – flagę czyta widok, nie szablon (§ 2.1 punkt 3).
                "zip_scope_choice": student_status_enabled(self.competition),
            }
        )
        return context
