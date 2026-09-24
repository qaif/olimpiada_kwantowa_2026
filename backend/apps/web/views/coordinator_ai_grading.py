"""Panel koordynatora: ocena AI – klucz API, model, limit wydatków, widoczność i zlecenia.

Trzy ekrany, jedna bramka. **Kolejność bramek** jest ta sama co przy moderacji forum: najpierw
rola (``CoordinatorRequiredMixin`` – 403 dla każdego innego konta), potem przełącznik konkursu
(404 przy wyłączonej ocenie AI). Odwrotna kolejność mówiłaby uczestnikowi odpowiedzią serwera,
jak ten konkurs jest skonfigurowany.

Klucz API jest **tylko do zapisu**. Formularz ma pole hasła bez ``render_value`` – po nieudanym
zapisie pole wraca puste, a nie z tym, co wklejono – a ekran pokazuje wyłącznie „ustawiony,
kończy się na …abcd”. Komunikaty po zapisie nie cytują klucza, a audyt zapisuje sam fakt.

Zlecenie jest **dwustopniowe**, tak jak wysyłka komunikatów (``coordinator_messages``): pierwszy
POST pokazuje liczbę prac i szacowany koszt, dopiero drugi (``action=confirm``) zleca. Pieniądze
wydane na API nie wracają, więc liczba i kwota przed kliknięciem są jedynym momentem, w którym
pomyłka („nie to zadanie”, „z ponownym generowaniem”) jest jeszcze darmowa.
"""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters
from django.views.generic import View

from apps.ai_grading import services as ai
from apps.ai_grading.models import AiModel
from apps.competitions.models import Problem, Stage
from apps.competitions.services import current_edition
from apps.core.api import DomainError
from apps.web.mixins import CoordinatorRequiredMixin

SETTINGS_TEMPLATE = "web/coordinator/ai_grading.html"
CONFIRM_TEMPLATE = "web/coordinator/ai_generate_confirm.html"
PROGRESS_TEMPLATE = "web/coordinator/_ai_problem.html"

#: Wartość pola ``action``, która znaczy „zleć naprawdę”. Każda inna kończy się podglądem.
ACTION_CONFIRM = "confirm"


class ApiKeyForm(forms.Form):
    """Pole klucza. ``render_value=False`` – klucz nie wraca do przeglądarki nawet po błędzie."""

    api_key = forms.CharField(
        label="Klucz API Anthropic",
        max_length=500,
        strip=True,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off", "spellcheck": "false"}),
        help_text="Klucz zaczyna się od „sk-ant-”. Po zapisaniu nie da się go odczytać – można go "
        "tylko zastąpić albo usunąć.",
    )


class OptionsForm(forms.Form):
    model = forms.ChoiceField(label="Model", choices=AiModel.choices)
    spending_limit_usd = forms.DecimalField(
        label="Limit wydatków (USD)",
        required=False,
        min_value=Decimal("0"),
        max_digits=10,
        decimal_places=2,
        help_text="Po jego osiągnięciu nowe zlecenia są odrzucane, a oceny czekające w kolejce "
        "kończą się błędem zamiast wołać API. Puste = bez limitu.",
    )


class AiCoordinatorMixin(CoordinatorRequiredMixin):
    """Rola, potem przełącznik – uzasadnienie kolejności w docstringu modułu."""

    @property
    def ai_competition(self):
        competition = self.competition
        if not ai.is_enabled(competition):
            raise Http404("Ocena AI jest w tym konkursie wyłączona.")
        return competition

    def problem(self, pk: int) -> Problem:
        """Zadanie **tego** konkursu – identyfikator z sąsiedniej olimpiady daje 404."""
        return get_object_or_404(
            Problem.objects.for_competition(self.ai_competition).select_related(
                "stage", "stage__edition", "stage__edition__competition", "stage__scoring_scale"
            ),
            pk=pk,
        )


def _stages(competition) -> list[Stage]:
    edition = current_edition(competition)
    if edition is None:
        return []
    return list(Stage.objects.filter(edition=edition).order_by("opens_at", "id"))


@method_decorator(sensitive_post_parameters("api_key"), name="dispatch")
class AiGradingSettingsView(AiCoordinatorMixin, View):
    """``GET|POST /coordinator/ai-grading/`` – klucz, model, limit, widoczność dla uczestników.

    ``sensitive_post_parameters``: raport błędu Django (list do ``ADMINS``) cytuje dane POST
    żądania, które się wywróciło – pole z kluczem ma w nim stać jako gwiazdki.
    """

    def get(self, request):
        competition = self.ai_competition
        return self._render(request, competition)

    def post(self, request):
        competition = self.ai_competition
        action = request.POST.get("action", "")
        key_form = None
        options_form = None
        try:
            if action == "set_key":
                key_form = ApiKeyForm(request.POST)
                if key_form.is_valid():
                    ai.set_api_key(
                        competition, key_form.cleaned_data["api_key"], actor=request.user, request=request
                    )
                    messages.success(
                        request, "Klucz API zapisany. Użyj „Sprawdź klucz”, żeby potwierdzić, że działa."
                    )
                    return redirect(reverse("web:coordinator-ai-grading"))
            elif action == "remove_key":
                ai.remove_api_key(competition, actor=request.user, request=request)
                messages.warning(request, "Klucz API usunięty – nowe oceny AI nie będą liczone.")
                return redirect(reverse("web:coordinator-ai-grading"))
            elif action == "check_key":
                ok, message = ai.check_api_key(competition, actor=request.user, request=request)
                (messages.success if ok else messages.error)(request, message)
                return redirect(reverse("web:coordinator-ai-grading"))
            elif action == "options":
                options_form = OptionsForm(request.POST)
                if options_form.is_valid():
                    ai.update_options(
                        competition,
                        model=options_form.cleaned_data["model"],
                        spending_limit_usd=options_form.cleaned_data["spending_limit_usd"],
                        actor=request.user,
                        request=request,
                    )
                    messages.success(request, "Ustawienia oceny AI zapisane.")
                    return redirect(reverse("web:coordinator-ai-grading"))
            elif action == "visibility":
                stage_id = request.POST.get("stage", "")
                if not stage_id.isdigit():
                    raise Http404("Nie ma takiego etapu.")
                stage = get_object_or_404(Stage.objects.for_competition(competition), pk=int(stage_id))
                show = request.POST.get("show") == "1"
                ai.set_stage_visibility(stage, show, actor=request.user, request=request)
                if show:
                    messages.warning(
                        request,
                        f"Uczestnicy etapu „{stage.display_name}” zobaczą ocenę AI swoich prac po ogłoszeniu "
                        "wyników – obok oficjalnej oceny, z podpisem, że to sugestia AI.",
                    )
                else:
                    messages.success(
                        request, f"Ocena AI jest ukryta przed uczestnikami etapu „{stage.display_name}”."
                    )
                return redirect(reverse("web:coordinator-ai-grading"))
            else:
                messages.error(request, "Nieznana czynność.")
                return redirect(reverse("web:coordinator-ai-grading"))
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect(reverse("web:coordinator-ai-grading"))
        return self._render(request, competition, key_form=key_form, options_form=options_form, status=400)

    def _render(self, request, competition, *, key_form=None, options_form=None, status: int = 200):
        row = ai.settings_for(competition)
        stages = _stages(competition)
        visible = ai.visible_stage_ids(stages)
        context = {
            "ai_settings": row,
            "key_form": key_form or ApiKeyForm(),
            "options_form": options_form
            or OptionsForm(initial={"model": row.model, "spending_limit_usd": row.spending_limit_usd}),
            "stages": [{"stage": stage, "visible": stage.pk in visible} for stage in stages],
            "processor": ai.PROCESSOR_NAME,
        }
        return TemplateResponse(request, SETTINGS_TEMPLATE, context, status=status)


class AiGenerateView(AiCoordinatorMixin, View):
    """``POST /coordinator/problems/<pk>/ai/generate/`` – podgląd (liczba i koszt), potem zlecenie.

    Jedna praca albo całe zadanie: pole ``submission`` zawęża zlecenie do jednej wersji pracy
    (przycisk przy wierszu), jego brak – do wszystkich najnowszych wersji zadania. Formularz jedzie
    drugi raz w komplecie, więc nie da się zlecić czegoś innego, niż pokazał podgląd.
    """

    def post(self, request, pk: int):
        problem = self.problem(pk)
        regenerate = request.POST.get("regenerate") == "on"
        submission_ids = None
        submission_id = request.POST.get("submission", "")
        if submission_id:
            try:
                submission_ids = [int(submission_id)]
            except ValueError:
                raise Http404("Nie ma takiej pracy.") from None
        back = reverse("web:coordinator-problem", kwargs={"pk": problem.pk}) + "#ocena-ai"
        try:
            ai.assert_can_request(self.ai_competition)
            if request.POST.get("action") != ACTION_CONFIRM:
                plan = ai.plan_generation(problem, regenerate=regenerate, submission_ids=submission_ids)
                context = {
                    "problem": problem,
                    "stage": problem.stage,
                    "plan": plan,
                    "regenerate": regenerate,
                    "submission_id": submission_id,
                    "back_url": back,
                }
                return TemplateResponse(request, CONFIRM_TEMPLATE, context)
            plan = ai.request_generation(
                problem,
                regenerate=regenerate,
                submission_ids=submission_ids,
                actor=request.user,
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect(back)
        if plan.count:
            messages.success(
                request,
                f"Zlecono ocenę AI dla {plan.count} prac. Oceny liczą się po kolei w tle – postęp widać "
                "w sekcji „Ocena AI” poniżej.",
            )
        else:
            messages.info(
                request, "Nie było czego zlecić – wszystkie prace mają już ocenę AI albo są w toku."
            )
        return redirect(back)


class AiProblemProgressView(AiCoordinatorMixin, View):
    """``GET /coordinator/problems/<pk>/ai/`` – sama sekcja postępu (odświeżana przez htmx)."""

    def get(self, request, pk: int):
        problem = self.problem(pk)
        context = {
            "problem": problem,
            "ai": ai.problem_overview(problem),
            "ai_settings": ai.settings_for(self.ai_competition),
        }
        return TemplateResponse(request, PROGRESS_TEMPLATE, context)
