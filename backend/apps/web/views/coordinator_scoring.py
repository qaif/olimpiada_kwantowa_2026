"""Ekrany punktacji etapu: wagi zadań, rozstrzyganie remisów, punkty z rozmowy i role recenzenckie.

Cztery czynności z trzech różnych paragrafów planu (``docs/UNIWERSALNY-ETAP-2.md`` § 1.2.3,
§ 1.2.6, § 1.2.7) w jednym module, bo łączy je jedno: każda opisuje **jak z ocen robi się tabela
wyników tego etapu**, każda jest za inną flagą i każda jest listą wierszy pod adresem etapu.
Rozbicie na cztery moduły po sto linii powtórzyłoby cztery razy tę samą bramkę flagi i ten sam
odczyt etapu z zawężonego querysetu.

**Każdy z tych ekranów jest za flagą i bez niej daje 404**, a nie 403 (§ 2.1):

- ``weighted_scoring`` – wagi zadań (sekcja na istniejącym ekranie skali) i rozstrzyganie remisów,
- ``process_editor`` – punkty z rozmowy, bo dla Olimpiady Kwantowej **ekran zamiast ``/admin/``
  jest zmianą widoczną** (decyzja organizatora D9),
- ``reviewer_roles`` – nazwane role recenzenckie.

Rolę sprawdza wcześniej i osobno ``CoordinatorRequiredMixin``, więc uczestnik dostaje 403
niezależnie od stanu flagi i nie dowiaduje się z odpowiedzi, jak ten konkurs jest skonfigurowany.

**Reguły są w serwisach.** Skalę oceny rozmowy rozstrzyga ``competitions.interviews`` przez
``grading.services.allowed_scores`` (ta sama skala, co przy ocenie pracy), zgodność kryterium
remisu z etapem – ``TieBreak.clean()``, a liczbę recenzentów w roli – więz bazy. Widok wyłącznie
orkiestruje: czyta formularz, woła serwis albo zapisuje wiersz po ``full_clean()`` i tłumaczy
odmowę na komunikat z jej własnym kodem HTTP.

**Audyt.** Punkty z rozmowy zapisuje serwis (``interview.scored``). Kryteria remisu i role
recenzenckie nie mają serwisu – są wierszami konfiguracji etapu, tak samo jak skala – więc wpis
audytowy pisze tutaj widok, z identyfikatorami i liczbami, nigdy z danymi osobowymi.
"""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.competitions.interviews import (
    interview_components_for,
    interview_score_rows,
    record_interview_score,
)
from apps.competitions.models import Problem, Stage, StageComponent, StageEntry, TieBreak
from apps.competitions.scoring import coordinator_score_widget, safe_score_rule
from apps.core.api import DomainError
from apps.core.models import audit
from apps.grading.models import ReviewerRole
from apps.grading.services import allowed_scores
from apps.web.coordinator_forms import (
    InterviewScoreForm,
    ProblemWeightForm,
    ReviewerRoleForm,
    TieBreakForm,
)
from apps.web.mixins import CoordinatorRequiredMixin
from apps.web.views.coordinator_stages import render_scale_page, weight_prefix

TIE_BREAKS_TEMPLATE = "web/coordinator/tie_breaks.html"
INTERVIEW_SCORES_TEMPLATE = "web/coordinator/interview_scores.html"
REVIEWER_ROLES_TEMPLATE = "web/coordinator/reviewer_roles.html"

#: Trzy przełączniki, po jednym na obszar. Nazwy stoją tutaj raz, żeby literówka wywracała się
#: w jednym miejscu, a nie w każdym wywołaniu ``has_feature`` – ten sam zabieg, co przy
#: ``PROCESS_EDITOR_FLAG`` w ``apps.results.services``.
WEIGHTED_SCORING_FLAG = "weighted_scoring"
PROCESS_EDITOR_FLAG = "process_editor"
REVIEWER_ROLES_FLAG = "reviewer_roles"


class ScoringScreenMixin(CoordinatorRequiredMixin):
    """Wspólna bramka ekranów tego modułu: rola, flaga konkursu i zakres querysetu.

    ``feature`` ustawia każda klasa – jedno pole zamiast czterech kopii tego samego ``if``-a.
    """

    feature = ""
    #: Zdanie w odpowiedzi 404. Własne na ekran, bo to jedyna informacja, jaką dostaje ktoś, kto
    #: trafił tu z zakładki po wyłączeniu funkcji.
    disabled_message = "Ta funkcja jest w tym konkursie wyłączona."

    def competition_or_404(self, request):
        competition = getattr(request, "competition", None)
        if competition is None or not competition.has_feature(self.feature):
            raise Http404(self.disabled_message)
        return competition

    def stage_or_404(self, request, stage_id: int) -> Stage:
        """Etap **tego** konkursu albo 404 – razem ze skalą, o którą pyta każdy z tych ekranów."""
        competition = self.competition_or_404(request)
        return get_object_or_404(
            Stage.objects.for_competition(competition).select_related("edition", "scoring_scale"),
            pk=stage_id,
        )


# --- wagi zadań (§ 1.2.6 a) ----------------------------------------------------------------------


class ProblemWeightsView(ScoringScreenMixin, View):
    """``POST /coordinator/stages/<id>/weights/`` – wagi wszystkich zadań etapu naraz.

    Formularz stoi na ekranie skali (sekcja za flagą), a zapis ma własny adres: skala i wagi to
    dwie różne decyzje o dwóch różnych skutkach i nieudany zapis jednej nie ma czyścić drugiej.

    Zapis jest **hurtowy**: wagi zadań jednego etapu ustawia się razem, bo mają sumować się do
    całości, a zapisywanie ich po jednej zostawiałoby etap w stanie policzonym do połowy.

    ``GET`` nie ma tu czego pokazać i odsyła na ekran skali – to tam ten formularz stoi.
    """

    feature = WEIGHTED_SCORING_FLAG
    disabled_message = "Ten konkurs nie liczy sumy etapu z wagami – ekran jest wyłączony."

    def get(self, request, stage_id: int):
        stage = self.stage_or_404(request, stage_id)
        return redirect(reverse("web:coordinator-stage-scale", args=[stage.pk]))

    def post(self, request, stage_id: int):
        stage = self.stage_or_404(request, stage_id)
        problems = list(stage.problems.order_by("number", "id"))
        forms = {
            problem.pk: ProblemWeightForm(request.POST, prefix=weight_prefix(problem)) for problem in problems
        }
        if not all(form.is_valid() for form in forms.values()):
            return self._back(request, stage, forms, status=400)
        changed = self._save(request, problems, forms)
        if changed:
            messages.success(request, f"Zapisano wagi zadań: {changed}.")
        else:
            # Zapis bez zmiany nie jest błędem i nie zostawia śladu – formularz bywa otwierany po
            # to, żeby przeczytać wagi, a wpis audytowy o zmianie, której nie było, psuje jedyny
            # dziennik, który ktoś naprawdę czyta po latach (ta sama reguła, co przy zgodach).
            messages.info(request, "Nic się nie zmieniło – wagi zadań zostały bez zmian.")
        return redirect(reverse("web:coordinator-stage-scale", args=[stage.pk]))

    def _save(self, request, problems: list[Problem], forms: dict) -> int:
        """Zapisuje zmienione wagi i notuje je w audycie. Zwraca liczbę zmienionych zadań."""
        changed = 0
        for problem in problems:
            data = forms[problem.pk].cleaned_data
            before = (problem.weight_numerator, problem.weight_denominator)
            after = (data["weight_numerator"], data["weight_denominator"])
            if before == after:
                continue
            problem.weight_numerator, problem.weight_denominator = after
            problem.save(update_fields=["weight_numerator", "weight_denominator"])
            audit(
                request.user,
                "problem.weight_updated",
                problem,
                {"from": list(before), "to": list(after)},
                request=request,
            )
            changed += 1
        return changed

    def _back(self, request, stage: Stage, forms: dict, *, status: int):
        messages.error(request, "Waga musi być parą liczb całkowitych, a mianownik – dodatni.")
        return render_scale_page(request, stage, status=status, weight_forms=forms)


# --- rozstrzyganie remisów (§ 1.2.6 c) -----------------------------------------------------------


def _render_tie_breaks(request, stage: Stage, form: TieBreakForm, *, status: int = 200):
    """Strona kryteriów remisu. Wspólna dla wejścia, nieudanego dodania i nieudanego usunięcia."""
    context = {
        "stage": stage,
        "form": form,
        "rules": list(stage.tie_breaks.select_related("problem", "component").order_by("position", "id")),
    }
    return TemplateResponse(request, TIE_BREAKS_TEMPLATE, context, status=status)


class TieBreakListView(ScoringScreenMixin, View):
    """``GET|POST /coordinator/stages/<id>/tie-breaks/`` – uporządkowana lista kryteriów remisu.

    Etap bez ani jednego wiersza układa tabelę dokładnie tak, jak przed etapem 2, i to jest stan
    wyjściowy każdego etapu: migracja nie wpisuje Olimpiadzie Kwantowej żadnego kryterium, bo jej
    regulamin remisów nie rozstrzyga.
    """

    feature = WEIGHTED_SCORING_FLAG
    disabled_message = "Ten konkurs nie rozstrzyga remisów z danych – ekran jest wyłączony."

    def get(self, request, stage_id: int):
        stage = self.stage_or_404(request, stage_id)
        return _render_tie_breaks(request, stage, TieBreakForm(stage=stage))

    def post(self, request, stage_id: int):
        stage = self.stage_or_404(request, stage_id)
        form = TieBreakForm(request.POST, stage=stage)
        if not form.is_valid():
            return _render_tie_breaks(request, stage, form, status=400)
        try:
            # Savepoint: kolizja unikalności przy wyścigu o to samo miejsce w kolejce nie może
            # unieważnić całej transakcji żądania.
            with transaction.atomic():
                rule = form.save()
        except IntegrityError:
            messages.error(
                request,
                "Na tym miejscu w kolejności stoi już inne kryterium – zmień numer kolejności.",
            )
            return _render_tie_breaks(request, stage, form, status=409)
        audit(
            request.user,
            "stage.tie_break_added",
            stage,
            {"key": rule.key, "position": rule.position, "descending": rule.descending},
            request=request,
        )
        messages.success(request, f"Dodano kryterium: {rule.get_key_display()}.")
        return redirect(reverse("web:coordinator-stage-tie-breaks", args=[stage.pk]))


class TieBreakDeleteView(ScoringScreenMixin, View):
    """``POST /coordinator/tie-breaks/<id>/delete/`` – zdjęcie kryterium z listy etapu."""

    feature = WEIGHTED_SCORING_FLAG
    disabled_message = "Ten konkurs nie rozstrzyga remisów z danych – ekran jest wyłączony."

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        rule = get_object_or_404(
            TieBreak.objects.for_competition(competition).select_related("stage", "stage__edition"), pk=pk
        )
        stage = rule.stage
        key, position = rule.key, rule.position
        rule.delete()
        audit(
            request.user,
            "stage.tie_break_removed",
            stage,
            {"key": key, "position": position},
            request=request,
        )
        messages.success(request, "Kryterium zostało zdjęte z listy.")
        return redirect(reverse("web:coordinator-stage-tie-breaks", args=[stage.pk]))


# --- punkty z rozmowy (§ 1.2.3, decyzja organizatora D9) -----------------------------------------


def _selected_component(stage: Stage, raw) -> StageComponent | None:
    """Komponent rozmowy wskazany w adresie albo pierwszy z listy. ``None`` = etap ich nie ma.

    Nieznana wartość w adresie schodzi do pierwszego komponentu, a nie do 404: parametr bywa
    uszkodzony przez skrócenie odnośnika, a ekran ma się wtedy otworzyć, a nie odmówić. Wybór i tak
    nie jest uprawnieniem – komponenty należą do etapu, który już przeszedł przez zawężony queryset.
    """
    components = interview_components_for(stage)
    if not components:
        return None
    try:
        wanted = int(raw)
    except (TypeError, ValueError):
        return components[0]
    return next((item for item in components if item.pk == wanted), components[0])


def _render_interview_scores(request, stage: Stage, component, form, *, status: int = 200):
    """Strona wpisu punktów komisji: wybór komponentu i tabela wpisów z terminami."""
    context = {
        "stage": stage,
        "components": interview_components_for(stage),
        "component": component,
        "form": form,
        "rows": interview_score_rows(stage, component) if component is not None else [],
        # Skala w postaci, w której ekran ją **przyjmuje** – tej samej, którą sprawdza serwis.
        # Druga lista, przeliczona po swojemu, rozjechałaby się przy pierwszej zmianie skali.
        "allowed": sorted(allowed_scores(stage)) if component is not None else [],
        # Tryb etapu i granice zakresu (wydanie 0.35.0): w etapie z dowolnymi wartościami podpowiedź
        # pod formularzem mówi „od … do …, co 0,01”, a nie wylicza samych wartości skali.
        "score_widget": coordinator_score_widget(safe_score_rule(stage)) if component is not None else None,
    }
    return TemplateResponse(request, INTERVIEW_SCORES_TEMPLATE, context, status=status)


class InterviewScoresView(ScoringScreenMixin, View):
    """``GET|POST /coordinator/stages/<id>/interview-scores/`` – punkty komisji z rozmów.

    Wpis idzie przez ``record_interview_score``: to on sprawdza skalę, rodzaj komponentu
    i przynależność wpisu do etapu, i to on pisze audyt. Powtórne wpisanie tej samej pary
    (wpis, komponent) jest **poprawką**, a nie drugim wynikiem – dwa wiersze tej samej rozmowy
    znaczyłyby dwie sumy etapu.

    Ekran pokazuje dane osobowe (komisja musi wiedzieć, kogo ocenia i o której), więc stoi na nim
    ta sama odznaka, co przy terminach rozmów i przy podglądzie wyników.
    """

    feature = PROCESS_EDITOR_FLAG
    disabled_message = "Ten konkurs nie prowadzi rozmów z własną punktacją – ekran jest wyłączony."

    def get(self, request, stage_id: int):
        stage = self.stage_or_404(request, stage_id)
        component = _selected_component(stage, request.GET.get("component"))
        return _render_interview_scores(request, stage, component, self._form(stage))

    def post(self, request, stage_id: int):
        stage = self.stage_or_404(request, stage_id)
        component = _selected_component(stage, request.POST.get("component"))
        if component is None:
            raise Http404("Ten etap nie ma ani jednego komponentu rozmowy.")
        form = self._form(stage, request.POST)
        if not form.is_valid():
            return _render_interview_scores(request, stage, component, form, status=400)
        try:
            record_interview_score(
                form.cleaned_data["entry"],
                component,
                form.cleaned_data["points"],
                actor=request.user,
                request=request,
                note=form.cleaned_data["note"],
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return _render_interview_scores(request, stage, component, form, status=exc.status_code)
        messages.success(request, "Punkty z rozmowy zostały zapisane.")
        return redirect(
            f"{reverse('web:coordinator-stage-interview-scores', args=[stage.pk])}?component={component.pk}"
        )

    def _form(self, stage: Stage, data=None) -> InterviewScoreForm:
        """Formularz wpisu z listą wpisów **tego** etapu. ``data=None`` daje formularz niezwiązany."""
        entries = StageEntry.objects.filter(stage=stage).select_related("participant")
        return InterviewScoreForm(data, entries=entries)


# --- role recenzenckie (§ 1.2.7) ------------------------------------------------------------------


def _render_reviewer_roles(request, stage: Stage, form: ReviewerRoleForm, *, status: int = 200):
    """Strona ról etapu: lista w kolejności obsadzania, każdy wiersz z własnym formularzem zmiany.

    Prefiks formularza wiersza (``r<pk>``) jest ten sam, którego szuka ``ReviewerRoleUpdateView``:
    jedna definicja dla strony, która rysuje pola, i dla widoku, który czyta POST.
    """
    context = {
        "stage": stage,
        "form": form,
        "rows": [
            {"role": role, "form": ReviewerRoleForm(instance=role, prefix=f"r{role.pk}")}
            for role in stage.reviewer_roles.order_by("position", "id")
        ],
    }
    return TemplateResponse(request, REVIEWER_ROLES_TEMPLATE, context, status=status)


class ReviewerRolesView(ScoringScreenMixin, View):
    """``GET|POST /coordinator/stages/<id>/reviewer-roles/`` – nazwane role recenzenckie etapu.

    Etap bez ani jednej roli zachowuje się dokładnie jak przed etapem 2: przydział czyta liczbę
    recenzentów z argumentu, a zgodność liczy się ze wszystkich nieanulowanych recenzji rundy
    ślepej. Dopiero pierwszy wiersz przełącza etap na role – i to jest cały przełącznik, obok flagi.
    """

    feature = REVIEWER_ROLES_FLAG
    disabled_message = "Ten konkurs nie nazywa ról recenzenckich – ekran jest wyłączony."

    def get(self, request, stage_id: int):
        stage = self.stage_or_404(request, stage_id)
        return _render_reviewer_roles(request, stage, ReviewerRoleForm())

    def post(self, request, stage_id: int):
        stage = self.stage_or_404(request, stage_id)
        form = ReviewerRoleForm(request.POST)
        if not form.is_valid():
            return _render_reviewer_roles(request, stage, form, status=400)
        role = form.save(commit=False)
        role.stage = stage
        try:
            role.full_clean()
            with transaction.atomic():
                role.save()
        except (ValidationError, IntegrityError):
            # Kod roli jest unikalny w etapie i to jedyna reguła, którą da się tu złamać dwiema
            # drogami naraz (formularz nie zna etapu, więc walidacja unikalności go nie obejmuje).
            form.add_error("code", "Rola o tym kodzie już istnieje w tym etapie.")
            return _render_reviewer_roles(request, stage, form, status=409)
        audit(
            request.user,
            "stage.reviewer_role_added",
            stage,
            {"code": role.code, "round": role.round, "count": role.count},
            request=request,
        )
        messages.success(request, f"Dodano rolę: {role.name}.")
        return redirect(reverse("web:coordinator-stage-reviewer-roles", args=[stage.pk]))


class ReviewerRoleUpdateView(ScoringScreenMixin, View):
    """``POST /coordinator/reviewer-roles/<id>/update/`` – zmiana jednej roli.

    Zmiana idzie z wiersza listy, a nie z osobnej strony: rola to sześć pól i otwieranie dla nich
    własnego ekranu znaczyłoby dwa kliknięcia na każdą literówkę w nazwie.
    """

    feature = REVIEWER_ROLES_FLAG
    disabled_message = "Ten konkurs nie nazywa ról recenzenckich – ekran jest wyłączony."

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        role = get_object_or_404(
            ReviewerRole.objects.for_competition(competition).select_related("stage", "stage__edition"),
            pk=pk,
        )
        stage = role.stage
        form = ReviewerRoleForm(request.POST, instance=role, prefix=f"r{role.pk}")
        if not form.is_valid():
            messages.error(request, "Popraw dane roli – zapis się nie udał.")
            return _render_reviewer_roles(request, stage, ReviewerRoleForm(), status=400)
        changed = list(form.changed_data)
        try:
            with transaction.atomic():
                form.save()
        except IntegrityError:
            messages.error(request, "Rola o tym kodzie już istnieje w tym etapie.")
            return _render_reviewer_roles(request, stage, ReviewerRoleForm(), status=409)
        if changed:
            audit(
                request.user,
                "stage.reviewer_role_updated",
                stage,
                {"code": role.code, "fields": changed},
                request=request,
            )
            messages.success(request, f"Zapisano rolę: {role.name}.")
        else:
            messages.info(request, "Nic się nie zmieniło – rola została bez zmian.")
        return redirect(reverse("web:coordinator-stage-reviewer-roles", args=[stage.pk]))


class ReviewerRoleDeleteView(ScoringScreenMixin, View):
    """``POST /coordinator/reviewer-roles/<id>/delete/`` – wycofanie roli z etapu.

    Rola, na którą ktoś już recenzował, jest chroniona więzem ``PROTECT``: skasowanie zabrałoby
    znaczenie wystawionej ocenie. Odmowa wraca z **409** i renderuje listę, zamiast przekierowywać –
    przy 302 kod odmowy ginie, a wywołujący nie odróżnia „nie usunięto” od „usunięto”.
    """

    feature = REVIEWER_ROLES_FLAG
    disabled_message = "Ten konkurs nie nazywa ról recenzenckich – ekran jest wyłączony."

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        role = get_object_or_404(
            ReviewerRole.objects.for_competition(competition).select_related("stage", "stage__edition"),
            pk=pk,
        )
        stage = role.stage
        code = role.code
        try:
            with transaction.atomic():
                role.delete()
        except ProtectedError:
            messages.error(
                request,
                "Na tę rolę są już wystawione recenzje – nie można jej usunąć. "
                "Zmień jej nazwę albo liczbę recenzentów.",
            )
            return _render_reviewer_roles(request, stage, ReviewerRoleForm(), status=409)
        audit(request.user, "stage.reviewer_role_removed", stage, {"code": code}, request=request)
        messages.success(request, "Rola została wycofana z etapu.")
        return redirect(reverse("web:coordinator-stage-reviewer-roles", args=[stage.pk]))
