"""Narzędzia analityczne koordynatora: postęp oceniania, eksport, audyt i symulacja progu.

Cztery ekrany w jednym module, bo łączy je jedna cecha: **wszystkie są odczytem**. Żaden z nich
nie zmienia stanu zawodów – nawet symulacja, która jedynie pokazuje skutek reguły. Wyjątkami są
trzy akcje POST stojące na tych ekranach (przypomnienie recenzentowi, przypomnienie wszystkim
zaległym, zapisanie progu) i każda z nich jest osobnym kliknięciem z osobnym wpisem w audycie –
tak, żeby wejście na stronę nigdy niczego nie zmieniało.

Moduł jest osobny od ``coordinator.py`` z tego samego powodu, co ``coordinator_accounts.py``:
tam mieszkają akcje prowadzenia zawodów (zamknij etap, przydziel, opublikuj), tutaj – narzędzia
do patrzenia na nie. Wspólne zostają uprawnienia (``CoordinatorRequiredMixin``, 403 dla każdej
innej roli) i zasada, że reguła domenowa stoi w serwisie, a widok wyłącznie orkiestruje.

Cała nawigacja tych ekranów działa **bez JavaScriptu**: filtry i parametry symulacji jadą zwykłym
formularzem GET, stronicowanie audytu zwykłymi odnośnikami, a rozwijanie ``diff`` – znacznikiem
``<details>``. Ekran, na który patrzy się w trakcie awarii albo w sali komisji, nie może zależeć
od tego, czy skrypt się wczytał.
"""

from __future__ import annotations

from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.http import urlencode
from django.views.generic import TemplateView, View

from apps.accounts.models import CommitteeMember
from apps.competitions.models import QualificationMode, Stage
from apps.competitions.services import current_edition
from apps.core import audit_browser, exports
from apps.core.api import DomainError
from apps.core.models import audit
from apps.grading.reports import FALLBACK_OVERDUE_DAYS, review_has_due_at, reviewer_rows, stage_progress
from apps.results.simulation import apply_rule, simulate
from apps.web.coordinator_forms import AuditFilterForm, SimulationForm
from apps.web.mixins import ActionViewMixin, CoordinatorRequiredMixin

PROGRESS_TEMPLATE = "web/coordinator/reports.html"
AUDIT_TEMPLATE = "web/coordinator/audit.html"
EXPORT_TEMPLATE = "web/coordinator/export.html"
SIMULATION_TEMPLATE = "web/coordinator/simulation.html"


def _stage(stage_id: int) -> Stage:
    """Etap z doczytaną edycją i progiem – oba stoją w nagłówku każdego z tych ekranów."""
    return get_object_or_404(Stage.objects.select_related("edition", "qualification_rule"), pk=stage_id)


# --- 1. Pulpit postępu oceniania ----------------------------------------------------------------


class StageProgressView(CoordinatorRequiredMixin, TemplateView):
    """``/coordinator/stages/<id>/progress/`` – ile prac jest w którym stanie i kto zalega.

    Ekran odpowiada na dwa pytania, które w trakcie oceniania padają codziennie: „jak daleko
    jesteśmy” i „na kogo czekamy”. Pierwsze pokazuje pasek segmentowy z liczbami, drugie – tabela
    recenzentów z licznikiem zaległości i przyciskiem przypomnienia.

    Definicja zaległości zależy od tego, czy model recenzji ma własny termin (``Review.due_at``).
    Ekran **mówi wprost**, której używa: przybliżenie („przydzielone dawniej niż tydzień temu”)
    podane bez tego zastrzeżenia zostałoby wzięte za termin regulaminowy, a nim nie jest.
    """

    template_name = PROGRESS_TEMPLATE

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        stage = _stage(self.kwargs["stage_id"])
        context.update(
            {
                "stage": stage,
                "progress": stage_progress(stage),
                "reviewers": reviewer_rows(stage),
                "has_due_at": review_has_due_at(),
                "fallback_days": FALLBACK_OVERDUE_DAYS,
            }
        )
        return context


class RemindReviewersView(ActionViewMixin, CoordinatorRequiredMixin, View):
    """POST: przypomnienie o niedokończonych recenzjach – jednej osobie albo wszystkim zaległym.

    Jeden adres dla obu przycisków, bo różni je wyłącznie zakres: obecność ``reviewer_id``
    w formularzu znaczy „ta osoba”, jego brak – „wszyscy, którzy mają coś po terminie”. Dwa
    adresy znaczyłyby dwa widoki robiące to samo wywołanie serwisu.
    """

    def get_success_url(self, stage_id: int, *args, **kwargs) -> str:
        return reverse("web:coordinator-stage-progress", args=[stage_id])

    def perform(self, request, stage_id: int) -> str:
        from apps.grading.reports import remind_reviewers

        stage = _stage(stage_id)
        raw_id = (request.POST.get("reviewer_id") or "").strip()
        member = None
        if raw_id:
            member = get_object_or_404(CommitteeMember.objects.select_related("user"), pk=raw_id)
        summary = remind_reviewers(
            stage,
            member=member,
            only_overdue=member is None,
            actor=request.user,
            request=request,
        )
        if not summary["reviewers"]:
            return "Nie ma komu przypominać – nikt nie ma zaległych recenzji w tym etapie."
        return (
            f"Wysłano przypomnienia do {summary['reviewers']} recenzentów "
            f"({summary['reviews']} recenzji, w tym {summary['overdue']} po terminie)."
        )


# --- 3. Eksport danych --------------------------------------------------------------------------


class ExportIndexView(CoordinatorRequiredMixin, TemplateView):
    """``/coordinator/export/`` – spis dostępnych eksportów z odnośnikami do plików.

    Strona jest wyłącznie spisem treści: każdy odnośnik prowadzi do widoku, który plik naprawdę
    buduje. Rozdział jest praktyczny – lista ma się otwierać natychmiast, a policzenie tabeli
    wyników etapu trwa tyle, ile trwa.
    """

    template_name = EXPORT_TEMPLATE

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        edition = current_edition()
        context.update(
            {
                "edition": edition,
                "stages": list(Stage.objects.filter(edition=edition).order_by("opens_at", "id"))
                if edition
                else [],
                "formats": sorted(exports.FORMATS),
            }
        )
        return context


class ExportDownloadView(CoordinatorRequiredMixin, View):
    """``/coordinator/export/<kind>/<fmt>/`` – plik z danymi edycji albo etapu.

    ``kind`` i ``fmt`` przychodzą z adresu, więc obie wartości są sprawdzane wobec zamkniętych
    list, a nieznana daje 404. Eksporty etapowe wymagają parametru ``?stage=<id>``: bez niego nie
    wiadomo, czyje wyniki miałyby wyjść z systemu, a domyślanie się etapu byłoby najgorszym
    możliwym rozwiązaniem przy danych osobowych.

    Wpis audytowy (``export.generated``) powstaje **przed** oddaniem pliku i niesie wyłącznie
    rodzaj eksportu, format i liczbę wierszy. Obiektem wpisu jest edycja albo etap – to pozwala
    później zapytać „co wyszło z tego etapu” jednym filtrem przeglądarki audytu.
    """

    def get(self, request, kind: str, fmt: str):
        if fmt not in exports.FORMATS:
            raise Http404("Nieznany format eksportu.")
        dataset, target = self._dataset(request, kind)
        audit(
            request.user,
            "export.generated",
            target,
            {"kind": kind, "format": fmt, "rows": dataset.count},
            request=request,
        )
        return exports.build_response(dataset, fmt)

    def _dataset(self, request, kind: str):
        """Para (zbiór danych, obiekt do audytu) dla rodzaju eksportu z adresu."""
        if kind == "participants":
            edition = current_edition()
            if edition is None:
                raise Http404("Brak bieżącej edycji.")
            return exports.participant_dataset(edition), edition
        if kind in ("results", "reviews"):
            stage = _stage(self._stage_id(request))
            if kind == "results":
                return exports.stage_results_dataset(stage), stage
            return exports.stage_reviews_dataset(stage), stage
        raise Http404("Nieznany rodzaj eksportu.")

    @staticmethod
    def _stage_id(request) -> int:
        raw = (request.GET.get("stage") or "").strip()
        if not raw.isdigit():
            raise Http404("Eksport etapu wymaga parametru ?stage=<id>.")
        return int(raw)


# --- 4. Przeglądarka audytu ---------------------------------------------------------------------


class AuditBrowserView(CoordinatorRequiredMixin, TemplateView):
    """``/coordinator/audit/`` – ślad audytowy z filtrami i stronicowaniem.

    Ekran nie dokłada do wierszy ani jednej informacji spoza wpisu: wykonawca, akcja, obiekt,
    czas i ``diff``. ``diff`` z założenia nie zawiera danych osobowych (patrz
    ``apps.core.models``), więc rozwijanie go pod ``<details>`` jest bezpieczne – schowany jest
    dlatego, że jest techniczny i przy stu wierszach zabiłby czytelność listy, a nie dlatego, że
    jest wrażliwy.
    """

    template_name = AUDIT_TEMPLATE

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        params = self.request.GET
        queryset = audit_browser.entries(params)
        paginator = Paginator(queryset, audit_browser.AUDIT_PAGE_SIZE)
        page = paginator.get_page(params.get("page"))
        # Parametry filtra do odnośników stronicowania – bez nich „następna strona” gubiłaby
        # zawężenie i pokazywała drugą stronę zupełnie innej listy.
        filters = {key: value for key, value in params.items() if key != "page" and value}
        context.update(
            {
                "form": AuditFilterForm(
                    data=params or None,
                    actions=audit_browser.known_actions(),
                    target_types=audit_browser.known_target_types(),
                ),
                "page_obj": page,
                "paginator": paginator,
                "rows": page.object_list,
                "filter_query": urlencode(filters),
                "has_filters": bool(filters),
            }
        )
        return context


# --- 5. Symulacja kwalifikacji ------------------------------------------------------------------


class StageSimulationView(CoordinatorRequiredMixin, TemplateView):
    """``/coordinator/stages/<id>/simulation/`` – „kto by się zakwalifikował przy takim progu”.

    Parametry jadą w adresie (GET), więc wynik symulacji da się odświeżyć, zapisać w zakładkach
    i wkleić w wiadomości do reszty komitetu – a to jest dokładnie to, do czego ten ekran służy
    przy ustalaniu progu. Żądanie GET niczego nie zapisuje: przeliczenie idzie trybem podglądu
    (``compute_stage_results(preview=True)``), a reguła jest niezapisanym obiektem.

    Bez parametrów ekran pokazuje próg **aktualnie zapisany** przy etapie – najczęstsze pytanie
    brzmi „co daje to, co mamy teraz”, a nie „co daje coś, czego jeszcze nie wpisałem”.
    """

    template_name = SIMULATION_TEMPLATE

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        stage = _stage(self.kwargs["stage_id"])
        rule = getattr(stage, "qualification_rule", None)
        params = self.request.GET
        initial = {
            "mode": rule.mode if rule is not None else QualificationMode.MIN_POINTS,
            "min_points": rule.min_points if rule is not None else None,
            "top_n": rule.top_n if rule is not None else None,
        }
        form = SimulationForm(data=params) if "mode" in params else SimulationForm(initial=initial)
        result = error = None
        if form.is_bound and form.is_valid():
            try:
                result = simulate(
                    stage,
                    form.cleaned_data["mode"],
                    form.cleaned_data["min_points"],
                    form.cleaned_data["top_n"],
                )
            except DomainError as exc:
                error = str(exc.detail)
        context.update(
            {
                "stage": stage,
                "rule": rule,
                "form": form,
                "result": result,
                "error": error,
                # Parametry, które przycisk „Zastosuj” ma zapisać – dokładnie te, które właśnie
                # obejrzano. Bez nich zapis mógłby dotyczyć innej reguły niż pokazana tabela.
                "applied": form.cleaned_data if (form.is_bound and form.is_valid()) else None,
            }
        )
        return context


class ApplyQualificationRuleView(ActionViewMixin, CoordinatorRequiredMixin, View):
    """POST: zapisuje obejrzany próg przy etapie. Nie kwalifikuje i nikomu nic nie ogłasza.

    Rozdzielenie „obejrzyj” i „zapisz” jest tu całą ideą ekranu: symulacja wolno biega po
    parametrach, a stan zawodów zmienia dopiero świadome kliknięcie. Przeliczenie wyników
    i kwalifikacja zostają tam, gdzie były – na pulpicie, po zamknięciu okna reklamacji.
    """

    def get_success_url(self, stage_id: int, *args, **kwargs) -> str:
        return reverse("web:coordinator-stage-simulation", args=[stage_id])

    def perform(self, request, stage_id: int) -> str:
        stage = _stage(stage_id)
        form = SimulationForm(request.POST)
        if not form.is_valid():
            raise DomainError("Nieprawidłowe parametry progu kwalifikacji.", "QUALIFICATION_RULE_INVALID")
        rule = apply_rule(
            stage,
            form.cleaned_data["mode"],
            form.cleaned_data["min_points"],
            form.cleaned_data["top_n"],
            actor=request.user,
            request=request,
        )
        return f"Zapisano próg kwalifikacji etapu: {rule}."
