"""Panel koordynatora: ocena AI – dostawcy (klucze i umowy), model, ceny, limit, widoczność, zlecenia.

Cztery ekrany, jedna bramka. **Kolejność bramek** jest ta sama co przy moderacji forum: najpierw
rola (``CoordinatorRequiredMixin`` – 403 dla każdego innego konta), potem przełącznik konkursu
(404 przy wyłączonej ocenie AI). Odwrotna kolejność mówiłaby uczestnikowi odpowiedzią serwera,
jak ten konkurs jest skonfigurowany.

Klucze API są **tylko do zapisu**. Pole klucza jest polem hasła bez wartości początkowej – po
nieudanym zapisie wraca puste, a nie z tym, co wklejono – a ekran pokazuje wyłącznie „ustawiony,
kończy się na …abcd”. Komunikaty po zapisie nie cytują klucza, a audyt zapisuje sam fakt. Każdy
dostawca ma własny klucz i własne potwierdzenie umowy powierzenia (DPA) – bez potwierdzenia
dostawca nie pojawia się w wyborze przy zleceniu prac uczestników, a jedynie przy pracy testowej.

Zlecenie jest **dwustopniowe**, tak jak wysyłka komunikatów (``coordinator_messages``): pierwszy
POST pokazuje liczbę prac, dostawcę, model i szacowany koszt, dopiero drugi (``action=confirm``)
zleca. Pieniądze wydane na API nie wracają, więc liczba i kwota przed kliknięciem są jedynym
momentem, w którym pomyłka („nie to zadanie”, „nie ten model”) jest jeszcze darmowa.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters
from django.views.generic import View

from apps.ai_grading import providers as ai_providers
from apps.ai_grading import sandbox
from apps.ai_grading import services as ai
from apps.ai_grading.models import AiAssessment, AiProvider, AiTestWork
from apps.competitions.models import Problem, Stage
from apps.competitions.services import current_edition
from apps.core.api import DomainError
from apps.web.mixins import CoordinatorRequiredMixin

SETTINGS_TEMPLATE = "web/coordinator/ai_grading.html"
CONFIRM_TEMPLATE = "web/coordinator/ai_generate_confirm.html"
PROGRESS_TEMPLATE = "web/coordinator/_ai_problem.html"

#: Wartość pola ``action``, która znaczy „zleć naprawdę”. Każda inna kończy się podglądem.
ACTION_CONFIRM = "confirm"


def _model_choices(row) -> list:
    """Modele z list dostawców pogrupowane po dostawcy; model spoza list – na początku grupy."""
    groups = []
    for provider in ai_providers.all_providers():
        options = [(item.id, item.label) for item in provider.models]
        if row is not None and row.provider == provider.name and row.model not in {m for m, _ in options}:
            options.insert(0, (row.model, f"{row.model} (inny identyfikator)"))
        groups.append((ai.provider_label(provider.name), options))
    return groups


class ApiKeyForm(forms.Form):
    """Pole klucza. ``render_value=False`` – klucz nie wraca do przeglądarki nawet po błędzie."""

    provider = forms.ChoiceField(choices=AiProvider.choices, required=False)
    api_key = forms.CharField(
        label="Klucz API",
        max_length=500,
        strip=True,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off", "spellcheck": "false"}),
    )


class OptionsForm(forms.Form):
    model = forms.ChoiceField(label="Dostawca i model domyślny", choices=())
    custom_model = forms.CharField(
        label="Inny identyfikator modelu",
        required=False,
        max_length=100,
        help_text="Identyfikatory modeli zmieniają się – jeśli dostawca wydał nowy model, wpisz jego "
        "identyfikator (np. z dokumentacji dostawcy) i wybierz dostawcę obok.",
    )
    custom_provider = forms.ChoiceField(
        label="Dostawca tego modelu", choices=AiProvider.choices, required=False
    )
    spending_limit_usd = forms.DecimalField(
        label="Limit wydatków (USD)",
        required=False,
        min_value=Decimal("0"),
        max_digits=10,
        decimal_places=2,
        help_text="Po jego osiągnięciu nowe zlecenia są odrzucane, a oceny czekające w kolejce "
        "kończą się błędem zamiast wołać API. Przy ustawionym limicie model bez ceny jest "
        "niedostępny – limit nie widziałby jego kosztu. Puste = bez limitu.",
    )

    def __init__(self, *args, settings_row=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["model"].choices = _model_choices(settings_row)
        # Model spoza list (np. zapisany wcześniej „inny identyfikator”) też jest poprawnym wyborem.
        if self.data.get("model") and self.data.get("model") not in dict(
            item for _, group in self.fields["model"].choices for item in group
        ):
            self.fields["model"].choices = [
                *self.fields["model"].choices,
                ("", [(self.data["model"], self.data["model"])]),
            ]


class TestWorkForm(forms.Form):
    file = forms.FileField(label="Plik pracy testowej")
    label = forms.CharField(label="Opis (dla siebie)", required=False, max_length=120)
    no_personal_data = forms.BooleanField(
        label="Oświadczam, że plik jest moim własnym przykładem i nie zawiera danych osobowych "
        "uczestników (ani pracy uczestnika).",
        required=False,
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


def _provider_name(request) -> str:
    name = request.POST.get("provider") or AiProvider.ANTHROPIC
    if name not in AiProvider.values:
        raise Http404("Nie ma takiego dostawcy.")
    return name


def _decimal(value: str) -> Decimal | None:
    value = (value or "").strip().replace(",", ".")
    if not value:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise DomainError("Cena musi być liczbą (USD za milion tokenów).", "AI_PRICE_INVALID") from None
    if not number.is_finite() or number < 0 or number > Decimal("100000"):
        raise DomainError("Cena musi być liczbą od 0 do 100 000 USD za milion tokenów.", "AI_PRICE_INVALID")
    return number


def _price_entries(request, row) -> dict:
    """Pola tabeli cen → ``{"dostawca/model": (wejście, wyjście) | None}`` dla ``services.set_prices``."""
    entries: dict = {}
    for item in ai.price_table(row):
        price_in = _decimal(request.POST.get(f"in__{item['field']}", ""))
        price_out = _decimal(request.POST.get(f"out__{item['field']}", ""))
        if price_in is None and price_out is None:
            entries[item["field"]] = None
        elif price_in is None or price_out is None:
            raise DomainError("Podaj obie stawki modelu: wejście i wyjście.", "AI_PRICE_INVALID")
        else:
            entries[item["field"]] = (price_in, price_out)
    new_model = (request.POST.get("new_model") or "").strip()
    if new_model:
        provider = request.POST.get("new_provider", "")
        price_in = _decimal(request.POST.get("new_in", ""))
        price_out = _decimal(request.POST.get("new_out", ""))
        if price_in is None or price_out is None:
            raise DomainError("Podaj obie stawki nowego modelu: wejście i wyjście.", "AI_PRICE_INVALID")
        entries[f"{provider}/{new_model}"] = (price_in, price_out)
    return entries


@method_decorator(sensitive_post_parameters("api_key"), name="dispatch")
class AiGradingSettingsView(AiCoordinatorMixin, View):
    """``GET|POST /coordinator/ai-grading/`` – dostawcy, model, ceny, limit, widoczność.

    ``sensitive_post_parameters``: raport błędu Django (list do ``ADMINS``) cytuje dane POST
    żądania, które się wywróciło – pole z kluczem ma w nim stać jako gwiazdki.
    """

    def get(self, request):
        competition = self.ai_competition
        return self._render(request, competition)

    def post(self, request):
        competition = self.ai_competition
        action = request.POST.get("action", "")
        options_form = None
        back = reverse("web:coordinator-ai-grading")
        try:
            if action == "set_key":
                provider = _provider_name(request)
                key_form = ApiKeyForm(request.POST)
                if not key_form.is_valid():
                    messages.error(request, "Wklej klucz API.")
                    return redirect(f"{back}#dostawca-{provider}")
                ai.set_api_key(
                    competition,
                    key_form.cleaned_data["api_key"],
                    actor=request.user,
                    request=request,
                    provider=provider,
                )
                messages.success(
                    request,
                    f"Klucz API {ai.provider_label(provider)} zapisany. Użyj „Sprawdź klucz”, żeby "
                    "potwierdzić, że działa.",
                )
                return redirect(f"{back}#dostawca-{provider}")
            if action == "remove_key":
                provider = _provider_name(request)
                ai.remove_api_key(competition, actor=request.user, request=request, provider=provider)
                messages.warning(
                    request,
                    f"Klucz API {ai.provider_label(provider)} usunięty – ten dostawca nie liczy już ocen.",
                )
                return redirect(f"{back}#dostawca-{provider}")
            if action == "check_key":
                provider = _provider_name(request)
                ok, message = ai.check_api_key(
                    competition, actor=request.user, request=request, provider=provider
                )
                (messages.success if ok else messages.error)(request, message)
                return redirect(f"{back}#dostawca-{provider}")
            if action == "dpa_revoke":
                # Potwierdzenie idzie osobnym ekranem (``AiDpaConfirmView``); stąd wyłącznie wycofanie –
                # jedno kliknięcie z pytaniem w przeglądarce, bo wycofanie tylko zamyka drogę danym.
                provider = _provider_name(request)
                ai.set_dpa_confirmation(competition, provider, False, actor=request.user, request=request)
                messages.warning(
                    request,
                    f"Potwierdzenie umowy powierzenia z {ai.provider_label(provider)} wycofane – prace "
                    "uczestników nie trafią już do tego dostawcy (oceny czekające w kolejce zakończą się "
                    "błędem bez wysyłki).",
                )
                return redirect(f"{back}#dostawca-{provider}")
            if action == "options":
                row = ai.settings_for(competition)
                options_form = OptionsForm(request.POST, settings_row=row)
                if options_form.is_valid():
                    data = options_form.cleaned_data
                    custom = (data.get("custom_model") or "").strip()
                    ai.update_options(
                        competition,
                        model=custom or data["model"],
                        provider=(data.get("custom_provider") or row.provider) if custom else None,
                        spending_limit_usd=data["spending_limit_usd"],
                        actor=request.user,
                        request=request,
                    )
                    messages.success(request, "Ustawienia oceny AI zapisane.")
                    return redirect(back)
            elif action == "prices":
                row = ai.settings_for(competition)
                ai.set_prices(competition, _price_entries(request, row), actor=request.user, request=request)
                messages.success(request, "Ceny modeli zapisane.")
                return redirect(f"{back}#ceny")
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
                return redirect(back)
            else:
                messages.error(request, "Nieznana czynność.")
                return redirect(back)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect(back)
        return self._render(request, competition, options_form=options_form, status=400)

    def _render(self, request, competition, *, options_form=None, status: int = 200):
        row = ai.settings_for(competition)
        stages = _stages(competition)
        visible = ai.visible_stage_ids(stages)
        custom = row.provider != ai.provider_for_model(row.model)
        context = {
            "ai_settings": row,
            "providers": ai.provider_states(competition),
            "options_form": options_form
            or OptionsForm(
                settings_row=row,
                initial={
                    "model": row.model,
                    "custom_provider": row.provider,
                    "spending_limit_usd": row.spending_limit_usd,
                },
            ),
            "default_label": f"{ai.provider_label(row.provider)} – {row.model}"
            + (" (inny identyfikator)" if custom else ""),
            "default_price": ai.price_for(row.provider, row.model, row),
            "prices": ai.price_table(row),
            "provider_choices": AiProvider.choices,
            "stages": [{"stage": stage, "visible": stage.pk in visible} for stage in stages],
        }
        return TemplateResponse(request, SETTINGS_TEMPLATE, context, status=status)


class AiDpaConfirmView(AiCoordinatorMixin, View):
    """``GET|POST /coordinator/ai-grading/dpa/<dostawca>/`` – potwierdzenie umowy powierzenia.

    Decyzja organizatora: koordynator potwierdza umowę **osobiście, po zobaczeniu informacji**
    o dostawcy (co wychodzi z serwisu, odbiorca, przekazanie poza EOG, linki do DPA i warunków,
    retencja, trenowanie, brak retencji, ograniczenia wieku). Dlatego potwierdzenie ma własny ekran,
    a nie pole na liście ustawień: GET pokazuje informację, POST przyjmuje wyłącznie formularz
    z zaznaczonym oświadczeniem **i** z wersją informacji równą bieżącej – formularz otwarty przed
    zmianą treści (nowe wydanie) jest odrzucany, żeby zapisana wersja była tą, którą naprawdę
    pokazano. Strona działa bez JavaScriptu.
    """

    template_name = "web/coordinator/ai_dpa_confirm.html"

    def _disclosure(self, provider: str):
        from apps.ai_grading.disclosures import DISCLOSURES

        if provider not in DISCLOSURES:
            raise Http404("Nie ma takiego dostawcy.")
        return DISCLOSURES[provider]

    def get(self, request, provider: str):
        competition = self.ai_competition
        return self._render(request, competition, self._disclosure(provider))

    def post(self, request, provider: str):
        competition = self.ai_competition
        info = self._disclosure(provider)
        back = reverse("web:coordinator-ai-grading") + f"#dostawca-{provider}"
        if request.POST.get("ack") != "on":
            return self._render(
                request,
                competition,
                info,
                error="Zaznacz oświadczenie, że zapoznałeś(-aś) się z informacjami i że umowa jest zawarta.",
                status=400,
            )
        if request.POST.get("info_version") != info.version:
            return self._render(
                request,
                competition,
                info,
                error="Informacja o dostawcy zmieniła się od otwarcia tej strony – przeczytaj ją ponownie.",
                status=409,
            )
        try:
            account, changed = ai.set_dpa_confirmation(
                competition,
                provider,
                True,
                actor=request.user,
                note=request.POST.get("note", ""),
                info_version=info.version,
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect(back)
        if changed:
            messages.success(
                request,
                f"Potwierdzenie umowy powierzenia z {info.label} zapisane – prace uczestników mogą trafiać "
                "do tego dostawcy.",
            )
        else:
            messages.info(request, f"Umowa powierzenia z {info.label} była już potwierdzona.")
        return redirect(back)

    def _render(self, request, competition, info, *, error: str = "", status: int = 200):
        account = ai.accounts_for(competition)[info.provider]
        context = {"info": info, "account": account, "error": error}
        return TemplateResponse(request, self.template_name, context, status=status)


class AiGenerateView(AiCoordinatorMixin, View):
    """``POST /coordinator/problems/<pk>/ai/generate/`` – podgląd (liczba i koszt), potem zlecenie.

    Jedna praca albo całe zadanie: pole ``submission`` zawęża zlecenie do jednej wersji pracy
    (przycisk przy wierszu), jego brak – do wszystkich najnowszych wersji zadania. Pole ``target``
    (``dostawca:model``) wybiera dostawcę i model; bez niego – domyślne z ustawień. Formularz jedzie
    drugi raz w komplecie, z **rozstrzygniętym** dostawcą i modelem, więc nie da się zlecić czegoś
    innego, niż pokazał podgląd.
    """

    def post(self, request, pk: int):
        problem = self.problem(pk)
        competition = self.ai_competition
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
            if request.POST.get("target"):
                provider, model = ai.parse_target(
                    request.POST["target"], request.POST.get("custom_model", "")
                )
            else:
                provider, model = ai.default_target(competition)
            ai.assert_can_request(competition, provider, model)
            if request.POST.get("action") != ACTION_CONFIRM:
                plan = ai.plan_generation(
                    problem,
                    regenerate=regenerate,
                    submission_ids=submission_ids,
                    provider=provider,
                    model=model,
                )
                context = {
                    "problem": problem,
                    "stage": problem.stage,
                    "plan": plan,
                    "regenerate": regenerate,
                    "submission_id": submission_id,
                    "back_url": back,
                    "targets": ai.targets(competition, for_tests=False),
                    "unpriced_warning": plan.estimate_usd is None,
                }
                return TemplateResponse(request, CONFIRM_TEMPLATE, context)
            plan = ai.request_generation(
                problem,
                regenerate=regenerate,
                submission_ids=submission_ids,
                actor=request.user,
                request=request,
                provider=provider,
                model=model,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect(back)
        if plan.count:
            messages.success(
                request,
                f"Zlecono ocenę AI ({plan.provider_label}, {plan.model}) dla {plan.count} prac. Oceny liczą "
                "się po kolei w tle – postęp widać w sekcji „Ocena AI” poniżej.",
            )
        else:
            messages.info(
                request,
                "Nie było czego zlecić – wszystkie prace mają już ocenę AI tym modelem albo są w toku.",
            )
        return redirect(back)


class AiTestWorkView(AiCoordinatorMixin, View):
    """``POST /coordinator/problems/<pk>/ai/test/`` – praca testowa: wgranie, ocena, usuwanie.

    Ocena pracy testowej jest jednoetapowa (bez podglądu kosztu): to zawsze jedna praca, a wybór
    dostawcy i modelu stoi przy przycisku. Bramka umowy powierzenia jej nie dotyczy
    (``apps.ai_grading.sandbox``) – pozostałe bramki, w tym limit wydatków, tak.
    """

    def post(self, request, pk: int):
        problem = self.problem(pk)
        back = reverse("web:coordinator-problem", kwargs={"pk": problem.pk}) + "#ocena-ai-test"
        action = request.POST.get("action", "")
        try:
            if action == "upload":
                form = TestWorkForm(request.POST, request.FILES)
                if not form.is_valid():
                    messages.error(request, "Wybierz plik pracy testowej.")
                    return redirect(back)
                sandbox.upload_test_work(
                    problem,
                    form.cleaned_data["file"],
                    actor=request.user,
                    no_personal_data=form.cleaned_data["no_personal_data"],
                    label=form.cleaned_data["label"],
                    request=request,
                )
                messages.success(
                    request,
                    "Praca testowa wgrana. Po skanie antywirusowym można ją ocenić dowolnym dostawcą.",
                )
            elif action == "generate":
                work = self._work(problem, request.POST.get("work", ""))
                provider, model = ai.parse_target(
                    request.POST.get("target", ""), request.POST.get("custom_model", "")
                )
                sandbox.request_test_assessment(
                    work, provider=provider, model=model, actor=request.user, request=request
                )
                messages.success(
                    request,
                    f"Zlecono ocenę testową ({ai.provider_label(provider)}, {model}). Wynik pojawi się "
                    "poniżej.",
                )
            elif action == "delete_work":
                sandbox.delete_test_work(
                    self._work(problem, request.POST.get("work", "")), actor=request.user, request=request
                )
                messages.success(request, "Praca testowa i jej oceny usunięte.")
            elif action == "delete_assessment":
                assessment = self._assessment(problem, request.POST.get("assessment", ""))
                sandbox.delete_test_assessment(assessment, actor=request.user, request=request)
                messages.success(request, "Ocena testowa usunięta.")
            else:
                messages.error(request, "Nieznana czynność.")
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        return redirect(back)

    def _work(self, problem, value: str) -> AiTestWork:
        if not value.isdigit():
            raise Http404("Nie ma takiej pracy testowej.")
        return get_object_or_404(
            AiTestWork.objects.for_competition(self.ai_competition).select_related("competition", "problem"),
            pk=int(value),
            problem=problem,
        )

    def _assessment(self, problem, value: str) -> AiAssessment:
        if not value.isdigit():
            raise Http404("Nie ma takiej oceny.")
        return get_object_or_404(
            AiAssessment.objects.for_competition(self.ai_competition).select_related("test_work"),
            pk=int(value),
            test_work__problem=problem,
        )


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
