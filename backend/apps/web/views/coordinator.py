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

from apps.accounts.activation import (
    ACTIVATION_HOURS,
    ACTIVATION_MAX_AGE,
    mark_activated,
    resend_activation,
)
from apps.accounts.models import CommitteeMember, CommitteeStatus, InvitationGrantsStatus, User
from apps.accounts.services import approve_committee_member, create_invitation, verify_committee_district
from apps.competitions.models import Stage
from apps.competitions.services import current_edition, missing_stage_kinds
from apps.core.api import DomainError
from apps.grading.models import ProblemReviewerRule, Review, ReviewStatus
from apps.grading.services import (
    add_problem_reviewer_rule,
    allowed_scores,
    assign_reviewer_to_submission,
    assign_reviewers,
    assign_third_reviewer,
    moderation_queue,
    override_final_grade,
    remove_problem_reviewer_rule,
    resolve_moderation,
    reviewer_pool,
    set_review_score,
    stage_assignment_rows,
    stage_problem_rules,
    unassign_reviewer,
)
from apps.results.models import ResultsPublication
from apps.results.services import compute_stage_results, publish_results
from apps.submissions.services import close_stage_now
from apps.web.forms import (
    VOIVODESHIP_CHOICES,
    AssignReviewersForm,
    AssignThirdReviewerForm,
    InvitationForm,
    OverrideFinalGradeForm,
    PublishResultsForm,
    ResolveModerationForm,
    ReviewerPickForm,
    SetReviewScoreForm,
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
        "pending_activation": pending_activation_rows(),
        "activation_hours": ACTIVATION_HOURS,
        "preview": None,
    }
    context.update(extra or {})
    return context


def pending_activation_rows(now=None) -> list[dict]:
    """Konta, które czekają na potwierdzenie adresu e-mail – z czasem do automatycznego skasowania.

    Ta sekcja jest **obejściem operacyjnym z terminem ważności**: dopóki domena nadawcy nie ma
    poprawnych rekordów SPF/DKIM (README § 4.2), część listów aktywacyjnych trafia do spamu albo
    jest odrzucana przez serwer odbiorcy, a uczestnik nie ma jak sam wejść do serwisu. Koordynator
    potwierdza wtedy adres ręcznie – po kontakcie telefonicznym albo ze szkołą.

    Pozostały czas jest tu, bo bez niego przycisk „Aktywuj ręcznie” jest ruletką: konto starsze niż
    okno aktywacji zniknie przy najbliższym przebiegu kosiarki (co 15 minut), więc koordynator musi
    wiedzieć, czy rozmawia o koncie, które jeszcze istnieje. Ujemna wartość znaczy „już po czasie,
    czeka na skasowanie” – i wtedy nie ma sensu do niego wracać.

    Role czytamy z grup jednym zapytaniem (``prefetch_related``): lista bywa długa, a rola jest tu
    jedyną podpowiedzią, czy chodzi o uczestnika, czy o zaproszonego recenzenta.
    """
    now = now or timezone.now()
    deadline_offset = timedelta(seconds=ACTIVATION_MAX_AGE)
    rows = []
    users = (
        User.objects.filter(is_active=False, email_verified_at__isnull=True)
        .prefetch_related("groups")
        .order_by("date_joined", "id")
    )
    for user in users:
        purge_at = user.date_joined + deadline_offset
        rows.append(
            {
                "user": user,
                "purge_at": purge_at,
                # Minuty, nie sekundy: dokładność co do sekundy sugerowałaby, że kosiarka chodzi
                # w tym rytmie – a chodzi co kwadrans.
                "minutes_left": int((purge_at - now).total_seconds() // 60),
                "roles": ", ".join(sorted(group.name for group in user.groups.all())) or "—",
            }
        )
    return rows


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


class StageAssignmentsView(CoordinatorRequiredMixin, TemplateView):
    """``/coordinator/stages/<id>/assignments/`` – przydziały i oceny etapu.

    Ekran jest osobny od pulpitu, bo odpowiada na inne pytanie: pulpit mówi „przydziel wszystko”,
    ten ekran – „komu konkretnie ma trafić ta praca” i „ile ostatecznie ma dostać punktów”. Tabela
    prac pokazuje **nazwisko obok kodu publicznego**: koordynator jest jedyną rolą, która ma do tego
    prawo (PROJEKT.md 2.3), a bez nazwiska nie da się załatwić telefonu „dzwonię w sprawie pracy
    mojego ucznia”.

    Wartości punktowe w formularzach pochodzą ze skali etapu, a nie z wolnego pola: ocena spoza
    skali i tak zostałaby odrzucona przez serwis, a lista wyboru mówi koordynatorowi wprost, czym
    dysponuje. Etap bez skali nie przewraca ekranu – formularze ocen po prostu na nim nie stoją.

    Strona jest w całości renderowana po stronie serwera – przy skali zawodów treningowych lista
    prac etapu mieści się na jednym ekranie, a stronicowanie byłoby kosztem bez pożytku.
    """

    template_name = "web/coordinator/assignments.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        stage = get_object_or_404(Stage.objects.select_related("edition"), pk=self.kwargs["stage_id"])
        query = self.request.GET.get("q", "")
        try:
            scores = sorted(allowed_scores(stage))
        except DomainError:
            # Etap bez skali punktacji to stan do naprawienia w adminie, a nie powód, żeby odciąć
            # koordynatora od przydziałów. Ekran stoi, znika tylko to, czego nie da się wypełnić.
            scores = []
        context.update(
            {
                "stage": stage,
                "now": timezone.now(),
                "query": query,
                "problem_rows": stage_problem_rules(stage),
                "submission_rows": stage_assignment_rows(stage, query),
                "reviewer_pool": reviewer_pool(),
                "assigned_status": ReviewStatus.ASSIGNED,
                "scale_values": scores,
                "results_published": ResultsPublication.objects.filter(stage=stage).exists(),
            }
        )
        return context


class StageAssignmentActionView(CoordinatorActionView):
    """Akcja wracająca na ekran przydziałów etapu, a nie na pulpit.

    Etap ustala ``perform`` (wynika z zadania, pracy albo recenzji, nie z adresu), więc powrót
    czyta ``self.stage_id``. Gdy akcja odpadła, zanim udało się go ustalić – wracamy na pulpit,
    bo nie wiadomo, na który ekran.
    """

    def get_success_url(self, *args, **kwargs) -> str:
        stage_id = getattr(self, "stage_id", None)
        if stage_id is None:
            return str(DASHBOARD_URL)
        return reverse("web:coordinator-stage-assignments", args=[stage_id])


class AddProblemRuleView(StageAssignmentActionView):
    """Dodanie reguły „to zadanie recenzuje ta osoba” – działa też na prace już zablokowane."""

    def perform(self, request, problem_id: int) -> str:
        from apps.competitions.models import Problem

        problem = get_object_or_404(Problem.objects.select_related("stage"), pk=problem_id)
        self.stage_id = problem.stage_id
        form = ReviewerPickForm(request.POST)
        if not form.is_valid():
            raise DomainError("Wskaż recenzenta.", "REVIEWER_REQUIRED")
        reviewer = get_object_or_404(
            CommitteeMember.objects.select_related("user"), pk=form.cleaned_data["reviewer_id"]
        )
        result = add_problem_reviewer_rule(problem, reviewer, actor=request.user, request=request)
        if result["conflicts"]:
            messages.warning(
                request,
                f"Pominięto {result['conflicts']} prac – recenzent ma konflikt interesów z ich autorami.",
            )
        return (
            f"Reguła dodana: zadanie {problem.number} recenzuje {reviewer.user.email}. "
            f"Dopisano recenzji do prac już zablokowanych: {result['assigned']}."
        )


class RemoveProblemRuleView(StageAssignmentActionView):
    """Usunięcie reguły. Recenzje, które z niej powstały, zostają – komunikat mówi to wprost."""

    def perform(self, request, pk: int) -> str:
        rule = get_object_or_404(
            ProblemReviewerRule.objects.select_related("problem", "reviewer", "reviewer__user"), pk=pk
        )
        self.stage_id = rule.problem.stage_id
        email = rule.reviewer.user.email
        number = rule.problem.number
        remove_problem_reviewer_rule(rule, actor=request.user, request=request)
        return (
            f"Reguła usunięta (zadanie {number}, {email}). Przydziały, które już z niej powstały, "
            "zostają – cofnij je osobno przyciskiem „Cofnij”."
        )


class AssignSubmissionReviewerView(StageAssignmentActionView):
    """Ręczny przydział jednej pracy jednemu recenzentowi."""

    def perform(self, request, submission_id: int) -> str:
        from apps.submissions.models import Submission

        submission = get_object_or_404(
            Submission.objects.select_related("entry", "entry__participant"), pk=submission_id
        )
        self.stage_id = submission.entry.stage_id
        form = ReviewerPickForm(request.POST)
        if not form.is_valid():
            raise DomainError("Wskaż recenzenta.", "REVIEWER_REQUIRED")
        reviewer = get_object_or_404(
            CommitteeMember.objects.select_related("user"), pk=form.cleaned_data["reviewer_id"]
        )
        assign_reviewer_to_submission(submission, reviewer, actor=request.user, request=request)
        return (
            f"Przydzielono pracę {submission.entry.participant.public_code} "
            f"recenzentowi {reviewer.user.email}."
        )


class UnassignReviewView(StageAssignmentActionView):
    """Cofnięcie nierozpoczętego przydziału."""

    def perform(self, request, pk: int) -> str:
        review = get_object_or_404(
            Review.objects.select_related("submission", "submission__entry", "reviewer", "reviewer__user"),
            pk=pk,
        )
        self.stage_id = review.submission.entry.stage_id
        unassign_reviewer(review, actor=request.user, request=request)
        return f"Przydział dla {review.reviewer.user.email} został cofnięty."


class SetReviewScoreView(StageAssignmentActionView):
    """Wpisanie albo poprawienie punktów jednej recenzji."""

    def perform(self, request, pk: int) -> str:
        review = get_object_or_404(
            Review.objects.select_related("submission", "submission__entry", "reviewer", "reviewer__user"),
            pk=pk,
        )
        self.stage_id = review.submission.entry.stage_id
        form = SetReviewScoreForm(request.POST)
        if not form.is_valid():
            raise DomainError("Podaj punkty ze skali etapu.", "SCORE_REQUIRED")
        set_review_score(
            review,
            form.cleaned_data["score"],
            actor=request.user,
            request=request,
            rationale=form.cleaned_data["rationale"],
        )
        return f"Zapisano {form.cleaned_data['score']} pkt w recenzji {review.reviewer.user.email}."


class OverrideFinalGradeView(StageAssignmentActionView):
    """Wpisanie albo korekta oceny końcowej pracy – także pracy, której nikt nie recenzował."""

    def perform(self, request, submission_id: int) -> str:
        from apps.submissions.models import Submission

        submission = get_object_or_404(
            Submission.objects.select_related("entry", "entry__participant"), pk=submission_id
        )
        self.stage_id = submission.entry.stage_id
        form = OverrideFinalGradeForm(request.POST)
        if not form.is_valid():
            raise DomainError("Podaj punkty i uzasadnienie korekty.", "RATIONALE_REQUIRED")
        result = override_final_grade(
            submission,
            form.cleaned_data["score"],
            rationale=form.cleaned_data["rationale"],
            actor=request.user,
            request=request,
        )
        if result["results_stale"]:
            # Ostrzeżenie, a nie odmowa: ogłoszona tabela jest dokumentem z chwili publikacji,
            # więc korekta wchodzi do niej dopiero przez ponowne przeliczenie i ogłoszenie.
            messages.warning(
                request,
                "Wyniki tego etapu są już ogłoszone – zmiana pojawi się dopiero po ponownym "
                "przeliczeniu i publikacji.",
            )
        return (
            f"Ocena końcowa pracy {submission.entry.participant.public_code}: "
            f"{result['grade'].score} pkt (korekta koordynatora)."
        )


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


class ActivateAccountView(CoordinatorActionView):
    """Ręczna aktywacja konta przez koordynatora – obejście na czas problemów z dostarczalnością.

    Wpis audytowy ma **własną akcję** (``account.activated_by_coordinator``), a nie tę samą, co
    kliknięcie linku: różnica jest istotna dla każdego, kto później pyta „skąd wiemy, że ten adres
    należy do tej osoby”. Przy aktywacji linkiem dowodem jest dostęp do skrzynki; tutaj – decyzja
    człowieka, który sprawdził to inaczej (telefonicznie, przez szkołę).
    """

    def perform(self, request, pk: int) -> str:
        user = get_object_or_404(User, pk=pk)
        if user.email_verified_at is not None:
            raise DomainError("To konto jest już aktywne.", "ALREADY_ACTIVE")
        mark_activated(user, actor=request.user, action="account.activated_by_coordinator", request=request)
        return "Konto zostało aktywowane ręcznie."


class ResendActivationView(CoordinatorActionView):
    """Ponowna wysyłka linku aktywacyjnego z panelu koordynatora.

    Tu, w odróżnieniu od publicznego formularza, komunikat mówi prawdę o stanie konta: koordynator
    i tak widzi całą listę oczekujących, więc ukrywanie przed nim, że konto jest już aktywne,
    nie chroniłoby niczego, a utrudniałoby pracę.
    """

    def perform(self, request, pk: int) -> str:
        user = get_object_or_404(User, pk=pk)
        if not resend_activation(user.email, request=request):
            raise DomainError(
                "Tego konta nie da się aktywować linkiem – adres jest już potwierdzony.",
                "NOTHING_TO_SEND",
            )
        return "Link aktywacyjny został wysłany ponownie."


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
