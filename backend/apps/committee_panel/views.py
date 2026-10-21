"""Ekrany ramy panelu komisji: Start, Materiały dla komisji, Powiadomienia (UX-PANEL-03 D5).

Dostęp – te same definicje ról, co bramki istniejących paneli (``services``): Start i Powiadomienia
dla członka komisji tego konkursu (aktywny recenzent **albo** komisja odwoławcza), Materiały wyłącznie
dla recenzenta (``ReviewerRequiredMixin``). Pod adresem bez konkursu – 404 przed logowaniem i rolą
(``RoleRequiredMixin``).

Anonimowość (PROJEKT.md 2.2): żaden z tych ekranów nie dotyka danych uczestnika – Start to liczby,
Materiały to zadania, a Powiadomienia mówią „że” i „gdzie” (nazwa etapu, liczba prac).
"""

from __future__ import annotations

from django.contrib import messages
from django.db.models import Prefetch
from django.http import Http404, HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import TemplateView

from apps.competitions.models import Problem
from apps.notifications import services as notifications
from apps.notifications.models import Notification
from apps.web.mixins import ReviewerRequiredMixin, RoleRequiredMixin
from apps.web.throttle import PerAccountThrottleMixin

from . import dashboard, services
from .nav import MEETINGS_ANCHOR


class CommitteeRequiredMixin(RoleRequiredMixin):
    """Członek komisji tego konkursu: aktywny recenzent albo komisja odwoławcza z rolą ``appeals``."""

    role_denied_message = "Ta strona jest dostępna wyłącznie dla członków komisji."

    def has_role(self, user) -> bool:
        return services.member(self.request) is not None


class CommitteeStartView(CommitteeRequiredMixin, TemplateView):
    """``/committee/`` – pulpit „Start”: prace do oceny per etap, postęp, reklamacje, terminy, skróty."""

    template_name = "committee_panel/start.html"

    def get_context_data(self, **kwargs):
        from apps.competitions.video_rooms import committee_rooms_context
        from apps.webinars.services import committee_webinars_context

        context = super().get_context_data(**kwargs)
        now = timezone.now()
        reviewer = services.reviewer_profile(self.request)
        appeals = services.appeals_profile(self.request)
        rows = dashboard.stage_rows(reviewer, self.competition, now)
        # Karty spotkań – puste słowniki **bez zapytań**, gdy przepustek / webinarów nie ma.
        meetings = {}
        meetings.update(committee_rooms_context(self.request.user, self.competition))
        meetings.update(committee_webinars_context(self.request.user, self.competition))
        counts = services.review_counts(self.request)
        context.update(meetings)
        context.update(
            {
                "reviewer": reviewer,
                "appeals_member": appeals,
                "member": reviewer or appeals,
                "stage_rows": rows,
                "counts": counts,
                "progress_percent": round(100 * counts["submitted"] / counts["total"])
                if counts["total"]
                else 0,
                "overdue_total": sum(row["overdue"] for row in rows),
                "appeals_count": services.appeals_count(self.request) if appeals is not None else 0,
                "notifications_unread": notifications.unread_count(self.request),
                "upcoming": dashboard.upcoming(rows, meetings, now),
                "meetings_anchor": MEETINGS_ANCHOR,
                "meetings": bool(
                    meetings.get("committee_interviews")
                    or meetings.get("committee_rooms")
                    or meetings.get("video_room_issuer")
                    or meetings.get("committee_webinars_on")
                ),
                "now": now,
            }
        )
        return context


class CommitteeMaterialsView(ReviewerRequiredMixin, TemplateView):
    """``/committee/materials/`` – rubryki, uwagi dla recenzentów, wzorcówki i szablony komentarzy zadań.

    Zakres: zadania, w których recenzent ma (nieodebrany) przydział – to są „jego” zadania i tylko do nich
    komitet daje mu materiały na ekranie oceny. Wzorcówkę wydaje dalej wyłącznie
    ``ProblemModelSolutionView`` (bramka komitetu); ta strona podaje do niej odnośnik. Trzy zapytania
    niezależnie od liczby zadań (zadania, kryteria, wspólne szablony).
    """

    template_name = "committee_panel/materials.html"

    def get_context_data(self, **kwargs):
        from apps.grading.models import CommentSnippet, RubricCriterion
        from apps.grading.services import reviews_for_reviewer

        context = super().get_context_data(**kwargs)
        problem_ids = (
            reviews_for_reviewer(self.reviewer, self.competition)
            .select_related(None)
            .prefetch_related(None)
            .order_by()
            .values("submission__problem_id")
        )
        problems = list(
            Problem.objects.filter(pk__in=problem_ids)
            .select_related("stage")
            .prefetch_related(
                Prefetch("rubric_criteria", queryset=RubricCriterion.objects.order_by("order", "id")),
                Prefetch(
                    "comment_snippets",
                    queryset=CommentSnippet.objects.filter(owner__isnull=True).order_by("order", "id"),
                    to_attr="shared_snippets",
                ),
            )
            .order_by("stage__opens_at", "stage_id", "number")
        )
        groups: list[dict] = []
        for problem in problems:
            if not groups or groups[-1]["stage"].pk != problem.stage_id:
                groups.append({"stage": problem.stage, "problems": []})
            groups[-1]["problems"].append(
                {
                    "problem": problem,
                    "criteria": list(problem.rubric_criteria.all()),
                    "snippets": problem.shared_snippets,
                    "model_solution_url": (
                        reverse("web:problem-model-solution", kwargs={"pk": problem.pk})
                        if problem.model_solution_pdf
                        else ""
                    ),
                    "queue_url": f"{reverse('web:review-list')}?problem={problem.pk}",
                }
            )
        context["groups"] = groups
        return context


class CommitteeNotificationListView(CommitteeRequiredMixin, TemplateView):
    """``/committee/notifications/`` – powiadomienia członka komisji (wiersze jak u uczestnika)."""

    template_name = "committee_panel/notifications.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page = notifications.page_for(self.request.user, self.competition, self.request.GET.get("page"))
        context.update(
            {
                "page_obj": page,
                "rows": [notifications.present(row) for row in page.object_list],
                "unread": notifications.unread_count(self.request),
                "retention_days": notifications.retention_days(),
            }
        )
        return context


class CommitteeNotificationOpenView(CommitteeRequiredMixin, View):
    """Otwarcie pozycji: oznacza ją jako przeczytaną i przekierowuje do celu (nazwa adresu z bazy)."""

    def get(self, request, pk: int):
        try:
            url = notifications.open_notification(request.user, self.competition, pk)
        except Notification.DoesNotExist as exc:
            raise Http404 from exc
        return HttpResponseRedirect(url or reverse("web:committee-notifications"))


class CommitteeNotificationReadAllView(CommitteeRequiredMixin, PerAccountThrottleMixin, View):
    #: Kubełek konta 60/min – jak w panelu uczestnika: zapis dotyczy wyłącznie własnych wierszy.
    throttle_scope = "anon"

    def post(self, request):
        changed = notifications.mark_all_read(request.user, self.competition)
        notifications.forget_unread_count(request)
        if changed:
            messages.success(request, _("Wszystkie powiadomienia oznaczono jako przeczytane."))
        return HttpResponseRedirect(reverse("web:committee-notifications"))
