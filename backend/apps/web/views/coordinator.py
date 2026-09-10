"""Panel koordynatora ``/coordinator/``.

Koordynator jest jedyną rolą, która widzi dane osobowe (podgląd tabeli wyników przed publikacją)
oraz tożsamość recenzentów w kolejce moderacji – tak stanowi macierz uprawnień (PROJEKT.md 2.3).
Każda akcja to wywołanie istniejącego serwisu; widok nie zna reguł domenowych.

Kod zaproszenia jest pokazywany **dokładnie raz**, przez komunikat sesyjny: w bazie zostaje
wyłącznie sha256, więc odtworzenie go nie jest możliwe (``accounts.services.create_invitation``).
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import TemplateView, View

from apps.accounts.models import CommitteeMember, CommitteeStatus, InvitationGrantsStatus
from apps.accounts.services import approve_committee_member, create_invitation, verify_committee_district
from apps.competitions.models import Stage
from apps.competitions.services import current_edition, missing_stage_kinds
from apps.core.api import DomainError
from apps.grading.models import ReviewStatus
from apps.grading.services import (
    assign_reviewers,
    assign_third_reviewer,
    moderation_queue,
    resolve_moderation,
    reviewer_pool,
)
from apps.results.models import ResultsPublication
from apps.results.services import compute_stage_results, publish_results
from apps.submissions.services import close_stage_now
from apps.web.forms import (
    VOIVODESHIP_CHOICES,
    AssignReviewersForm,
    AssignThirdReviewerForm,
    InvitationForm,
    PublishResultsForm,
    ResolveModerationForm,
    VerifyDistrictForm,
)
from apps.web.mixins import ActionViewMixin, CoordinatorRequiredMixin
from apps.web.templatetags.web_extras import LOCAL_TIME_LABEL

DASHBOARD_URL = reverse_lazy("web:coordinator")


def _counters(stages: list[Stage], moderation: list, pending_members: list) -> dict:
    """Liczniki na kafelki KPI. Wyłącznie prezentacja – żadnej reguły domenowej.

    Import modeli jest lokalny z tego samego powodu, co w akcjach niżej: moduł widoków ładuje się
    przy starcie urlconfa, a ``apps.submissions``/``apps.grading`` zaciągają wtedy własne serwisy.
    """
    from apps.appeals.models import Appeal, AppealStatus
    from apps.grading.models import Review
    from apps.submissions.models import Submission

    stage_ids = [stage.pk for stage in stages]
    return {
        "submissions": Submission.objects.filter(entry__stage_id__in=stage_ids).count(),
        "pending_reviews": Review.objects.filter(
            submission__entry__stage_id__in=stage_ids,
            status__in=(ReviewStatus.ASSIGNED, ReviewStatus.DRAFT),
        ).count(),
        "moderation": len(moderation),
        "open_appeals": Appeal.objects.filter(
            submission__entry__stage_id__in=stage_ids, status=AppealStatus.OPEN
        ).count(),
        "pending_members": len(pending_members),
    }


def dashboard_context(extra: dict | None = None) -> dict:
    """Wspólny kontekst pulpitu – używany też po przeliczeniu wyników, żeby pokazać podgląd."""
    edition = current_edition()
    # ``Count`` w zapytaniu, a nie ``stage.problems.count()`` w szablonie: liczniki zadań i terminów
    # rozmów stoją na każdej karcie etapu, więc pętla w szablonie kosztowałaby zapytanie na etap.
    # ``distinct=True`` przy obu, bo dwa ``Count`` na tej samej karcie mnożą wiersze przez siebie.
    stage_qs = Stage.objects.filter(edition=edition).annotate(
        problem_count=Count("problems", distinct=True),
        slot_count=Count("interview_slots", distinct=True),
    )
    stages = list(stage_qs.order_by("opens_at", "id")) if edition else []
    published = set(ResultsPublication.objects.filter(stage__in=stages).values_list("stage_id", flat=True))
    moderation = list(moderation_queue())
    pending_members = list(
        CommitteeMember.objects.select_related("user")
        .filter(status=CommitteeStatus.PENDING)
        .order_by("created_at", "id")
    )
    context = {
        "now": timezone.now(),
        "edition": edition,
        "stage_rows": [{"stage": stage, "has_results": stage.pk in published} for stage in stages],
        # Przycisk „Dodaj etap” znika, kiedy edycja ma już wszystkie trzy rodzaje: para
        # (edycja, rodzaj) jest unikalna, więc formularz nie miałby czego zaproponować.
        "missing_kinds": missing_stage_kinds(edition) if edition else [],
        "moderation": moderation,
        "pending_members": pending_members,
        "counters": _counters(stages, moderation, pending_members),
        "active_members": list(
            CommitteeMember.objects.select_related("user")
            .filter(status=CommitteeStatus.ACTIVE)
            .order_by("user__email")
        ),
        "reviewer_pool": reviewer_pool(),
        "assign_form": AssignReviewersForm(),
        "resolve_form": ResolveModerationForm(),
        "assign_third_form": AssignThirdReviewerForm(),
        "verify_form": VerifyDistrictForm(),
        # Lista województw dla wbudowanych w tabelę formularzy „Potwierdź okręg”: jeden
        # ``<select>`` na wiersz, a wierszy jest tyle, ilu aktywnych członków komitetu.
        "voivodeship_choices": VOIVODESHIP_CHOICES,
        "invitation_form": InvitationForm(),
        "publish_form": PublishResultsForm(),
        "preview": None,
    }
    context.update(extra or {})
    return context


class CoordinatorDashboardView(CoordinatorRequiredMixin, TemplateView):
    """Pulpit koordynatora: etapy, moderacja, komitet, zaproszenia, wyniki."""

    template_name = "web/coordinator/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(dashboard_context())
        return context


class CoordinatorActionView(ActionViewMixin, CoordinatorRequiredMixin, View):
    """Baza akcji koordynatora: POST → serwis → komunikat → powrót na pulpit."""

    success_url = DASHBOARD_URL


class CloseStageView(CoordinatorActionView):
    """Zamknięcie etapu: blokada najnowszych wersji plus znacznik ``closed_at``."""

    def perform(self, request, stage_id: int) -> str:
        stage = get_object_or_404(Stage, pk=stage_id)
        locked = close_stage_now(stage, actor=request.user, request=request)
        return f"Etap zamknięty. Zablokowanych rozwiązań: {locked}."


class AssignReviewersView(CoordinatorActionView):
    """Przydział recenzentów dla etapu. Pominięte prace (``skipped``) są wypisane z pseudonimem."""

    def perform(self, request, stage_id: int) -> str:
        stage = get_object_or_404(Stage.objects.select_related("edition"), pk=stage_id)
        form = AssignReviewersForm(request.POST)
        if not form.is_valid():
            raise DomainError("Nieprawidłowa liczba recenzentów na pracę.", "INVALID_PER_SUBMISSION")
        result = assign_reviewers(
            stage,
            form.cleaned_data["per_submission"],
            actor=request.user,
            request=request,
        )
        if result["skipped"]:
            codes = ", ".join(item["public_code"] for item in result["skipped"])
            messages.warning(
                request,
                f"Pominięto {len(result['skipped'])} prac (brak recenzentów bez konfliktu): {codes}.",
            )
        return f"Przydzielono {result['assignments']} recenzji dla {result['submissions']} rozwiązań."


class ResolveModerationView(CoordinatorActionView):
    """Rozstrzygnięcie rozjazdu ocen przez koordynatora (``GradeMethod.MODERATION``)."""

    def perform(self, request, submission_id: int) -> str:
        from apps.submissions.models import Submission

        submission = get_object_or_404(Submission, pk=submission_id)
        form = ResolveModerationForm(request.POST)
        if not form.is_valid():
            raise DomainError("Podaj punkty ze skali etapu.", "SCORE_REQUIRED")
        resolve_moderation(
            submission,
            request.user,
            form.cleaned_data["score"],
            None,
            form.cleaned_data["rationale"],
            request=request,
        )
        return "Rozjazd rozstrzygnięty."


class AssignThirdReviewerView(CoordinatorActionView):
    """Wyznaczenie trzeciego recenzenta (runda rozjemcza)."""

    def perform(self, request, submission_id: int) -> str:
        from apps.submissions.models import Submission

        submission = get_object_or_404(Submission, pk=submission_id)
        form = AssignThirdReviewerForm(request.POST)
        if not form.is_valid():
            raise DomainError("Wskaż recenzenta.", "REVIEWER_REQUIRED")
        reviewer = get_object_or_404(
            CommitteeMember.objects.select_related("user"), pk=form.cleaned_data["reviewer_id"]
        )
        assign_third_reviewer(submission, reviewer, actor=request.user, request=request)
        return "Trzeci recenzent został wyznaczony."


class ApproveCommitteeMemberView(CoordinatorActionView):
    """Zatwierdzenie członka komitetu (PENDING → ACTIVE + grupy)."""

    def perform(self, request, pk: int) -> str:
        member = get_object_or_404(CommitteeMember.objects.select_related("user"), pk=pk)
        approve_committee_member(member, actor=request.user)
        return "Członek komitetu został zatwierdzony."


class VerifyDistrictView(CoordinatorActionView):
    """Potwierdzenie okręgu – bez tego recenzent nie wchodzi do przydziału na etapie okręgowym."""

    def perform(self, request, pk: int) -> str:
        member = get_object_or_404(CommitteeMember.objects.select_related("user"), pk=pk)
        form = VerifyDistrictForm(request.POST)
        if not form.is_valid():
            raise DomainError("Podaj województwo do potwierdzenia.", "DISTRICT_REQUIRED")
        verify_committee_district(
            member, district=form.cleaned_data["district"], actor=request.user, request=request
        )
        return "Województwo zostało potwierdzone."


class CreateInvitationView(CoordinatorActionView):
    """Generowanie kodu zaproszenia. Kod jawny pokazujemy raz i nigdzie go nie zapisujemy."""

    def perform(self, request) -> str:
        form = InvitationForm(request.POST)
        if not form.is_valid():
            raise DomainError("Nieprawidłowe parametry zaproszenia.", "INVALID_INVITATION_PARAMS")
        data = form.cleaned_data
        invitation, plain_code = create_invitation(
            request.user,
            valid_for=timedelta(days=data["valid_days"]),
            max_uses=data["max_uses"],
            grants_status=(
                InvitationGrantsStatus.PENDING if data["requires_approval"] else InvitationGrantsStatus.ACTIVE
            ),
            is_appeals=data["is_appeals"],
            district=data["district"] or None,
        )
        messages.warning(
            request,
            f"Kod zaproszenia (widoczny tylko teraz, nie da się go odtworzyć): {plain_code}",
        )
        # Ważność podajemy w czasie lokalnym – koordynator przepisuje ją do wiadomości dla
        # zapraszanego, a „UTC” w takim komunikacie było zaproszeniem do pomyłki o godzinę lub dwie.
        expires_local = timezone.localtime(invitation.expires_at)
        return (
            f"Kod ważny do {expires_local:%Y-%m-%d %H:%M} ({LOCAL_TIME_LABEL}), "
            f"limit użyć: {invitation.max_uses}."
        )


class ComputeResultsView(CoordinatorRequiredMixin, View):
    """Podgląd pełnej tabeli wyników etapu bez publikacji (dane osobowe – tylko koordynator)."""

    template_name = "web/coordinator/dashboard.html"

    def post(self, request, stage_id: int):
        stage = get_object_or_404(Stage.objects.select_related("edition", "qualification_rule"), pk=stage_id)
        try:
            rows = compute_stage_results(stage)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect(reverse("web:coordinator"))
        context = dashboard_context({"preview": {"stage": stage, "rows": rows}})
        return TemplateResponse(request, self.template_name, context)


class PublishResultsView(CoordinatorActionView):
    """Publikacja wyników: przeliczenie, kwalifikacja i zamrożenie zanonimizowanej tabeli."""

    def perform(self, request, stage_id: int) -> str:
        stage = get_object_or_404(Stage.objects.select_related("edition", "qualification_rule"), pk=stage_id)
        form = PublishResultsForm(request.POST)
        if not form.is_valid():
            raise DomainError("Wybierz tryb anonimizacji.", "INVALID_ANONYMIZATION")
        publication = publish_results(
            stage, request.user, form.cleaned_data["anonymization"], request=request
        )
        return f"Opublikowano wyniki etapu ({len(publication.rows)} wierszy)."
