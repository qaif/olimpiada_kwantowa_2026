"""Trzy dodatkowe ekrany panelu uczestnika: informacja zwrotna, kalendarz i archiwum.

Osobny moduł od ``participant.py``, bo wszystkie trzy są **tylko do odczytu** i nie mają ani
jednej akcji zmieniającej stan: żadnego POST-a, żadnego serwisu domenowego, żadnej bramy poza
rolą. Trzymanie ich obok uploadu i rejestracji do etapu mieszałoby dwie różne odpowiedzialności
w pliku, który i tak jest najczęściej czytany w tej aplikacji.

Reguły widoczności nie powstają tutaj. „Co wolno pokazać po publikacji” liczy
``apps.results.feedback``, „co jest w kalendarzu edycji” – ``apps.cms.calendar``, a treści zadań
archiwalnych serwuje ``competitions.ProblemStatementView`` z własną bramą ``stage.has_opened()``.
Widok składa wynik i wybiera szablon.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView, View

from apps.cms.calendar import calendar_ics, ics_filename, participant_calendar
from apps.cms.workshops import WORKSHOPS_SLUG, workshop_rows, workshops_page
from apps.competitions.models import Edition, Problem, Stage, StageEntry, StageKind
from apps.competitions.scoping import resolve_competition, scope_to_competition
from apps.competitions.services import current_edition, training_stage
from apps.results.feedback import participant_feedback
from apps.web.mixins import ParticipantRequiredMixin


class ParticipantFeedbackView(ParticipantRequiredMixin, TemplateView):
    """Komentarze recenzentów i miejsce uczestnika w jednym etapie – po ogłoszeniu wyników.

    Przed publikacją odpowiedź to **404**, a nie 403 i nie pusta strona: uczestnik nie ma się
    dowiedzieć, że jego wynik jest już policzony i czeka na ogłoszenie. To samo 404 dostaje ktoś,
    kto w tym etapie nie brał udziału – rozróżnienie tych dwóch przypadków byłoby informacją
    o cudzych wpisach.
    """

    template_name = "web/participant/feedback.html"

    def get_context_data(self, stage_id: int, **kwargs):
        context = super().get_context_data(**kwargs)
        stage = get_object_or_404(
            Stage.objects.for_competition(self.competition).select_related("edition", "qualification_rule"),
            pk=stage_id,
        )
        feedback = participant_feedback(self.participant, stage)
        if feedback is None:
            raise Http404("Wyniki tego etapu nie zostały jeszcze ogłoszone.")
        context["feedback"] = feedback
        context["stage"] = stage
        return context


class ParticipantCalendarView(ParticipantRequiredMixin, TemplateView):
    """Kalendarz osobisty: terminy bieżącej edycji plus własny termin rozmowy."""

    template_name = "web/participant/calendar.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        edition = current_edition(self.competition)
        context.update(
            {
                "edition": edition,
                "items": participant_calendar(
                    self.participant, edition=edition, competition=self.competition
                ),
            }
        )
        return context


class ParticipantCalendarIcsView(ParticipantRequiredMixin, View):
    """Ten sam kalendarz jako plik ``.ics`` do zaimportowania albo zasubskrybowania.

    ``Content-Disposition: attachment`` jest świadome: bez niego przeglądarka wyświetliłaby plik
    jako tekst, a kalendarz jest po to, żeby trafił do kalendarza. Nazwa pliku zależy wyłącznie
    od konkursu i nie niesie danych osobowych – plik bywa przesyłany dalej.
    """

    def get(self, request):
        # Konkurs wprost, tak samo jak na ekranie kalendarza: plik ``.ics`` bywa **subskrybowany**,
        # więc odświeża się latami – i ma wtedy wyliczać terminy tej olimpiady, w której uczestnik
        # startuje, a nie tej, spod której domeny kiedyś kliknął. Ten sam konkurs rozstrzyga
        # napisy nagłówka pliku i jego nazwę (``apps.cms.calendar``, § 1.1.4).
        items = participant_calendar(self.participant, competition=self.competition)
        body = calendar_ics(items, competition=self.competition)
        response = HttpResponse(body, content_type="text/calendar; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{ics_filename(self.competition)}"'
        return response


# --- archiwum -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ArchiveProblem:
    """Jedno zadanie archiwalne: numer, tytuł i informacja, czy treść w ogóle jest do pobrania."""

    problem: Problem
    has_statement: bool


@dataclass(frozen=True)
class ArchiveStage:
    """Etap w archiwum wraz ze swoimi zadaniami."""

    stage: Stage
    problems: list[ArchiveProblem]


@dataclass(frozen=True)
class ArchiveEdition:
    """Zakończona edycja: podpis i etapy, których treści zadań są już jawne."""

    edition: Edition
    stages: list[ArchiveStage]


def _archive_problems(stage: Stage) -> list[ArchiveProblem]:
    return [
        ArchiveProblem(problem=problem, has_statement=bool(problem.statement_pdf))
        for problem in stage.problems.order_by("number", "id")
    ]


def archived_editions(competition=None) -> list[ArchiveEdition]:
    """Edycje inne niż bieżąca wraz z etapami, które się już otwarły.

    Filtr ``has_opened`` powtarza bramę, którą i tak egzekwuje ``ProblemStatementView``: treść
    zadania jest jawna dopiero po ``opens_at``. Widok nie może jej rozluźnić – powtarza ją tylko
    po to, żeby nie pokazywać odnośnika, który odpowie 404.

    Etap treningowy jest z tej listy wyjęty: ma własną sekcję, bo w nim wolno jeszcze oddać
    rozwiązanie, a archiwum jest z definicji do czytania.
    """
    editions = (
        scope_to_competition(Edition.objects.filter(is_current=False), competition)
        .prefetch_related("stages__problems")
        .order_by("-created_at", "-id")
    )
    rows: list[ArchiveEdition] = []
    for edition in editions:
        stages = [
            ArchiveStage(stage=stage, problems=_archive_problems(stage))
            for stage in sorted(edition.stages.all(), key=lambda item: (item.opens_at, item.pk))
            if stage.kind != StageKind.TRAINING and stage.has_opened()
        ]
        stages = [row for row in stages if row.problems]
        if stages:
            rows.append(ArchiveEdition(edition=edition, stages=stages))
    return rows


def workshop_materials(competition=None) -> list[dict]:
    """Warsztaty z harmonogramu redakcyjnego – temat i termin, z odnośnikiem do ``/warsztaty/``.

    Harmonogram warsztatów jest **treścią redakcyjną** (blok ``schedule`` na stronie „Warsztaty”)
    i nie ma w nim pola na załącznik: materiały redakcja publikuje w akapitach tej samej strony.
    Archiwum nie próbuje więc wyliczyć plików, których model nie zna – wypisuje terminy i odsyła
    tam, gdzie materiały faktycznie są. Gdyby kiedyś warsztat dostał własny załącznik w modelu,
    zmienia się ta jedna funkcja.
    """
    rows = workshop_rows(workshops_page(resolve_competition(competition)))
    url = f"/{WORKSHOPS_SLUG}/"
    return [{"topic": row["topic"], "date_value": row["date_value"], "url": url} for row in rows]


class ParticipantArchiveView(ParticipantRequiredMixin, TemplateView):
    """Materiały i zadania archiwalne: minione edycje, arkusz treningowy i warsztaty.

    Ekran jest wyłącznie do czytania. Jedyne wyjście „do działania” prowadzi na kartę treningową
    pulpitu – bo to tam mieszka upload, którego archiwum nie duplikuje.
    """

    template_name = "web/participant/archive.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        edition = current_edition(self.competition)
        training = training_stage(edition)
        context.update(
            {
                "editions": archived_editions(self.competition),
                "training_stage": training,
                "training_problems": _archive_problems(training) if training is not None else [],
                # Zgłoszenie do treningu jest warunkiem uploadu – archiwum mówi o tym wprost,
                # zamiast prowadzić na kartę, na której uczestnik zobaczy sam przycisk zapisu.
                "training_entry": (
                    StageEntry.objects.filter(participant=self.participant, stage=training).first()
                    if training is not None
                    else None
                ),
                "workshops": workshop_materials(self.competition),
            }
        )
        return context
