"""Edytor przebiegu zawodów: tor etapów, reguły przejścia kroku i komponenty etapu.

Trzy ekrany za jedną flagą (``process_editor``), bo są trzema warstwami tej samej decyzji: **co
po czym** (``/coordinator/pipeline/``), **kto idzie dalej** (``…/<krok>/rules/``) i **z czego
składa się jeden etap** (``/coordinator/stages/<id>/components/``). Rozdzielenie ich na trzy
moduły znaczyłoby trzy kopie tej samej bramki i trzy miejsca, w których trzeba pamiętać, że
przebieg wyrażony w danych ma dawać dokładnie te same tabele, co przebieg wyrażony w kodzie.

**Dlaczego ekrany są za flagą, a adres bez flagi daje 404.** Dopóki flaga jest wyłączona,
kolejność etapów czyta się ze stałej ``STAGE_ORDER``, a próg z ``QualificationRule``
(``apps.results.services``); wiersze ``PipelineStep`` leżą w bazie nieużywane, wpisane migracją
z dzisiejszego przebiegu. Ekran, który pozwalałby je wtedy przestawiać, obiecywałby zmianę
kolejności zawodów, której żaden uczestnik by nie zobaczył – czyli kłamałby w sprawie, w której
kłamać najmniej wolno. 404, a nie 403 (``docs/UNIWERSALNY-ETAP-2.md`` § 2.1): adresu, którego
w tej instalacji nie ma, nie ma tak samo jak adresu cudzego konkursu. Rola sprawdza się wcześniej
i osobno (``CoordinatorRequiredMixin``), więc uczestnik dostaje 403 niezależnie od flagi i nie
dowiaduje się z odpowiedzi, jak ten konkurs jest skonfigurowany.

**Miejsce w kolejce jest pozycją na liście, a nie wartością pola.** Więz
``competitions_pipelinestep_unique_position`` pilnuje, że jedno miejsce zajmuje jeden krok, więc
„ustaw krok na miejsce 2” bez przenumerowania reszty kończyłoby się albo ``IntegrityError``, albo
dwiema listami: tą w bazie i tą na ekranie. Każda zmiana kolejności przechodzi więc przez
:func:`_renumber`, które zapisuje **całą** kolejkę w dwóch przebiegach (najpierw na miejsca ponad
dotychczasowym maksimum, potem na docelowe 1…N) – bo więz jest sprawdzany po każdym wierszu
z osobna, a nie na końcu transakcji.

**Podgląd przed zapisem.** Reguła przejścia jest jedyną rzeczą na tych ekranach, której skutku nie
widać w chwili kliknięcia: zmienia się skład następnego etapu, a ten powstaje dopiero przy
przeliczeniu. Dlatego ekran reguł ma podgląd (``apps.results.simulation.simulate_transition``)
liczony **tą samą** drogą, co późniejsza kwalifikacja – gdyby miał własną kopię progu, pokazywałby
prędzej czy później inny skład niż ogłoszenie i byłby gorszy niż jego brak.

Audyt pisze widok, bo te ekrany nie mają pod sobą serwisu domenowego (przebieg jest konfiguracją,
a nie czynnością na danych uczestników). Do wpisu idą **wyłącznie identyfikatory i parametry** –
żadnych nazwisk, pseudonimów ani liczby osób, których reguła dotyczy.
"""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.competitions.models import Category as ParticipantCategory
from apps.competitions.models import (
    PipelineStep,
    Stage,
    StageComponent,
    StageKind,
    TransitionRule,
)
from apps.competitions.services import create_stage, current_edition
from apps.core.api import DomainError
from apps.core.models import audit
from apps.results.services import PROCESS_EDITOR_FLAG
from apps.results.simulation import build_transition_rule, simulate_transition
from apps.web.coordinator_forms import (
    PipelineStepCreateForm,
    PipelineStepForm,
    StageComponentForm,
    TransitionRuleForm,
)
from apps.web.forms import StageCreateForm
from apps.web.mixins import CoordinatorRequiredMixin

PIPELINE_TEMPLATE = "web/coordinator/pipeline.html"
RULES_TEMPLATE = "web/coordinator/pipeline_rules.html"
COMPONENTS_TEMPLATE = "web/coordinator/components.html"
COMPONENT_FORM_TEMPLATE = "web/coordinator/components_form.html"

#: Przełącznik, który włącza te trzy ekrany – ta sama stała, którą czyta kwalifikacja
#: (``apps.results.services``). Napis w dwóch miejscach dałby ekran włączony flagą, której nikt
#: nie czyta przy liczeniu wyników.
FEATURE = PROCESS_EDITOR_FLAG

#: Wartość parametru ``preview`` dla podglądu progu **zapisanego**. Podgląd kosztuje przeliczenie
#: całego etapu, więc nie liczy się go przy każdym wejściu na ekran – trzeba o niego poprosić.
PREVIEW_CURRENT = "current"


# --- wspólna bramka -----------------------------------------------------------------------------


class PipelineScreenMixin(CoordinatorRequiredMixin):
    """Wspólna bramka ekranów edytora: rola koordynatora, flaga konkursu, zakres querysetu."""

    def competition_or_404(self, request):
        """Konkurs żądania, o ile ma włączony edytor przebiegu. Inaczej 404.

        Konkurs bierzemy z ``request.competition`` (ustawia go ``CompetitionMiddleware``), a nie
        z identyfikatora w adresie – dzięki temu nie ma tu adresu, pod którym dałoby się wskazać
        cudzy przebieg. Sprawdzenie stoi w metodzie widoku, a nie w ``dispatch``, bo bramkę roli
        stawia wcześniej ``RoleRequiredMixin.dispatch``: anonim i uczestnik mają dostać 302 albo
        403, **zanim** odpowiedź zdradzi stan przełącznika tego konkursu.
        """
        competition = getattr(request, "competition", None)
        if competition is None or not competition.has_feature(FEATURE):
            raise Http404("Ten konkurs czyta przebieg zawodów z kodu – edytor jest wyłączony.")
        return competition

    def step_or_404(self, competition, step_id: int) -> PipelineStep:
        """Krok **tego konkursu** albo 404 – zakres wychodzi z querysetu, nie z widoku (§ 3.6)."""
        return get_object_or_404(
            PipelineStep.objects.for_competition(competition).select_related("stage", "edition"),
            pk=step_id,
        )

    def stage_or_404(self, competition, stage_id: int) -> Stage:
        """Etap **tego konkursu** albo 404 – jedno wejście dla ekranu komponentów."""
        return get_object_or_404(
            Stage.objects.for_competition(competition).select_related("edition"), pk=stage_id
        )


def _apply_model_errors(form, error: ValidationError) -> None:
    """Przenosi komunikaty ``full_clean()`` pod pola formularza.

    Reguły domenowe stoją w ``clean()`` modeli (kolejność klas kategorii, komplet parametrów
    trybu, dodatni mianownik wagi) i nie są powtórzone w formularzach. Komunikat musi jednak
    stanąć pod właściwym polem, a nie w chmurce nad formularzem – inaczej koordynator czyta
    „popraw formularz” i nie wie który. Pola, którego formularz nie ma (np. ``competition``),
    nie da się podpisać, więc jego komunikat idzie do błędów ogólnych.
    """
    for field, texts in error.message_dict.items():
        form.add_error(field if field in form.fields else None, texts)


# --- kolejka kroków -----------------------------------------------------------------------------


def _ordered_steps(edition) -> list[PipelineStep]:
    """Kroki **na torze** w kolejności zawodów. Kroki poza torem nie mają tu czego szukać."""
    return list(
        PipelineStep.objects.filter(edition=edition, off_pipeline=False)
        .select_related("stage")
        .order_by("position", "id")
    )


def _free_position(edition) -> int:
    """Miejsce, którego w tej edycji na pewno nikt nie zajmuje – parking na czas przestawiania."""
    top = PipelineStep.objects.filter(edition=edition).aggregate(models.Max("position"))
    return (top["position__max"] or 0) + 1


def _renumber(edition, ordered: list[PipelineStep]) -> None:
    """Zapisuje kolejkę jako miejsca 1…N, w dwóch przebiegach.

    Dwa przebiegi, a nie jeden, bo więz ``(edycja, miejsce)`` jest sprawdzany po każdym wierszu
    z osobna: zamiana miejscami dwóch sąsiadów zapisana wprost przewróciłaby się na pierwszym
    ``UPDATE``. Pierwszy przebieg odkłada całą kolejkę **ponad** dotychczasowe maksimum (te
    miejsca są z definicji wolne), drugi sprowadza ją na 1…N (te są wtedy wolne, bo wszyscy stoją
    wyżej). Kosztuje to dwa zapisy na krok, a kroków w edycji jest kilka – to jest cena za to,
    że kolejność w bazie i kolejność na ekranie nie mogą się rozjechać.
    """
    if not ordered:
        return
    offset = _free_position(edition)
    for index, step in enumerate(ordered):
        step.position = offset + index
        step.full_clean()
        step.save(update_fields=["position"])
    for index, step in enumerate(ordered, start=1):
        step.position = index
        step.full_clean()
        step.save(update_fields=["position"])


@transaction.atomic
def _place_step(step: PipelineStep, *, position: int, off_pipeline: bool) -> None:
    """Ustawia krok na wskazanym miejscu (albo poza torem) i przenumerowuje resztę kolejki.

    Miejsce większe niż długość kolejki znaczy „na koniec”, a nie błąd: koordynator, który chce
    przesunąć krok na sam koniec, nie ma obowiązku wiedzieć, ile kroków ma dziś edycja.
    """
    edition = step.edition
    others = [other for other in _ordered_steps(edition) if other.pk != step.pk]
    step.off_pipeline = off_pipeline
    step.position = _free_position(edition)
    step.full_clean()
    step.save(update_fields=["position", "off_pipeline"])
    if off_pipeline:
        step.position = 0
        step.full_clean()
        step.save(update_fields=["position"])
        _renumber(edition, others)
        return
    index = min(max(position, 1), len(others) + 1) - 1
    others.insert(index, step)
    _renumber(edition, others)


@transaction.atomic
def _move_step(step: PipelineStep, delta: int) -> bool:
    """Przesuwa krok o jedno miejsce. ``False`` = krok jest już na skraju kolejki albo poza torem."""
    if step.off_pipeline:
        return False
    ordered = _ordered_steps(step.edition)
    positions = [item.pk for item in ordered]
    if step.pk not in positions:
        return False
    index = positions.index(step.pk)
    target = index + delta
    if not 0 <= target < len(ordered):
        return False
    ordered[index], ordered[target] = ordered[target], ordered[index]
    _renumber(step.edition, ordered)
    return True


def _stages_without_step(edition):
    """Etapy edycji, które nie mają jeszcze swojego kroku – lista wyboru przy dopisywaniu."""
    return Stage.objects.filter(edition=edition, pipeline_step__isnull=True).order_by("opens_at", "id")


def _render_pipeline(request, edition, *, step_form=None, stage_form=None, status: int = 200):
    """Ekran toru etapów. Wspólny dla wejścia i dla każdego nieudanego zapisu.

    Formularz miejsca powstaje **osobny dla każdego wiersza** i jest niezwiązany: pola mają w
    każdym wierszu te same nazwy, a rozróżnia je adres akcji, który niesie identyfikator kroku.
    Jedna instancja na całą tabelę pokazywałaby miejsce pierwszego kroku w każdym wierszu.
    """
    steps = (
        list(
            PipelineStep.objects.filter(edition=edition)
            .select_related("stage")
            .annotate(rule_count=models.Count("transition_rules"))
            .order_by("off_pipeline", "position", "id")
        )
        if edition is not None
        else []
    )
    rows = [
        {
            "step": step,
            "form": PipelineStepForm(
                initial={"position": step.position or 1, "off_pipeline": step.off_pipeline}
            ),
        }
        for step in steps
    ]
    context = {
        "edition": edition,
        "rows": rows,
        "step_form": step_form
        if step_form is not None
        else PipelineStepCreateForm(stages=_stages_without_step(edition) if edition else None),
        "stage_form": stage_form if stage_form is not None else StageCreateForm(kind_choices=_round_choice()),
        "round_kind": StageKind.ROUND,
    }
    return TemplateResponse(request, PIPELINE_TEMPLATE, context, status=status)


def _round_choice() -> list[tuple[str, str]]:
    """Jedyny rodzaj etapu, który wolno dołożyć **z tego** ekranu.

    Pozostałe rodzaje mają własny ekran (``/coordinator/stages/new/``) i własną regułę „jeden na
    edycję”. Runda jest wyjątkiem, bo jej miejsce w zawodach bierze się wyłącznie z kroku – więc
    dokłada się ją tam, gdzie widać kolejkę, a nie na liście etapów, gdzie nie widać.
    """
    return [(StageKind.ROUND.value, StageKind.ROUND.label)]


class PipelineView(PipelineScreenMixin, View):
    """``GET /coordinator/pipeline/`` – tor etapów bieżącej edycji."""

    def get(self, request):
        competition = self.competition_or_404(request)
        return _render_pipeline(request, current_edition(competition))


class PipelineStepCreateView(PipelineScreenMixin, View):
    """``POST /coordinator/pipeline/steps/`` – dopisanie kroku dla istniejącego etapu."""

    def post(self, request):
        competition = self.competition_or_404(request)
        edition = current_edition(competition)
        if edition is None:
            messages.error(request, "Nie ustawiono bieżącej edycji – krok nie ma do czego należeć.")
            return redirect(reverse("web:coordinator-pipeline"))
        form = PipelineStepCreateForm(request.POST, stages=_stages_without_step(edition))
        if not form.is_valid():
            return _render_pipeline(request, edition, step_form=form, status=400)
        stage = form.cleaned_data["stage"]
        off_pipeline = form.cleaned_data["off_pipeline"]
        step = PipelineStep(
            edition=edition,
            stage=stage,
            position=0 if off_pipeline else _free_position(edition),
            off_pipeline=off_pipeline,
        )
        try:
            with transaction.atomic():
                step.full_clean()
                step.save()
                if not off_pipeline:
                    _renumber(edition, _ordered_steps(edition))
        except ValidationError as error:
            _apply_model_errors(form, error)
            return _render_pipeline(request, edition, step_form=form, status=400)
        audit(
            request.user,
            "pipeline_step.created",
            step,
            {"stage_id": stage.pk, "kind": stage.kind, "position": step.position, "off": off_pipeline},
            request=request,
        )
        messages.success(request, f"Etap {stage.display_name} wszedł do przebiegu edycji.")
        return redirect(reverse("web:coordinator-pipeline"))


class PipelineStepUpdateView(PipelineScreenMixin, View):
    """``POST /coordinator/pipeline/<krok>/`` – miejsce w kolejce i udział w torze zawodów."""

    def post(self, request, step_id: int):
        competition = self.competition_or_404(request)
        step = self.step_or_404(competition, step_id)
        form = PipelineStepForm(request.POST)
        if not form.is_valid():
            # Formularz miejsca jest jednopolowy i powtórzony w każdym wierszu, więc błąd wraca
            # komunikatem nad tabelą, a nie pod polem: pod którym z kilkunastu identycznych pól
            # miałby stanąć, wiedziałby wyłącznie adres akcji, a nie formularz.
            messages.error(request, "Miejsce w kolejce musi być liczbą całkowitą nie mniejszą niż 1.")
            return _render_pipeline(request, step.edition, status=400)
        before = {"position": step.position, "off": step.off_pipeline}
        try:
            _place_step(
                step,
                position=form.cleaned_data["position"],
                off_pipeline=form.cleaned_data["off_pipeline"],
            )
        except ValidationError as error:
            messages.error(
                request,
                "; ".join(message for texts in error.message_dict.values() for message in texts),
            )
            return _render_pipeline(request, step.edition, status=400)
        audit(
            request.user,
            "pipeline_step.updated",
            step,
            {
                "stage_id": step.stage_id,
                "before": before,
                "after": {"position": step.position, "off": step.off_pipeline},
            },
            request=request,
        )
        messages.success(request, f"Krok etapu {step.stage.display_name} został przestawiony.")
        return redirect(reverse("web:coordinator-pipeline"))


class PipelineStepMoveView(PipelineScreenMixin, View):
    """``POST /coordinator/pipeline/<krok>/move/<kierunek>/`` – przesunięcie o jedno miejsce.

    Osobny adres na kierunek, a nie jeden z polem „kierunek”: rozróżnianie po nazwie wciśniętego
    przycisku zależy od tego, czy przeglądarka ją przyśle, a przy wysyłce klawiaturą nie zawsze
    przysyła (ta sama reguła, co na ekranie integracji).
    """

    #: Kierunki i ich przesunięcie na liście. Napis w adresie, a nie ``-1``/``1``, bo adres z minusem
    #: bywa łamany przez filtry proxy i jest nieczytelny w logu.
    DIRECTIONS = {"up": -1, "down": 1}

    def post(self, request, step_id: int, direction: str):
        competition = self.competition_or_404(request)
        step = self.step_or_404(competition, step_id)
        delta = self.DIRECTIONS.get(direction)
        if delta is None:
            raise Http404("Nieznany kierunek przesunięcia kroku.")
        before = step.position
        if not _move_step(step, delta):
            messages.info(request, "Ten krok jest już na skraju kolejki – nic się nie zmieniło.")
            return redirect(reverse("web:coordinator-pipeline"))
        audit(
            request.user,
            "pipeline_step.updated",
            step,
            {"stage_id": step.stage_id, "before": {"position": before}, "after": {"position": step.position}},
            request=request,
        )
        messages.success(request, f"Krok etapu {step.stage.display_name} zmienił miejsce w kolejce.")
        return redirect(reverse("web:coordinator-pipeline"))


class PipelineStepDeleteView(PipelineScreenMixin, View):
    """``POST /coordinator/pipeline/<krok>/delete/`` – wyjęcie etapu z przebiegu.

    Etap **zostaje**; znika wyłącznie jego miejsce w kolejce i reguły przejścia, które na nim
    wisiały (``CASCADE``). To jest różnica, którą trzeba widzieć: usunięcie kroku nie kasuje
    wyników ani wpisów, ale zabiera etapowi następnika – czyli przy włączonym edytorze nikt
    z niego dalej nie przejdzie, dopóki krok nie wróci.
    """

    def post(self, request, step_id: int):
        competition = self.competition_or_404(request)
        step = self.step_or_404(competition, step_id)
        edition, stage = step.edition, step.stage
        rules = step.transition_rules.count()
        with transaction.atomic():
            step.delete()
            _renumber(edition, _ordered_steps(edition))
        audit(
            request.user,
            "pipeline_step.deleted",
            stage,
            {"stage_id": stage.pk, "kind": stage.kind, "rules": rules},
            request=request,
        )
        messages.success(request, f"Etap {stage.display_name} wyszedł z przebiegu edycji.")
        return redirect(reverse("web:coordinator-pipeline"))


class RoundStageCreateView(PipelineScreenMixin, View):
    """``POST /coordinator/pipeline/rounds/`` – nowa runda razem z jej miejscem w kolejce.

    Runda powstaje przez ``create_stage``, więc od razu ma domyślną skalę punktacji i próg –
    etap bez nich byłby niemożliwy do oceniania. Krok dokładamy w tej samej transakcji: runda bez
    kroku nie ma miejsca w zawodach, bo ``STAGE_ORDER`` jej nie zna (patrz ``missing_stage_kinds``).
    """

    def post(self, request):
        competition = self.competition_or_404(request)
        edition = current_edition(competition)
        if edition is None:
            messages.error(request, "Nie ustawiono bieżącej edycji – etap nie ma do czego należeć.")
            return redirect(reverse("web:coordinator-pipeline"))
        form = StageCreateForm(request.POST, kind_choices=_round_choice())
        if not form.is_valid():
            return _render_pipeline(request, edition, stage_form=form, status=400)
        data = dict(form.cleaned_data)
        try:
            with transaction.atomic():
                stage = create_stage(edition=edition, **data)
                step = PipelineStep(edition=edition, stage=stage, position=_free_position(edition))
                step.full_clean()
                step.save()
                _renumber(edition, _ordered_steps(edition))
        except ValidationError as error:
            _apply_model_errors(form, error)
            return _render_pipeline(request, edition, stage_form=form, status=400)
        audit(
            request.user,
            "stage.created",
            stage,
            {"kind": stage.kind, "format": stage.format, "position": step.position},
            request=request,
        )
        messages.success(
            request,
            f"Runda {stage.display_name} została dodana razem z miejscem {step.position} w kolejce.",
        )
        return redirect(reverse("web:coordinator-pipeline"))


# --- reguły przejścia kroku ---------------------------------------------------------------------


def _rule_form(competition, step: PipelineStep, data=None, instance=None) -> TransitionRuleForm:
    """Formularz reguły z listą kategorii **tego** konkursu i z krokiem wpisanym w instancję."""
    target = instance if instance is not None else TransitionRule(step=step)
    return TransitionRuleForm(
        data,
        instance=target,
        categories=ParticipantCategory.objects.for_competition(competition),
    )


def _render_rules(
    request, competition, step, form, *, editing=None, preview=None, error=None, status: int = 200
):
    """Ekran reguł kroku. Wspólny dla wejścia, podglądu, dopisania i zmiany.

    ``editing`` rozstrzyga, dokąd prowadzi przycisk zapisu: do dopisania nowej reguły albo do
    zmiany tej wskazanej. Adres zapisu jedzie do szablonu gotowy, a nie jako warunek w szablonie,
    bo to jest decyzja widoku – ten sam powód, dla którego flagi nie woła się w szablonie (§ 2.1).
    """
    context = {
        "step": step,
        "stage": step.stage,
        "rules": list(step.transition_rules.select_related("category").order_by("position", "id")),
        "form": form,
        "editing": editing,
        "save_action": reverse("web:coordinator-pipeline-rule", args=[step.pk, editing.pk])
        if editing is not None
        else reverse("web:coordinator-pipeline-rule-create", args=[step.pk]),
        "preview_action": reverse("web:coordinator-pipeline-rule-preview", args=[step.pk]),
        "preview": preview,
        "error": error,
        "preview_current": PREVIEW_CURRENT,
    }
    return TemplateResponse(request, RULES_TEMPLATE, context, status=status)


def _rule_of_step(step: PipelineStep, rule_id) -> TransitionRule | None:
    """Reguła **tego** kroku albo 404. ``None`` na wejściu znaczy „dopisujemy nową”.

    Zakres wychodzi z relacji (``step.transition_rules``), a nie z widoku: reguła cudzego kroku
    ma dać 404 tak samo, jak reguła cudzego konkursu, i nie wymaga do tego osobnego sprawdzenia.
    """
    if rule_id in (None, ""):
        return None
    return get_object_or_404(step.transition_rules.select_related("category"), pk=rule_id)


def _candidate_rule(step: PipelineStep, form: TransitionRuleForm) -> TransitionRule:
    """Niezapisana reguła z formularza – walidowana tak samo, jak przy zapisie.

    Przez ``build_transition_rule``, a nie „nowy obiekt z ``cleaned_data``”: to jest jedyne
    wejście do podglądu reguły przejścia i ono woła ``full_clean`` modelu, więc nie da się
    obejrzeć progu, którego potem nie da się zapisać.
    """
    return build_transition_rule(
        step.stage,
        form.cleaned_data["mode"],
        min_points=form.cleaned_data["min_points"],
        top_n=form.cleaned_data["top_n"],
        percentile=form.cleaned_data["percentile"],
        group_by=form.cleaned_data["group_by"],
        category=form.cleaned_data["category"],
    )


def _simulate(step: PipelineStep, rules):
    """Podgląd albo komunikat. Odmowa domenowa nie ma prawa wywrócić strony z formularzem."""
    try:
        return simulate_transition(step.stage, rules=rules), None
    except DomainError as exc:
        return None, str(exc.detail)


class PipelineRulesView(PipelineScreenMixin, View):
    """``GET /coordinator/pipeline/<krok>/rules/`` – reguły przejścia kroku.

    Samo wejście **nic nie liczy**: podgląd jest pełnym przeliczeniem etapu, a ekran otwiera się
    także po to, żeby przeczytać, co jest zapisane. Skutek reguł zapisanych pokazuje osobne
    żądanie ``?preview=current`` – „co daje to, co mamy teraz”, razem z odwrotem na
    ``QualificationRule`` dla kroku, którego edytor jeszcze nie tknął. Parametr jedzie w adresie,
    więc wynik da się odświeżyć i wkleić reszcie komitetu.
    """

    def get(self, request, step_id: int):
        competition = self.competition_or_404(request)
        step = self.step_or_404(competition, step_id)
        preview = error = None
        if request.GET.get("preview") == PREVIEW_CURRENT:
            preview, error = _simulate(step, None)
        return _render_rules(
            request, competition, step, _rule_form(competition, step), preview=preview, error=error
        )


class TransitionRulePreviewView(PipelineScreenMixin, View):
    """``POST /coordinator/pipeline/<krok>/rules/preview/`` – „co by było, gdyby zapisać tę regułę”.

    Podgląd bierze reguły **zapisane plus** tę z formularza, bo kilka reguł na kroku się sumuje:
    sama nowa reguła pokazywałaby skład węższy niż późniejsze przeliczenie. Przy zmianie reguły
    już zapisanej (pole ``rule``) jej wersja z bazy z tego zestawu **wypada** – inaczej podgląd
    liczyłby sumę starej i nowej wartości tego samego wiersza, czyli stan, który nigdy nie zaistnieje.

    ``POST``, a nie ``GET``, mimo że nic nie zapisuje: parametry przyjeżdżają z tego samego
    formularza, co zapis (jeden komplet pól, dwa adresy w ``formaction``), a formularz panelu ma
    token CSRF – wysłanie go metodą ``GET`` postawiłoby token w adresie, czyli w historii
    przeglądarki i w logu pośrednika.
    """

    def post(self, request, step_id: int):
        competition = self.competition_or_404(request)
        step = self.step_or_404(competition, step_id)
        editing = _rule_of_step(step, request.POST.get("rule"))
        form = _rule_form(competition, step, data=request.POST, instance=editing)
        if not form.is_valid():
            return _render_rules(request, competition, step, form, editing=editing, status=400)
        try:
            candidate = _candidate_rule(step, form)
        except DomainError as exc:
            return _render_rules(
                request, competition, step, form, editing=editing, error=str(exc.detail), status=400
            )
        saved = [
            rule
            for rule in step.transition_rules.order_by("position", "id")
            if editing is None or rule.pk != editing.pk
        ]
        preview, error = _simulate(step, [*saved, candidate])
        return _render_rules(request, competition, step, form, editing=editing, preview=preview, error=error)


class TransitionRuleCreateView(PipelineScreenMixin, View):
    """``POST /coordinator/pipeline/<krok>/rules/new/`` – dopisanie reguły do kroku."""

    def post(self, request, step_id: int):
        competition = self.competition_or_404(request)
        step = self.step_or_404(competition, step_id)
        form = _rule_form(competition, step, data=request.POST)
        if not form.is_valid():
            return _render_rules(request, competition, step, form, status=400)
        rule = form.save(commit=False)
        rule.step = step
        try:
            rule.full_clean()
        except ValidationError as error:
            _apply_model_errors(form, error)
            return _render_rules(request, competition, step, form, status=400)
        rule.save()
        audit(request.user, "transition_rule.created", rule, _rule_diff(rule), request=request)
        messages.success(request, f"Reguła „{rule.get_mode_display()}” została dopisana do kroku.")
        return redirect(reverse("web:coordinator-pipeline-rules", args=[step.pk]))


class TransitionRuleEditView(PipelineScreenMixin, View):
    """``GET|POST /coordinator/pipeline/<krok>/rules/<reguła>/`` – zmiana zapisanej reguły."""

    def get(self, request, step_id: int, rule_id: int):
        competition = self.competition_or_404(request)
        step = self.step_or_404(competition, step_id)
        rule = _rule_of_step(step, rule_id)
        return _render_rules(
            request, competition, step, _rule_form(competition, step, instance=rule), editing=rule
        )

    def post(self, request, step_id: int, rule_id: int):
        competition = self.competition_or_404(request)
        step = self.step_or_404(competition, step_id)
        rule = _rule_of_step(step, rule_id)
        before = _rule_diff(rule)
        form = _rule_form(competition, step, data=request.POST, instance=rule)
        if not form.is_valid():
            return _render_rules(request, competition, step, form, editing=rule, status=400)
        saved = form.save(commit=False)
        try:
            saved.full_clean()
        except ValidationError as error:
            _apply_model_errors(form, error)
            return _render_rules(request, competition, step, form, editing=rule, status=400)
        saved.save()
        audit(
            request.user,
            "transition_rule.updated",
            saved,
            {"before": before, "after": _rule_diff(saved)},
            request=request,
        )
        messages.success(request, f"Reguła „{saved.get_mode_display()}” została zapisana.")
        return redirect(reverse("web:coordinator-pipeline-rules", args=[step.pk]))


class TransitionRuleDeleteView(PipelineScreenMixin, View):
    """``POST /coordinator/pipeline/<krok>/rules/<reguła>/delete/`` – skasowanie reguły.

    Bez osobnego potwierdzenia: reguła jest parametrem, a nie dokumentem, a wpis audytowy
    zachowuje jej komplet, więc pomyłkę da się odtworzyć z historii. Krok **bez ani jednej**
    reguły nie znaczy jednak „nikt nie przechodzi”, tylko „nie skonfigurowano” – próg wraca wtedy
    do ``QualificationRule`` (``services.stage_qualification``) i o tym mówi komunikat.
    """

    def post(self, request, step_id: int, rule_id: int):
        competition = self.competition_or_404(request)
        step = self.step_or_404(competition, step_id)
        rule = get_object_or_404(step.transition_rules, pk=rule_id)
        diff = _rule_diff(rule)
        rule.delete()
        audit(request.user, "transition_rule.deleted", step, diff, request=request)
        if step.transition_rules.exists():
            messages.success(request, "Reguła została usunięta.")
        else:
            messages.warning(
                request,
                "To była ostatnia reguła tego kroku – próg wraca do reguły kwalifikacji zapisanej "
                "przy etapie.",
            )
        return redirect(reverse("web:coordinator-pipeline-rules", args=[step.pk]))


def _rule_diff(rule: TransitionRule) -> dict:
    """Komplet parametrów reguły do audytu – **kody i liczby**, ani jednego wiersza o człowieku."""
    return {
        "step_id": rule.step_id,
        "mode": rule.mode,
        "group_by": rule.group_by,
        "category": rule.category.code if rule.category_id else None,
        "min_points": rule.min_points,
        "top_n": rule.top_n,
        "percentile": rule.percentile,
        "position": rule.position,
    }


# --- komponenty etapu ---------------------------------------------------------------------------


def _render_components(request, stage: Stage, form: StageComponentForm, *, status: int = 200):
    """Ekran komponentów etapu. Wspólny dla wejścia i dla nieudanego dopisania."""
    context = {
        "stage": stage,
        "components": list(stage.components.order_by("position", "id")),
        "form": form,
    }
    return TemplateResponse(request, COMPONENTS_TEMPLATE, context, status=status)


def _component_diff(component: StageComponent) -> dict:
    """Parametry komponentu do audytu. Waga jako para liczb – dokładnie tak, jak leży w bazie."""
    return {
        "stage_id": component.stage_id,
        "kind": component.kind,
        "position": component.position,
        "weight": [component.weight_numerator, component.weight_denominator],
        "required": component.required,
    }


class StageComponentsView(PipelineScreenMixin, View):
    """``GET /coordinator/stages/<id>/components/`` – formy, z których składa się etap.

    Etap **bez ani jednego** komponentu liczy się dokładnie jak przed etapem 2: suma bez wag,
    źródło punktów z ``Stage.format``. Pierwszy komponent przełącza etap na sumowanie po
    komponentach – i to jest cały przełącznik obok flagi, więc ekran mówi o tym wprost.
    """

    def get(self, request, stage_id: int):
        competition = self.competition_or_404(request)
        stage = self.stage_or_404(competition, stage_id)
        return _render_components(request, stage, StageComponentForm())


class StageComponentCreateView(PipelineScreenMixin, View):
    """``POST /coordinator/stages/<id>/components/`` – dopisanie formy do etapu."""

    def post(self, request, stage_id: int):
        competition = self.competition_or_404(request)
        stage = self.stage_or_404(competition, stage_id)
        form = StageComponentForm(request.POST)
        if not form.is_valid():
            return _render_components(request, stage, form, status=400)
        component = form.save(commit=False)
        component.stage = stage
        try:
            component.full_clean()
        except ValidationError as error:
            _apply_model_errors(form, error)
            return _render_components(request, stage, form, status=400)
        component.save()
        audit(
            request.user,
            "stage_component.created",
            component,
            _component_diff(component),
            request=request,
        )
        messages.success(request, f"Komponent „{component.display_name}” został dodany do etapu.")
        return redirect(reverse("web:coordinator-stage-components", args=[stage.pk]))


class StageComponentEditView(PipelineScreenMixin, View):
    """``GET|POST /coordinator/stages/<id>/components/<komponent>/`` – zmiana jednej formy."""

    def get(self, request, stage_id: int, component_id: int):
        competition = self.competition_or_404(request)
        stage = self.stage_or_404(competition, stage_id)
        component = self._component(stage, component_id)
        return self._render(request, stage, component, StageComponentForm(instance=component))

    def post(self, request, stage_id: int, component_id: int):
        competition = self.competition_or_404(request)
        stage = self.stage_or_404(competition, stage_id)
        component = self._component(stage, component_id)
        before = _component_diff(component)
        form = StageComponentForm(request.POST, instance=component)
        if not form.is_valid():
            return self._render(request, stage, component, form, status=400)
        saved = form.save(commit=False)
        try:
            saved.full_clean()
        except ValidationError as error:
            _apply_model_errors(form, error)
            return self._render(request, stage, component, form, status=400)
        saved.save()
        audit(
            request.user,
            "stage_component.updated",
            saved,
            {"before": before, "after": _component_diff(saved)},
            request=request,
        )
        messages.success(request, f"Komponent „{saved.display_name}” został zapisany.")
        return redirect(reverse("web:coordinator-stage-components", args=[stage.pk]))

    @staticmethod
    def _component(stage: Stage, component_id: int) -> StageComponent:
        """Komponent **tego** etapu albo 404 – zakres wychodzi z relacji, nie z widoku."""
        return get_object_or_404(stage.components, pk=component_id)

    @staticmethod
    def _render(request, stage, component, form, *, status: int = 200):
        context = {"stage": stage, "component": component, "form": form}
        return TemplateResponse(request, COMPONENT_FORM_TEMPLATE, context, status=status)


class StageComponentDeleteView(PipelineScreenMixin, View):
    """``POST /coordinator/stages/<id>/components/<komponent>/delete/`` – usunięcie formy.

    Usunięcie **ostatniego** komponentu wraca etap do liczenia z ``Stage.format`` – czyli do
    zachowania sprzed etapu 2. To nie jest skutek uboczny, tylko droga odwrotu, więc komunikat
    mówi o niej wprost.
    """

    def post(self, request, stage_id: int, component_id: int):
        competition = self.competition_or_404(request)
        stage = self.stage_or_404(competition, stage_id)
        component = get_object_or_404(stage.components, pk=component_id)
        name, diff = component.display_name, _component_diff(component)
        component.delete()
        audit(request.user, "stage_component.deleted", stage, diff, request=request)
        if stage.components.exists():
            messages.success(request, f"Komponent „{name}” został usunięty.")
        else:
            messages.warning(
                request,
                f"Komponent „{name}” był ostatni – etap liczy się teraz z formy etapu, bez wag.",
            )
        return redirect(reverse("web:coordinator-stage-components", args=[stage.pk]))
