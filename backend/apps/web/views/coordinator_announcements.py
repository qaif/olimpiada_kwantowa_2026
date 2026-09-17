"""Komunikaty organizatora w panelu koordynatora: lista, dodanie, edycja i skasowanie.

Ekran jest w panelu, a nie tylko w ``/cms/``, i to jest cała jego wartość: komunikat pisze się
wtedy, gdy coś właśnie nie działa albo termin właśnie się przesunął – a wtedy koordynator jest
w swoim panelu, nie w edytorze treści. Droga przez Wagtaila zostaje jako druga (model jest tam
zarejestrowany jako snippet), bo redakcja bywa kimś innym niż organizator zawodów.

Wszystko na jednym adresie: lista, formularz dodania i formularz edycji. Komunikat ma sześć pól
i pisze się go w minutę – rozbicie tego na trzy ekrany kosztowałoby dwa przeładowania w sytuacji,
w której liczy się czas.
"""

from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.generic import View

from apps.cms.models import Announcement
from apps.core.models import audit
from apps.web.forms import AnnouncementForm
from apps.web.mixins import CoordinatorRequiredMixin

TEMPLATE = "web/coordinator/announcements.html"

#: Ile komunikatów pokazujemy. Bez stronicowania: komunikatów jest w edycji kilkanaście,
#: a lista służy do znalezienia tego, który trzeba zdjąć – nie do przeglądania historii.
LIMIT = 100


class CoordinatorAnnouncementsView(CoordinatorRequiredMixin, View):
    """``/coordinator/announcements/`` – lista komunikatów oraz ich dodawanie, edycja i usuwanie.

    Rozróżnienie „dodaj / zmień / usuń” idzie parametrem ``?edit=<id>`` i polem ``action``
    w formularzu, a nie trzema adresami. Adres z ``?edit=`` jest przy tym zwykłym GET-em, więc
    da się go odświeżyć i przesłać – w odróżnieniu od stanu trzymanego w sesji.

    Skasowanie jest **twarde**, a nie „wyłączenie”: do wyłączenia służy pole ``is_active``,
    a komunikat, który przestał być potrzebny, nie ma powodu zostawać w bazie jako wiersz, którego
    nikt już nigdy nie zobaczy. Ślad decyzji zostaje w audycie.
    """

    def get(self, request):
        edited = self._edited(request)
        form = AnnouncementForm(instance=edited) if edited is not None else AnnouncementForm()
        return self._render(request, form, edited)

    def post(self, request):
        if request.POST.get("action") == "delete":
            return self._delete(request)
        edited = self._edited(request)
        form = AnnouncementForm(request.POST, instance=edited)
        if not form.is_valid():
            return self._render(request, form, edited, status=400)
        announcement = form.save(commit=False)
        if edited is None:
            # Autora zapisujemy wyłącznie przy utworzeniu: „kto to ogłosił” jest pytaniem o źródło
            # komunikatu, a nie o to, kto ostatni poprawił w nim literówkę.
            announcement.created_by = request.user
        # Konkurs **z żądania**, a nie z formularza: komunikat wisi na każdej stronie serwisu,
        # więc „czyj jest” nie może być wartością, którą da się podstawić w POST. Pole nie stoi
        # w ``AnnouncementForm`` i stać nie ma – koordynator ogłasza w swoim konkursie albo
        # w żadnym.
        announcement.competition = request.competition
        announcement.save()
        audit(
            request.user,
            "announcement.updated" if edited is not None else "announcement.created",
            announcement,
            # Sam poziom i okno, bez treści: treść komunikatu jest jawna na każdej stronie serwisu,
            # ale wpis audytowy nie jest jej drugą kopią – ma mówić, kiedy i przez kogo się zmieniła.
            {"level": announcement.level, "is_active": announcement.is_active},
            request=request,
        )
        messages.success(
            request,
            "Komunikat został zapisany." if edited is not None else "Komunikat został dodany.",
        )
        return redirect(reverse("web:coordinator-announcements"))

    def _delete(self, request):
        announcement = get_object_or_404(
            Announcement.objects.for_competition(request.competition), pk=request.POST.get("pk") or 0
        )
        # Audyt **przed** skasowaniem: po ``delete()`` nie ma z czego wziąć identyfikatora celu.
        audit(
            request.user,
            "announcement.deleted",
            announcement,
            {"level": announcement.level},
            request=request,
        )
        announcement.delete()
        messages.success(request, "Komunikat został usunięty.")
        return redirect(reverse("web:coordinator-announcements"))

    def _edited(self, request) -> Announcement | None:
        """Komunikat wskazany do edycji (``?edit=<id>`` albo ukryte pole formularza)."""
        raw = request.POST.get("edit") if request.method == "POST" else request.GET.get("edit")
        if not raw:
            return None
        return get_object_or_404(Announcement.objects.for_competition(request.competition), pk=raw)

    def _render(self, request, form, edited, *, status: int = 200):
        now = timezone.now()
        rows = list(Announcement.objects.for_competition(request.competition)[:LIMIT])
        context = {
            "form": form,
            "edited": edited,
            "now": now,
            # ``is_live`` liczymy tutaj, a nie w szablonie: to reguła (okno czasowe plus wyłącznik),
            # a nie warunek wyświetlania, i ma być liczona tym samym kodem, co baner.
            "rows": [{"announcement": item, "live": item.is_live(now)} for item in rows],
        }
        return TemplateResponse(request, TEMPLATE, context, status=status)
