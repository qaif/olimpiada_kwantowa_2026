"""Ekrany logistyki etapu stacjonarnego: miejsca zawodów, przyjazdy i obecność.

Trzy adresy za jedną flagą ``onsite_logistics`` (``docs/UNIWERSALNY-ETAP-2.md`` § 1.5.2, § 2.2):

- ``/coordinator/venues/`` – **miejsca zawodów** (per konkurs, nie per etap: finał i warsztaty
  bywają w tym samym budynku) oraz **przełącznik zbierania potrzeb szczególnych** (decyzja D21),
- ``/coordinator/stages/<id>/logistics/`` – **przyjazdy i potrzeby**: kto kiedy przyjeżdża, czego
  potrzebuje, ile jest noclegów i posiłków do zamówienia, plus trzy listy do druku,
- ``/coordinator/stages/<id>/attendance/`` – **obecność**: odhaczenie wejścia na salę.

**Decyzja D21 i dlatego ten ekran jest koordynatorski.** „Dieta bezglutenowa” i „pokój na parterze”
bywają danymi o zdrowiu (art. 9 RODO). Zbieranie potrzeb szczególnych jest **domyślnie wyłączone**
i włącza je świadoma decyzja organizatora, zapisywana z wpisem audytowym
(``set_special_needs_collection``). Przełącznik stoi na ekranie miejsc zawodów razem
z ostrzeżeniem, którego brzmienie należy do obszaru (``SPECIAL_NEEDS_WARNING``), a nie do szablonu.
Uwagi uczestników nie widzi ani recenzent, ani opiekun: ``arrival_rows`` w ogóle nie kładzie klucza
``note`` w wierszu, gdy zbieranie jest wyłączone – szablon nie ma czego pokazać nawet przez pomyłkę.

**Listy do druku nie mają własnego generatora.** Trzy rodzaje (obecności, noclegowa, żywieniowa)
składa ``apps.integrations.exports.render_logistics_list`` tą samą ścieżką ReportLab, którą
powstaje protokół etapu. Uwaga tekstowa wychodzi **wyłącznie** na listę żywieniową i wyłącznie
w konkursie, który świadomie włączył zbieranie – lista obecności krąży po sali.

**Reguły są w serwisie, widok orkiestruje** (§ 2.1). Ten moduł nie zapisuje ani jednego pola
samodzielnie: miejsca zakłada ``create_venue``, zmienia ``update_venue``, obecność notuje
``record_attendance``, a przełącznik przestawia ``set_special_needs_collection``. Wpisy audytowe
pisze ``apps.competitions.logistics``.

**Formularze są tutaj, a nie w ``apps.web.coordinator_forms``** – ta sama decyzja i to samo
uzasadnienie, co w ``apps.web.views.coordinator_integrations``: opisują wyłącznie te ekrany i nie
mają odpowiednika w API.
"""

from __future__ import annotations

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.competitions.logistics import (
    FEATURE,
    SPECIAL_NEEDS_WARNING,
    Venue,
    arrival_rows,
    attendance_rows,
    collects_special_needs,
    create_venue,
    needs_summary,
    record_attendance,
    set_special_needs_collection,
    update_venue,
    venues_for,
)
from apps.competitions.models import Stage, StageEntry
from apps.competitions.scoping import require_competition
from apps.core.api import DomainError
from apps.integrations.exports import (
    LIST_ACCOMMODATION,
    LIST_ATTENDANCE,
    LIST_MEAL,
    logistics_list_filename,
    render_logistics_list,
)
from apps.web.mixins import CoordinatorRequiredMixin

VENUES_TEMPLATE = "web/coordinator/venues.html"
LOGISTICS_TEMPLATE = "web/coordinator/logistics.html"
ATTENDANCE_TEMPLATE = "web/coordinator/attendance.html"

#: Rodzaje list do druku wraz z podpisem przycisku. Kolejność jest treścią ekranu: obecność jest
#: dokumentem, który komisja niesie na salę, a dwie pozostałe są wydrukami roboczymi.
LIST_KINDS: tuple[tuple[str, str], ...] = (
    (LIST_ATTENDANCE, "Lista obecności"),
    (LIST_ACCOMMODATION, "Lista noclegowa"),
    (LIST_MEAL, "Lista żywieniowa"),
)


def form_errors(form) -> str:
    """Błędy formularza jako jedno zdanie do ``messages`` – bez nazw pól technicznych."""
    return "; ".join(text for errors in form.errors.values() for text in errors)


# --- formularze -------------------------------------------------------------------------------


class VenueForm(forms.Form):
    """Miejsce zawodów: adres i pojemność. Reguły (jedyność nazwy w konkursie) zostają w serwisie.

    ``capacity`` jest opcjonalna, bo puste znaczy „nie wiadomo”, a nie „zero”: organizator bywa
    pewien sali, zanim pozna jej limit, a system nie ma prawa wtedy odmówić zapisania adresu.
    """

    #: Komplet argumentów ``create_venue`` i ``update_venue`` poza nazwą – jedna krotka, żeby
    #: ekran i serwis nie mogły się rozjechać.
    FIELDS: tuple[str, ...] = ("address", "city", "capacity", "note")

    name = forms.CharField(label="Nazwa", max_length=160)
    address = forms.CharField(label="Adres", max_length=255, required=False)
    city = forms.CharField(label="Miejscowość", max_length=120, required=False)
    capacity = forms.IntegerField(label="Pojemność", required=False, min_value=0)
    note = forms.CharField(
        label="Uwagi",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Wejście, parking, godziny otwarcia – informacje organizacyjne, nie dane osób.",
    )

    def clean_capacity(self):
        """Puste pole zostaje ``None``: „nie wiadomo” i „zero miejsc” to dwie różne odpowiedzi."""
        return self.cleaned_data.get("capacity")


class SpecialNeedsForm(forms.Form):
    """Przełącznik zbierania potrzeb szczególnych – jedno pole i decyzja z art. 9 RODO za nim."""

    collect = forms.BooleanField(label="Zbieraj potrzeby szczególne", required=False)


class AttendanceForm(forms.Form):
    """Jedno odhaczenie: który wpis i czy obecny. Godzinę stawia serwis, nie formularz."""

    entry = forms.IntegerField(min_value=1)
    present = forms.BooleanField(required=False)


# --- wspólna bramka ---------------------------------------------------------------------------


class LogisticsScreenMixin(CoordinatorRequiredMixin):
    """Rola koordynatora, konkurs żądania i flaga ``onsite_logistics`` – jedno wejście na wszystkie."""

    def competition_or_404(self, request):
        """Konkurs żądania, o ile prowadzi logistykę. Inaczej 404 (§ 2.1).

        Konkurs bierzemy z kontekstu żądania (``CompetitionMiddleware``), a nie z identyfikatora
        w adresie. Sprawdzenie stoi w metodzie widoku, a nie w ``dispatch``, bo bramkę roli stawia
        wcześniej ``RoleRequiredMixin.dispatch``: anonim i uczestnik mają dostać 302 albo 403,
        **zanim** odpowiedź zdradzi stan przełącznika tego konkursu.
        """
        competition = require_competition(getattr(request, "competition", None))
        if not competition.has_feature(FEATURE):
            raise Http404("Ten konkurs nie prowadzi logistyki etapu stacjonarnego – ekran jest wyłączony.")
        return competition

    def stage_or_404(self, competition, stage_id: int) -> Stage:
        """Etap **tego konkursu** albo 404 – zakres z querysetu, nie z widoku (§ 3.6)."""
        return get_object_or_404(
            Stage.objects.for_competition(competition).select_related("edition"), pk=stage_id
        )

    def venue_or_404(self, competition, pk: int) -> Venue:
        """Miejsce **tego konkursu** albo 404."""
        return get_object_or_404(venues_for(competition), pk=pk)


# --- miejsca zawodów ---------------------------------------------------------------------------


class VenueListView(LogisticsScreenMixin, View):
    """``GET|POST /coordinator/venues/`` – miejsca zawodów i przełącznik danych szczególnych."""

    def get(self, request):
        competition = self.competition_or_404(request)
        return TemplateResponse(request, VENUES_TEMPLATE, self._context(competition))

    def post(self, request):
        competition = self.competition_or_404(request)
        form = VenueForm(request.POST)
        if not form.is_valid():
            return TemplateResponse(
                request, VENUES_TEMPLATE, self._context(competition, form=form), status=400
            )
        data = form.cleaned_data
        try:
            create_venue(
                competition,
                name=data["name"],
                actor=request.user,
                request=request,
                **{field: data[field] for field in VenueForm.FIELDS},
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return TemplateResponse(
                request, VENUES_TEMPLATE, self._context(competition, form=form), status=exc.status_code
            )
        except ValidationError as exc:
            # Jedyna droga tutaj: druga sala o tej samej nazwie w tym samym konkursie. Więz jest
            # w bazie, a komunikat serwisu jest jedyną informacją, co poprawić.
            messages.error(request, "; ".join(exc.messages))
            return TemplateResponse(
                request, VENUES_TEMPLATE, self._context(competition, form=form), status=400
            )
        messages.success(request, "Miejsce zawodów zapisane.")
        return redirect(reverse("web:coordinator-venues"))

    def _context(self, competition, *, form=None) -> dict:
        """Jeden kontekst ekranu – ten sam dla ``GET`` i dla nieudanego ``POST``."""
        venues = list(venues_for(competition).order_by("name", "id"))
        return {
            "venues": [
                {
                    "venue": venue,
                    "form": VenueForm(
                        initial={
                            "name": venue.name,
                            **{field: getattr(venue, field) for field in VenueForm.FIELDS},
                        }
                    ),
                }
                for venue in venues
            ],
            "form": form or VenueForm(),
            "special_needs": collects_special_needs(competition),
            "special_needs_form": SpecialNeedsForm(initial={"collect": collects_special_needs(competition)}),
            # Ostrzeżenie należy do obszaru, nie do szablonu: ma się zmieniać razem z regułą,
            # którą opisuje (``apps.competitions.logistics.SPECIAL_NEEDS_WARNING``).
            "special_needs_warning": SPECIAL_NEEDS_WARNING,
        }


class VenueEditView(LogisticsScreenMixin, View):
    """``POST /coordinator/venues/<pk>/`` – zmiana adresu, miasta, pojemności albo uwag."""

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        venue = self.venue_or_404(competition, pk)
        form = VenueForm(request.POST)
        if not form.is_valid():
            messages.error(request, f"Miejsca nie zapisano: {form_errors(form)}")
            return redirect(reverse("web:coordinator-venues"))
        data = form.cleaned_data
        try:
            update_venue(
                venue,
                actor=request.user,
                request=request,
                name=data["name"],
                **{field: data[field] for field in VenueForm.FIELDS},
            )
        except (DomainError, ValidationError) as exc:
            messages.error(
                request, str(exc.detail) if isinstance(exc, DomainError) else "; ".join(exc.messages)
            )
            return redirect(reverse("web:coordinator-venues"))
        messages.success(request, "Miejsce zawodów zmienione.")
        return redirect(reverse("web:coordinator-venues"))


class SpecialNeedsView(LogisticsScreenMixin, View):
    """``POST /coordinator/venues/special-needs/`` – decyzja D21: zbieramy dane szczególne czy nie.

    Wyłączenie **nie kasuje** zebranych już uwag: czyszczenie danych jest osobną czynnością
    (retencja), a ciche usuwanie przy przestawieniu przełącznika byłoby zmianą, której nikt się
    nie spodziewa. Od chwili wyłączenia uwagi nie ma w formularzu uczestnika, w wierszach ekranu
    przyjazdów ani na liście żywieniowej – pilnuje tego serwis, a nie szablon.
    """

    def post(self, request):
        competition = self.competition_or_404(request)
        form = SpecialNeedsForm(request.POST)
        if not form.is_valid():  # pragma: no cover - pole logiczne nie ma jak nie przejść
            messages.error(request, form_errors(form))
            return redirect(reverse("web:coordinator-venues"))
        enabled = form.cleaned_data["collect"]
        set_special_needs_collection(competition, enabled=enabled, actor=request.user, request=request)
        messages.success(
            request,
            "Zbieranie potrzeb szczególnych włączone. Formularz przyjazdu pyta od tej chwili o dietę "
            "i dostępność."
            if enabled
            else "Zbieranie potrzeb szczególnych wyłączone. Zebrane wcześniej uwagi zostają w bazie "
            "i podlegają retencji.",
        )
        return redirect(reverse("web:coordinator-venues"))


# --- przyjazdy i potrzeby ------------------------------------------------------------------------


class StageLogisticsView(LogisticsScreenMixin, View):
    """``GET /coordinator/stages/<id>/logistics/`` – deklaracje przyjazdu i liczby do zamówienia.

    Wiersze i podsumowanie składa serwis (``arrival_rows``, ``needs_summary``), a nie widok:
    reguła „co pokazujemy o deklaracji” należy do obszaru, a ekran – do zadania montażowego.
    """

    def get(self, request, stage_id: int):
        competition = self.competition_or_404(request)
        stage = self.stage_or_404(competition, stage_id)
        summary = needs_summary(stage)
        rows = arrival_rows(stage)
        return TemplateResponse(
            request,
            LOGISTICS_TEMPLATE,
            {
                "stage": stage,
                "rows": rows,
                "summary": summary,
                # Podsumowanie jako pary (etykieta, liczba) w kolejności słownika potrzeb – szablon
                # nie ma po czym rozwinąć kluczy ``TextChoices``, a kolejność jest treścią.
                "summary_rows": _summary_rows(summary),
                "special_needs": collects_special_needs(competition),
                "list_kinds": LIST_KINDS,
                "attendance_url": reverse("web:coordinator-stage-attendance", args=[stage.pk]),
            },
        )


def _summary_rows(summary: dict[str, int]) -> list[tuple[str, int]]:
    """Podsumowanie potrzeb jako pary (etykieta, liczba) – w kolejności listy rodzajów."""
    from apps.competitions.logistics import LogisticsNeed

    labels = dict(LogisticsNeed.choices)
    return [(labels[value], summary.get(value, 0)) for value in LogisticsNeed.values]


class LogisticsListView(LogisticsScreenMixin, View):
    """``GET /coordinator/stages/<id>/logistics/<rodzaj>/`` – lista do druku jako PDF.

    Rodzaj jedzie w adresie **swoją wartością** (``attendance``, ``accommodation``, ``meal``), bo
    nie jest wierszem w bazie, tylko wyborem na ekranie i fragmentem nazwy pliku. Wartość spoza
    listy daje 404 – to jest literówka w adresie, a nie „jeszcze nieskonfigurowany” rodzaj.
    """

    def get(self, request, stage_id: int, kind: str):
        competition = self.competition_or_404(request)
        stage = self.stage_or_404(competition, stage_id)
        if kind not in dict(LIST_KINDS):
            raise Http404(f"Nie ma listy rodzaju {kind!r}.")
        response = HttpResponse(render_logistics_list(stage, kind), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{logistics_list_filename(stage, kind)}"'
        return response


# --- obecność ------------------------------------------------------------------------------------


class StageAttendanceView(LogisticsScreenMixin, View):
    """``GET|POST /coordinator/stages/<id>/attendance/`` – kto się stawił na sali.

    Lista obejmuje **wszystkich** zapisanych do etapu, a nie tylko odhaczonych
    (``attendance_rows``): to jest dokument, który komisja niesie na salę, więc ma na nim być
    każdy, kto ma prawo wejść.

    Każde odhaczenie jest osobnym POST-em na jeden wiersz, a nie zbiorczym zapisem całej tabeli.
    Powód jest praktyczny: obecność notuje się po jednej osobie przy wejściu, a zbiorczy zapis
    kazałby po każdym wejściu przesłać stan wszystkich – i przy dwóch osobach odhaczających
    z dwóch urządzeń kasowałby cudze odhaczenia.
    """

    def get(self, request, stage_id: int):
        competition = self.competition_or_404(request)
        stage = self.stage_or_404(competition, stage_id)
        return TemplateResponse(request, ATTENDANCE_TEMPLATE, self._context(stage))

    def post(self, request, stage_id: int):
        competition = self.competition_or_404(request)
        stage = self.stage_or_404(competition, stage_id)
        form = AttendanceForm(request.POST)
        if not form.is_valid():
            messages.error(request, f"Obecności nie zapisano: {form_errors(form)}")
            return TemplateResponse(request, ATTENDANCE_TEMPLATE, self._context(stage), status=400)
        entry = get_object_or_404(
            StageEntry.objects.for_competition(competition).select_related("participant"),
            pk=form.cleaned_data["entry"],
            stage=stage,
        )
        try:
            record_attendance(
                entry, present=form.cleaned_data["present"], actor=request.user, request=request
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return TemplateResponse(
                request, ATTENDANCE_TEMPLATE, self._context(stage), status=exc.status_code
            )
        messages.success(
            request,
            f"Odnotowano obecność: {entry.participant.public_code}."
            if form.cleaned_data["present"]
            else f"Odnotowano nieobecność: {entry.participant.public_code}.",
        )
        return redirect(reverse("web:coordinator-stage-attendance", args=[stage.pk]))

    def _context(self, stage) -> dict:
        rows = attendance_rows(stage)
        return {
            "stage": stage,
            "rows": rows,
            "present_count": sum(1 for row in rows if row["present"]),
            "list_url": reverse("web:coordinator-stage-logistics-list", args=[stage.pk, LIST_ATTENDANCE]),
            "logistics_url": reverse("web:coordinator-stage-logistics", args=[stage.pk]),
        }
