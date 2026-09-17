"""Wydarzenia edycji w panelu koordynatora – kalendarz, którego serwer nie egzekwuje.

Osobny moduł od ``coordinator_stages.py``, mimo identycznego kształtu ekranów (lista → formularz →
usunięcie). Rozdział idzie po przedmiocie, a nie po wyglądzie: tam zmienia się oś czasu, od której
zależy, czy uczestnik odda pracę na czas, i każdy zapis potyka się o stan zawodów (zgłoszenia,
zamknięty etap, wyznaczone rozmowy). Tutaj dopisuje się galę i dzień otwarty – nic w systemie się
od tego nie otwiera ani nie zamyka, więc jedyną regułą jest kolejność dwóch dat.

Zasady zostają te same: uprawnienia z ``CoordinatorRequiredMixin``, reguła domenowa w serwisie
(``apps.competitions.events``), widok wyłącznie orkiestruje, odmowa serwisu wraca **z kodem błędu
domenowego**, a nie jako 302 z komunikatem.
"""

from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.competitions.events import (
    EVENT_EDITABLE_FIELDS,
    create_event,
    delete_event,
    events_for_edition,
    update_event,
)
from apps.competitions.models import EditionEvent
from apps.competitions.services import current_edition
from apps.core.api import DomainError
from apps.web.forms import EditionEventForm
from apps.web.mixins import CoordinatorRequiredMixin

LIST_TEMPLATE = "web/coordinator/events.html"
FORM_TEMPLATE = "web/coordinator/event_form.html"


def _event(competition, pk: int) -> EditionEvent:
    """Wydarzenie **tego konkursu** albo 404 – zakres wychodzi z querysetu, nie z widoku (§ 3.6)."""
    return get_object_or_404(
        EditionEvent.objects.for_competition(competition).select_related("edition"), pk=pk
    )


def render_event_list(request, edition, *, status: int = 200):
    """Lista wydarzeń bieżącej edycji. Wspólna dla wejścia na ekran i dla nieudanego usunięcia."""
    context = {"edition": edition, "events": events_for_edition(edition)}
    return TemplateResponse(request, LIST_TEMPLATE, context, status=status)


class EventListView(CoordinatorRequiredMixin, View):
    """``GET /coordinator/events/`` – wydarzenia bieżącej edycji, także te schowane.

    Lista pokazuje **komplet**, łącznie z wierszami wyłączonymi z linii czasu: koordynator ma
    widzieć również to, co dopiero przygotowuje. Gdyby schowane wydarzenie znikało z listy, nie
    dałoby się go już odsłonić inaczej niż przez ``/admin/``.
    """

    def get(self, request):
        return render_event_list(request, current_edition(request.competition))


class EventCreateView(CoordinatorRequiredMixin, View):
    """``/coordinator/events/new/`` – dopisanie wydarzenia do bieżącej edycji."""

    def dispatch(self, request, *args, **kwargs):
        self.edition = current_edition(request.competition)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        if self.edition is None:
            return self._no_edition(request)
        return self._render(request, EditionEventForm())

    def post(self, request):
        if self.edition is None:
            return self._no_edition(request)
        form = EditionEventForm(request.POST)
        if not form.is_valid():
            return self._render(request, form, status=400)
        data = {name: form.cleaned_data[name] for name in EVENT_EDITABLE_FIELDS}
        try:
            event = create_event(edition=self.edition, actor=request.user, request=request, **data)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return self._render(request, form, status=exc.status_code)
        messages.success(request, f"Wydarzenie „{event.title}” zostało dodane do linii czasu.")
        return redirect(reverse("web:coordinator-events"))

    def _no_edition(self, request):
        messages.error(request, "Nie ustawiono bieżącej edycji – wydarzenie nie ma do czego należeć.")
        return redirect(reverse("web:coordinator"))

    def _render(self, request, form: EditionEventForm, *, status: int = 200):
        context = {"event": None, "edition": self.edition, "form": form}
        return TemplateResponse(request, FORM_TEMPLATE, context, status=status)


class EventEditView(CoordinatorRequiredMixin, View):
    """``/coordinator/events/<id>/edit/`` – zmiana wydarzenia."""

    def get(self, request, pk: int):
        event = _event(request.competition, pk)
        return self._render(request, event, EditionEventForm(instance=event))

    def post(self, request, pk: int):
        event = _event(request.competition, pk)
        form = EditionEventForm(request.POST, instance=event)
        if not form.is_valid():
            return self._render(request, event, form, status=400)
        try:
            update_event(event, request.user, request=request, **form.changed_values())
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            # Świeży obiekt z bazy: ``ModelForm`` zdążył już wpisać odrzucone wartości do
            # ``form.instance``, a strona ma pokazać stan, który faktycznie obowiązuje.
            event = _event(request.competition, pk)
            return self._render(request, event, EditionEventForm(instance=event), status=exc.status_code)
        messages.success(request, f"Wydarzenie „{event.title}” zostało zapisane.")
        return redirect(reverse("web:coordinator-events"))

    def _render(self, request, event: EditionEvent, form: EditionEventForm, *, status: int = 200):
        context = {"event": event, "edition": event.edition, "form": form}
        return TemplateResponse(request, FORM_TEMPLATE, context, status=status)


class EventDeleteView(CoordinatorRequiredMixin, View):
    """``POST /coordinator/events/<id>/delete/`` – usunięcie wydarzenia.

    Bez potwierdzenia w osobnym kroku i bez blokad: na wydarzeniu nie wisi ani jeden wiersz innej
    tabeli, a wpis audytowy (``event.deleted``) zachowuje pełną treść skasowanego terminu, więc
    pomyłkę da się odtworzyć z historii.
    """

    def post(self, request, pk: int):
        event = _event(request.competition, pk)
        title = event.title
        delete_event(event, request.user, request=request)
        messages.success(request, f"Wydarzenie „{title}” zostało usunięte.")
        return redirect(reverse("web:coordinator-events"))
