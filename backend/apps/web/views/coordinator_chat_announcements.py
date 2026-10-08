"""Ogłoszenia organizatora w Wiadomościach uczestników (zadanie CZ-ANN-01) – panel koordynatora.

Ekran ``/coordinator/inbox-announcements/``: lista, dodanie (szkic albo „Zapisz i opublikuj”),
edycja, „Opublikuj / Wyłącz” i usunięcie. Osobny adres od ``/coordinator/announcements/``, bo to
inna wiadomość: tamten ekran prowadzi baner nad każdą stroną serwisu (``cms.Announcement``), ten –
wpisy nad skrzynką Wiadomości i na pulpicie zalogowanego uczestnika (``chat.OrganizerAnnouncement``).

Reguły:

- **wyłącznie koordynator tego konkursu** (``CoordinatorRequiredMixin`` – inne role 403), a każdy
  odczyt ogłoszenia przez ``for_competition`` – cudze jest 404, nie 403. Rolę sprawdza też serwis,
- **ekran działa przy wyłączonych Wiadomościach.** Pulpit uczestnika pokazuje ogłoszenia zawsze,
  więc wyłączenie rozmów nie może zabrać organizatorowi drogi do ogłoszenia linku,
- akcje zmieniające stan są ``POST``-em z CSRF na osobnych adresach (``GET`` → 405), z limitem
  ``chat`` liczonym per konto – ten sam, co przy wiadomościach koordynatora.
"""

from __future__ import annotations

from django import forms
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.generic import View

from apps.chat import announcements as service
from apps.chat.models import ANNOUNCEMENT_BODY_LENGTH, ANNOUNCEMENT_TITLE_LENGTH, OrganizerAnnouncement
from apps.core.api import DomainError
from apps.web.forms import REQUIRED_CSS_CLASS, LocalDateTimeField
from apps.web.mixins import CoordinatorRequiredMixin
from apps.web.throttle import ThrottledFormMixin

TEMPLATE = "web/coordinator/chat_announcements.html"
LIST_URL = "web:coordinator-inbox-announcements"

#: Etykiety stanów z ``OrganizerAnnouncement.state`` – z klasą odznaki.
STATE_BADGES = {
    "live": ("widoczne", "badge--ok"),
    "draft": ("wyłączone", "badge--neutral"),
    "scheduled": ("zaplanowane", "badge--info"),
    "expired": ("wygasło", "badge--neutral"),
}


class InboxAnnouncementForm(forms.Form):
    """Tytuł, treść i opcjonalne okno. Reguły (puste pola, okno) powtarza serwis – formularz daje
    tylko komunikat przy właściwym polu zamiast ogólnego błędu nad formularzem."""

    required_css_class = REQUIRED_CSS_CLASS

    title = forms.CharField(label="Tytuł", max_length=ANNOUNCEMENT_TITLE_LENGTH)
    body = forms.CharField(
        label="Treść",
        max_length=ANNOUNCEMENT_BODY_LENGTH,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text=(
            "Zwykły tekst. Wklejony adres (np. https://meet.google.com/…) stanie się klikalnym "
            "odnośnikiem; znaczniki HTML nie zadziałają."
        ),
    )
    published_from = LocalDateTimeField(
        label="Widoczne od", required=False, help_text="Puste = od chwili publikacji."
    )
    published_until = LocalDateTimeField(
        label="Widoczne do", required=False, help_text="Puste = do wyłączenia."
    )

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("published_from"), cleaned.get("published_until")
        if start is not None and end is not None and end <= start:
            self.add_error("published_until", "Koniec widoczności musi być późniejszy niż jej początek.")
        return cleaned


def _initial(announcement: OrganizerAnnouncement) -> dict:
    return {
        "title": announcement.title,
        "body": announcement.body,
        "published_from": announcement.published_from,
        "published_until": announcement.published_until,
    }


class AnnouncementLookupMixin(CoordinatorRequiredMixin):
    """Ogłoszenie z adresu – wyłącznie z konkursu żądania (cudze = 404)."""

    def announcement(self, pk) -> OrganizerAnnouncement:
        row = service.announcements_for_panel(self.competition).filter(pk=pk).first()
        if row is None:
            raise Http404("Nie ma takiego ogłoszenia.")
        return row

    def render_screen(self, request, form, edited=None, *, status: int = 200):
        now = timezone.now()
        rows = []
        for item in service.announcements_for_panel(self.competition):
            label, badge = STATE_BADGES[item.state(now)]
            rows.append({"announcement": item, "state_label": label, "state_badge": badge})
        context = {
            "form": form,
            "edited": edited,
            "rows": rows,
            "visible_limit": service.VISIBLE_LIMIT,
        }
        return TemplateResponse(request, TEMPLATE, context, status=status)


class CoordinatorInboxAnnouncementsView(AnnouncementLookupMixin, ThrottledFormMixin, View):
    """``/coordinator/inbox-announcements/`` – lista i formularz nowego ogłoszenia."""

    throttle_scope = "chat"

    def get(self, request):
        return self.render_screen(request, InboxAnnouncementForm())

    def post(self, request):
        form = InboxAnnouncementForm(request.POST)
        if not form.is_valid():
            return self.render_screen(request, form, status=400)
        publish = request.POST.get("publish") == "1"
        try:
            service.create_announcement(
                competition=self.competition,
                actor=request.user,
                publish=publish,
                request=request,
                **form.cleaned_data,
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self.render_screen(request, form, status=exc.status_code)
        messages.success(
            request,
            "Ogłoszenie zostało opublikowane – uczestnicy widzą je w Wiadomościach i na pulpicie."
            if publish
            else "Ogłoszenie zostało zapisane jako szkic. Uczestnicy zobaczą je po publikacji.",
        )
        return redirect(reverse(LIST_URL))


class CoordinatorInboxAnnouncementEditView(AnnouncementLookupMixin, ThrottledFormMixin, View):
    """``/coordinator/inbox-announcements/<id>/`` – edycja treści i okna (stan publikacji bez zmian)."""

    throttle_scope = "chat"

    def get(self, request, pk: int):
        announcement = self.announcement(pk)
        return self.render_screen(
            request, InboxAnnouncementForm(initial=_initial(announcement)), announcement
        )

    def post(self, request, pk: int):
        announcement = self.announcement(pk)
        form = InboxAnnouncementForm(request.POST)
        if not form.is_valid():
            return self.render_screen(request, form, announcement, status=400)
        try:
            service.update_announcement(
                announcement=announcement, actor=request.user, request=request, **form.cleaned_data
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self.render_screen(request, form, announcement, status=exc.status_code)
        messages.success(request, "Zmiany w ogłoszeniu zostały zapisane.")
        return redirect(reverse(LIST_URL))


class AnnouncementActionView(AnnouncementLookupMixin, ThrottledFormMixin, View):
    """Baza akcji „Opublikuj”, „Wyłącz” i „Usuń”: wyłącznie ``POST``, potem powrót na listę."""

    http_method_names = ["post"]
    throttle_scope = "chat"

    def perform(self, request, announcement) -> str:  # pragma: no cover - klasa abstrakcyjna
        raise NotImplementedError

    def post(self, request, pk: int):
        announcement = self.announcement(pk)
        try:
            message = self.perform(request, announcement)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, message)
        return redirect(reverse(LIST_URL))


class CoordinatorInboxAnnouncementPublishView(AnnouncementActionView):
    def perform(self, request, announcement) -> str:
        service.set_published(announcement=announcement, actor=request.user, published=True, request=request)
        return f"Ogłoszenie „{announcement.title}” jest opublikowane."


class CoordinatorInboxAnnouncementUnpublishView(AnnouncementActionView):
    def perform(self, request, announcement) -> str:
        service.set_published(announcement=announcement, actor=request.user, published=False, request=request)
        return f"Ogłoszenie „{announcement.title}” zostało wyłączone – uczestnicy już go nie widzą."


class CoordinatorInboxAnnouncementDeleteView(AnnouncementActionView):
    def perform(self, request, announcement) -> str:
        title = announcement.title
        service.delete_announcement(announcement=announcement, actor=request.user, request=request)
        return f"Ogłoszenie „{title}” zostało usunięte."
