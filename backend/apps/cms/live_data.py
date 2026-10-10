"""Dane zawodów pokazywane w części informacyjnej – jedno źródło dla **dwóch** wersji serwisu.

Ten moduł czytają dwie wersje tej samej strony: strony Wagtaila (``apps.cms.models`` – ``get_context``
strony głównej, zadań, wyników i edycji archiwalnej) oraz wersja porównawcza na django CMS
(``dj.<SITE_DOMAIN>``, ``docs/tasks/DJ-01.md``), która dostaje te same dane przez wewnętrzne API
(``apps.cms.djcms_api``). Logika stoi tutaj, a nie w każdym z odbiorców osobno, bo dwie kopie reguły
„co wolno pokazać” rozjechałyby się przy pierwszej poprawce – a rozjazd byłby tu terminem albo
tytułem zadania widocznym pod jednym adresem, a schowanym pod drugim.

Trzy reguły, których pilnują funkcje niżej (te same, co w docstringu ``apps/cms/models.py``):

- **CMS nie jest źródłem prawdy o zawodach.** Terminy, stany etapów, zadania i wyniki czytamy
  z ``apps.competitions`` i ``apps.results``; żadna z wersji serwisu nie ma pola, w którym redaktor
  przepisałby datę albo liczbę punktów. Strona nie może pokazać innego deadline'u niż ten, który
  egzekwuje serwer.
- **Treść zadań jest jawna dopiero po ``Stage.opens_at``.** ``problems_state`` oddaje pustą listę
  zadań, dopóki etap się nie otworzy – odbiorca nie ma z czego zrenderować ani tytułu, ani linku do
  PDF-u. Tę samą regułę sprawdza jeszcze raz widok pliku (``ProblemStatementView``).
- **Tabela wyników pochodzi wyłącznie ze snapshotu.** ``results_state`` czyta
  ``ResultsPublication.snapshot`` i nie dotyka ``FinalGrade`` ani danych uczestników
  (PROJEKT.md 2.4).

Funkcje przyjmują **konkurs**, a nie żądanie: strona Wagtaila bierze go z żądania albo z drzewa
stron (``competition_for_page``), API dla djcms – ze ścieżki (``/internal/djcms/v2/c/<slug>/…``,
``apps.cms.djcms_api.views.endpoint_v2``). Kolejność i kształt zapytań są
przeniesione bez zmian z dawnych ``get_context`` – budżety zapytań stron pilnują tego testami.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from django.utils import timezone

from apps.competitions.models import Edition, Stage
from apps.competitions.scoping import scope_to_competition
from apps.competitions.scoring import problem_maxima_by_number
from apps.competitions.services import current_edition, current_stage, training_stage
from apps.results.models import ResultsPublication

from .timeline import stage_rows

if TYPE_CHECKING:  # pragma: no cover - wyłącznie dla podpowiedzi typów
    from apps.competitions.models import Problem


@dataclass(frozen=True)
class CompetitionState:
    """Bieżąca edycja, etap „na teraz” i wiersze osi czasu – materiał strony głównej."""

    edition: Edition | None
    current_stage: Stage | None
    stage_rows: list[dict] = field(default_factory=list)


def competition_state(competition, now=None) -> CompetitionState:
    """Stan zawodów konkursu „na teraz” (wyjęte z ``HomePage.get_context``).

    ``stage_rows`` dostaje konkurs wprost: przy edycji ``None`` funkcja szuka jej jeszcze raz
    i ma szukać w **tym** konkursie, a nie w konkursie kontekstu – strona podglądana z panelu
    innego konkursu nie może pożyczyć cudzego harmonogramu (``apps.cms.tenancy``).
    """
    now = now or timezone.now()
    edition = current_edition(competition)
    return CompetitionState(
        edition=edition,
        current_stage=current_stage(edition, now) if edition else None,
        stage_rows=stage_rows(edition, now, competition=competition),
    )


@dataclass(frozen=True)
class ProblemsState:
    """Etap bieżący i treningowy wraz z zadaniami – ale zadania wyłącznie po otwarciu etapu."""

    edition: Edition | None
    stage: Stage | None
    stage_has_opened: bool
    problems: list[Problem] = field(default_factory=list)
    training_stage: Stage | None = None
    training_problems: list[Problem] = field(default_factory=list)


def problems_state(competition, now=None) -> ProblemsState:
    """Zadania etapu bieżącego i etapu treningowego (wyjęte z ``ProblemsPage.get_context``).

    Jedyne miejsce decydujące o jawności treści zadań w części informacyjnej. ``problems`` zostaje
    puste, dopóki etap się nie otworzy – odbiorca (szablon Wagtaila albo API dla ``dj.``) nie ma
    z czego zrenderować ani tytułu, ani linku do PDF.

    Trening podlega **tej samej** regule jawności (``has_opened``): w praktyce jest otwarty od
    chwili posiania, ale reguła ma być jedna, a nie „jedna dla zawodów, druga dla treningu” – link
    do PDF-a przed otwarciem etapu i tak dałby 404 w ``ProblemStatementView``.

    Komunikat „przed otwarciem” (``notice``) nie należy do tej funkcji: to treść redakcyjna strony
    (``ProblemsPage.closed_notice``), a nie dana zawodów.
    """
    now = now or timezone.now()
    edition = current_edition(competition)
    stage = current_stage(edition, now) if edition else None
    has_opened = bool(stage and stage.has_opened(now))
    training = training_stage(edition)
    training_has_opened = bool(training and training.has_opened(now))
    return ProblemsState(
        edition=edition,
        stage=stage,
        stage_has_opened=has_opened,
        problems=list(stage.problems.order_by("number")) if stage is not None and has_opened else [],
        training_stage=training,
        training_problems=(
            list(training.problems.order_by("number")) if training is not None and training_has_opened else []
        ),
    )


@dataclass(frozen=True)
class ResultsState:
    """Pełne tabele bieżącej edycji i odnośniki do ogłoszeń wcześniejszych edycji."""

    edition: Edition | None
    tables: list[dict] = field(default_factory=list)
    archive: list[dict] = field(default_factory=list)


def results_state(competition) -> ResultsState:
    """Ogłoszone tabele wyników konkursu (wyjęte z ``ResultsPage.get_context``).

    Dwie decyzje o kształcie tego odczytu (opisane szerzej przy ``ResultsPage``):

    - **jedno zapytanie na całość.** Wchodzimy od strony publikacji i dociągamy etap z edycją przez
      ``select_related`` – wariant „etapy, a potem publikacja per etap” rósł liniowo z liczbą
      ogłoszonych etapów,
    - **pełne tabele tylko dla bieżącej edycji.** Archiwalne edycje zostają odnośnikiem: snapshot
      finału to tysiące wierszy.

    ``live()`` zostaje: znacznik ``results_published_at`` na etapie jest tym, co koordynator zdejmuje,
    żeby wycofać ogłoszenie, a sam rekord publikacji ma zostać jako ślad. Zawężenie do konkursu jest
    **drugim** filtrem i musi być – bez niego tabela wyników jednej olimpiady wyliczałaby etapy
    drugiej. Drogę do konkursu zna manager modelu, a regułę odwrotów – ``scope_to_competition``.
    """
    edition = current_edition(competition)
    publications = (
        scope_to_competition(ResultsPublication.objects.live(), competition)
        .select_related("stage", "stage__edition", "stage__scoring_scale")
        # Zadania etapów jednym zapytaniem na całą stronę – czyta je ``problem_maxima_by_number``
        # (nagłówki „Zad. 3 (max 12,5)”, wydanie 0.35.0). Bez tego każda tabela dokładałaby dwa
        # zapytania, a liczba zapytań rosłaby z liczbą ogłoszonych etapów.
        .prefetch_related("stage__problems")
        .order_by("-stage__results_published_at", "-stage_id")
    )
    tables: list[dict] = []
    archive: list[dict] = []
    for publication in publications:
        stage = publication.stage
        if edition is not None and stage.edition_id == edition.pk:
            rows = publication.rows
            tables.append(
                {
                    "stage": stage,
                    "publication": publication,
                    "rows": rows,
                    "problem_numbers": sorted(
                        {key for row in rows for key in (row.get("points") or {})},
                        key=lambda value: (len(value), value),
                    ),
                    # Maksima zadań do nagłówków kolumn („Zad. 3 (max 12,5)”, wydanie 0.35.0) –
                    # opis skali, nie dane uczestnika, więc wolno je czytać obok snapshotu.
                    "problem_maxima": problem_maxima_by_number(stage),
                }
            )
        else:
            archive.append({"stage": stage, "publication": publication})
    return ResultsState(edition=edition, tables=tables, archive=archive)


def archive_result_links(edition_id: int | None, competition=None) -> list[dict]:
    """Etapy edycji, dla których istnieje **ogłoszona** tabela wyników (``ArchiveEditionPage``).

    Bez publikacji nie ma linku: publiczny widok ``/results/<id>/`` i tak odpowiada 404, a martwy
    odnośnik sugerowałby, że wyniki są, tylko schowane.

    ``competition`` jest dla wołającego, który identyfikator edycji dostał **z zewnątrz** – API
    wersji ``dj.`` czyta go z metadanych strony, którą redaktor wpisał ręcznie. Edycja cudzego
    konkursu daje wtedy pustą listę, a nie jego tabele. Strona Wagtaila argumentu nie podaje:
    jej edycję wybiera redaktor z listy w ``/cms/``, a zachowanie tej strony ma zostać co do
    zapytania takie, jak przed wydzieleniem tej funkcji.

    ``select_related("edition")`` nie dokłada zapytania (to złączenie), a oszczędza je każdemu
    odbiorcy, który przy etapie pokazuje rocznik edycji.
    """
    if edition_id is None:
        return []
    if (
        competition is not None
        and not Edition.objects.filter(pk=edition_id, competition=competition).exists()
    ):
        return []
    stages = list(
        Stage.objects.filter(edition_id=edition_id).select_related("edition").order_by("opens_at", "id")
    )
    published = set(
        ResultsPublication.objects.live().filter(stage__in=stages).values_list("stage_id", flat=True)
    )
    return [{"stage": stage} for stage in stages if stage.pk in published]
