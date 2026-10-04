"""Webinary w LiveKit (zadanie WEB-01): panel koordynatora, strona odbiorcy, pokój, gość, webhook.

Widoki tylko pytają serwis (``apps.webinars.services``) i zamieniają odpowiedź na stronę albo JSON.
Pokój (``/webinars/<id>/room/``) to **nasza** strona z naszym interfejsem (``static/js/webinar-room.js``
na SDK ``livekit-client`` z ``static/vendor/``); token wejścia przeglądarka pobiera osobnym
``POST`` (``/webinars/<id>/token/``, CSRF, ``no-store``) – w HTML-u strony nie ma żadnego
poświadczenia, więc kopia strony w historii przeglądarki ani w pamięci pośrednika niczego nie otwiera.

Bramki:

- flaga konkursu ``webinars`` wyłączona – wszystkie adresy 404,
- flaga włączona, serwer LiveKit nieskonfigurowany – ekran koordynatora mówi o tym (bez formularza),
  ekrany odbiorców, pokój i gość – 404,
- webinar innego konkursu albo bez prawa do niego – 404 (zawężony queryset + ``Viewer.role``),
- webhook (``/integrations/livekit/webhook/``) – bez poprawnego podpisu 401, niczego nie zmienia.

Ekrany koordynatora są po polsku (zakres I18N-01); ekrany odbiorców, pokój i gość – przez ``gettext``.
"""

from __future__ import annotations

import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import View

from apps.core.api import DomainError
from apps.web.mixins import CoordinatorRequiredMixin
from apps.web.throttle import ThrottledFormMixin
from apps.web.views.video import _form_page, _no_referrer
from apps.webinars import livekit, services
from apps.webinars.forms import WebinarForm
from apps.webinars.models import RecordingStatus, Webinar

COORDINATOR_LIST_TEMPLATE = "web/coordinator/webinars.html"
COORDINATOR_DETAIL_TEMPLATE = "web/coordinator/webinar_detail.html"
COORDINATOR_FORM_TEMPLATE = "web/coordinator/webinar_form.html"
AUDIENCE_TEMPLATE = "web/webinars/list.html"
ROOM_TEMPLATE = "web/webinars/room.html"
GUEST_TEMPLATE = "web/webinars/guest.html"

#: Ile minionych webinarów pokazuje lista koordynatora. Starsze zostają w bazie i w audycie.
HISTORY_LIMIT = 30

#: Klucz sesji gościa: ``{"<pk webinaru>": {"identity": "g-…", "name": "…"}}``.
GUEST_SESSION_KEY = "webinar_guests"


def _json(data: dict, status: int = 200) -> JsonResponse:
    """Odpowiedź JSON bez pamięci podręcznej i bez ``Referer`` – może nieść token."""
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "no-store"
    return _no_referrer(response)


def _json_error(exc: DomainError) -> JsonResponse:
    return _json({"code": exc.machine_code, "detail": str(exc.detail)}, status=exc.status_code)


class JsonThrottleMixin:
    """429 jako JSON dla adresów wołanych przez ``webinar-room.js`` (token, polecenia prowadzącego).

    Strona HTML z limitem (``ThrottledFormMixin.throttled_response``) nie dałaby się pokazać w pokoju –
    JS czyta ``detail`` i wyświetla je w pasku stanu, a ``Retry-After`` mówi, kiedy spróbować znowu.
    """

    def throttled_response(self, request, wait: float):
        from apps.web.throttle import THROTTLE_MESSAGE

        retry_after = max(1, int(wait) + 1)
        response = _json(
            {"code": "THROTTLED", "detail": _(THROTTLE_MESSAGE), "retry_after": retry_after}, 429
        )
        response["Retry-After"] = str(retry_after)
        return response


class WebinarFeatureMixin:
    """Flaga konkursu: bez niej adresy webinarów nie istnieją (404)."""

    def dispatch(self, request, *args, **kwargs):
        if not services.enabled(getattr(request, "competition", None)):
            raise Http404("Webinary są w tym konkursie wyłączone.")
        return super().dispatch(request, *args, **kwargs)


class WebinarAvailableMixin:
    """Flaga **i** serwer LiveKit – warunek ekranów odbiorców, pokoju i gościa."""

    def dispatch(self, request, *args, **kwargs):
        if not services.available(getattr(request, "competition", None)):
            raise Http404("Webinary są w tym konkursie niedostępne.")
        return super().dispatch(request, *args, **kwargs)


# --- koordynator ----------------------------------------------------------------------------------


class _CoordinatorMixin(CoordinatorRequiredMixin, WebinarFeatureMixin):
    """Koordynator tego konkursu i flaga. Webinar spoza konkursu – 404 z zawężonego querysetu."""

    def webinar(self, request, pk: int) -> Webinar:
        return get_object_or_404(
            Webinar.objects.for_competition(request.competition).select_related(
                "competition", "stage", "created_by"
            ),
            pk=pk,
        )

    def detail_url(self, webinar) -> str:
        return reverse("web:coordinator-webinar", args=[webinar.pk])


@method_decorator(never_cache, name="dispatch")
class CoordinatorWebinarsView(_CoordinatorMixin, View):
    """``GET|POST /coordinator/webinars/`` – lista i formularz nowego webinaru."""

    def render(self, request, form=None, status=200):
        now = timezone.now()
        competition = request.competition
        rows = list(
            Webinar.objects.for_competition(competition)
            .select_related("stage", "created_by", "competition")
            .order_by("starts_at", "id")
        )
        upcoming, history = [], []
        for webinar in rows:
            closed = webinar.cancelled_at is not None or services.window_state(webinar, now) == "closed"
            (history if closed else upcoming).append(webinar)
        history.reverse()
        configured = livekit.configured()
        context = {
            "configured": configured,
            "form": (form or WebinarForm(competition=competition)) if configured else None,
            "upcoming": upcoming,
            "history": history[:HISTORY_LIMIT],
            "zone": services.competition_zone(competition),
            "now": now,
        }
        return TemplateResponse(request, COORDINATOR_LIST_TEMPLATE, context, status=status)

    def get(self, request):
        return self.render(request)

    def post(self, request):
        if not livekit.configured():
            messages.error(request, "Serwer LiveKit nie jest skonfigurowany – poproś operatora platformy.")
            return redirect("web:coordinator-webinars")
        form = WebinarForm(request.POST, competition=request.competition)
        if not form.is_valid():
            return self.render(request, form, status=400)
        try:
            webinar = services.create_webinar(
                request.competition,
                request.user,
                form.service_data(),
                form.cleaned_data.get("co_moderators") or [],
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return self.render(request, form, status=exc.status_code)
        messages.success(request, "Webinar zapisany. Odbiorcy widzą go w swoim panelu.")
        return redirect(self.detail_url(webinar))


@method_decorator(never_cache, name="dispatch")
class CoordinatorWebinarDetailView(_CoordinatorMixin, View):
    """``GET /coordinator/webinars/<id>/`` – stan, czynności, link gościa, obecność, nagrania."""

    def get(self, request, pk: int):
        from apps.accounts.activation import absolute_url

        webinar = self.webinar(request, pk)
        configured = livekit.configured()
        guest_link = ""
        if webinar.public_link and webinar.cancelled_at is None:
            guest_link = absolute_url(
                reverse("web:webinar-guest", args=[webinar.public_key]), request=request
            )
        attendees = list(webinar.attendees.select_related("user").order_by("first_token_at", "id"))
        recordings = list(webinar.recordings.order_by("-started_at", "-id"))
        context = {
            "webinar": webinar,
            "configured": configured,
            "state": services.window_state(webinar),
            "can_start": configured
            and webinar.cancelled_at is None
            and timezone.now() < services.join_window(webinar)[1],
            "live": webinar.started_at is not None
            and webinar.ended_at is None
            and webinar.cancelled_at is None,
            "attendees": attendees,
            "present": sum(1 for attendee in attendees if attendee.present),
            "recordings": recordings,
            "recording_active": any(item.status == RecordingStatus.ACTIVE for item in recordings),
            "co_moderators": list(webinar.co_moderators.order_by("last_name", "first_name")),
            "guest_link": guest_link,
            "zone": services.competition_zone(request.competition),
            "retention_days": settings.WEBINAR_RETENTION_DAYS,
        }
        return _form_page(TemplateResponse(request, COORDINATOR_DETAIL_TEMPLATE, context))


@method_decorator(never_cache, name="dispatch")
class CoordinatorWebinarEditView(_CoordinatorMixin, View):
    """``GET|POST /coordinator/webinars/<id>/edit/``."""

    def render(self, request, webinar, form, status=200):
        return TemplateResponse(
            request, COORDINATOR_FORM_TEMPLATE, {"webinar": webinar, "form": form}, status=status
        )

    def get(self, request, pk: int):
        webinar = self.webinar(request, pk)
        return self.render(request, webinar, WebinarForm(instance=webinar, competition=request.competition))

    def post(self, request, pk: int):
        webinar = self.webinar(request, pk)
        if webinar.cancelled_at is not None:
            messages.error(request, "Odwołanego webinaru nie da się zmienić.")
            return redirect(self.detail_url(webinar))
        # Formularz bez ``instance`` do walidacji: ``ModelForm`` z instancją przepisałby wartości
        # do obiektu przed decyzją serwisu (``_post_clean``), a zmiany ma liczyć serwis.
        form = WebinarForm(request.POST, competition=request.competition, extra_stage_id=webinar.stage_id)
        if not form.is_valid():
            return self.render(request, webinar, form, status=400)
        try:
            services.update_webinar(
                webinar,
                request.user,
                form.service_data(),
                form.cleaned_data.get("co_moderators") or [],
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return self.render(request, webinar, form, status=exc.status_code)
        messages.success(request, "Zmiany zapisane.")
        return redirect(self.detail_url(webinar))


class _CoordinatorActionMixin(_CoordinatorMixin, ThrottledFormMixin):
    """Czynność POST na jednym webinarze: limit per konto, ``no-store``, powrót na szczegóły."""

    throttle_scope = "webinar_control"

    @method_decorator(never_cache)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def run(self, request, webinar, action, success: str):
        try:
            action()
        except DomainError as exc:
            if exc.status_code == 404:
                raise Http404(str(exc.detail)) from exc
            messages.error(request, str(exc.detail))
        else:
            if success:
                messages.success(request, success)
        return redirect(self.detail_url(webinar))


class CoordinatorWebinarStartView(_CoordinatorActionMixin, View):
    """``POST /coordinator/webinars/<id>/start/`` – rozpoczęcie i przejście do pokoju."""

    def post(self, request, pk: int):
        webinar = self.webinar(request, pk)
        try:
            services.start(webinar, request.user, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect(self.detail_url(webinar))
        return redirect("web:webinar-room", pk=webinar.pk)


class CoordinatorWebinarEndView(_CoordinatorActionMixin, View):
    """``POST /coordinator/webinars/<id>/end/`` – zamyka pokój dla wszystkich."""

    def post(self, request, pk: int):
        webinar = self.webinar(request, pk)
        return self.run(
            request,
            webinar,
            lambda: services.end(webinar, request.user, request=request),
            "Webinar zakończony – pokój zamknięty dla wszystkich.",
        )


class CoordinatorWebinarCancelView(_CoordinatorActionMixin, View):
    """``POST /coordinator/webinars/<id>/cancel/`` – odwołanie (znika odbiorcom, link gościa gaśnie)."""

    def post(self, request, pk: int):
        webinar = self.webinar(request, pk)
        services.cancel_webinar(webinar, request.user, request=request)
        messages.success(request, "Webinar odwołany. Odbiorcy już go nie widzą.")
        return redirect("web:coordinator-webinars")


class CoordinatorWebinarAnnounceView(_CoordinatorActionMixin, View):
    """``POST /coordinator/webinars/<id>/announce/`` – zaproszenie e-mailem, raz."""

    def post(self, request, pk: int):
        webinar = self.webinar(request, pk)
        if not livekit.configured():
            messages.error(request, "Serwer LiveKit nie jest skonfigurowany – poproś operatora platformy.")
            return redirect(self.detail_url(webinar))
        try:
            sent = services.announce(webinar, request.user, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            if sent:
                messages.success(request, "Zaproszenia trafiły do kolejki wysyłki.")
            else:
                messages.info(request, "Zaproszenie do tego webinaru zostało już wysłane.")
        return redirect(self.detail_url(webinar))


class CoordinatorWebinarRecordingView(_CoordinatorActionMixin, View):
    """``POST /coordinator/webinars/<id>/recordings/`` – nagrywanie i nagrania.

    ``action``: ``start`` / ``stop`` (egress), ``publish`` / ``unpublish`` / ``delete`` (jedno nagranie,
    pole ``recording``). Usunięcie wymaga zaznaczenia pola potwierdzenia – pliku nie da się odzyskać.
    """

    def post(self, request, pk: int):
        webinar = self.webinar(request, pk)
        action = request.POST.get("action", "")
        recording = request.POST.get("recording", "")
        user = request.user
        if action == "start":
            return self.run(
                request,
                webinar,
                lambda: services.start_recording(webinar, user, request=request),
                "Nagrywanie włączone.",
            )
        if action == "stop":
            return self.run(
                request,
                webinar,
                lambda: services.stop_recording(webinar, user, request=request),
                "Nagrywanie zatrzymane – plik pojawi się po przetworzeniu.",
            )
        if action in ("publish", "unpublish"):
            return self.run(
                request,
                webinar,
                lambda: services.set_recording_published(
                    webinar, recording, action == "publish", user, request=request
                ),
                "Nagranie opublikowane." if action == "publish" else "Nagranie wycofane.",
            )
        if action == "mark_failed":
            return self.run(
                request,
                webinar,
                lambda: services.mark_recording_failed(webinar, recording, user, request=request),
                "Stan nagrania uzgodniony z serwerem.",
            )
        if action == "delete":
            if request.POST.get("confirm") != "1":
                messages.error(request, "Zaznacz potwierdzenie – usuniętego nagrania nie da się odzyskać.")
                return redirect(self.detail_url(webinar))
            return self.run(
                request,
                webinar,
                lambda: services.delete_recording(webinar, recording, user, request=request),
                "Nagranie usunięte.",
            )
        raise Http404("Nieznana czynność.")


class CoordinatorWebinarStreamView(_CoordinatorActionMixin, View):
    """``POST /coordinator/webinars/<id>/stream/`` – transmisja RTMP (YouTube): ``start`` / ``stop``.

    Klucz strumienia przychodzi w polu formularza i idzie wyłącznie do LiveKit – nie zapisujemy go
    i nie odsyłamy w odpowiedzi (``services.start_stream``).
    """

    def post(self, request, pk: int):
        webinar = self.webinar(request, pk)
        action = request.POST.get("action", "")
        if action == "start":
            target = request.POST.get("target", "")
            return self.run(
                request,
                webinar,
                lambda: services.start_stream(webinar, request.user, target, request=request),
                "Transmisja włączona.",
            )
        if action == "stop":
            return self.run(
                request,
                webinar,
                lambda: services.stop_stream(webinar, request.user, request=request),
                "Transmisja zatrzymana.",
            )
        raise Http404("Nieznana czynność.")


class CoordinatorWebinarGuestLinkView(_CoordinatorActionMixin, View):
    """``POST /coordinator/webinars/<id>/guest-link/`` – nowy klucz linku gościa (stary od razu 404)."""

    def post(self, request, pk: int):
        webinar = self.webinar(request, pk)
        return self.run(
            request,
            webinar,
            lambda: services.rotate_guest_link(webinar, request.user, request=request),
            "Nowy link dla gości jest gotowy – poprzedni już nie działa.",
        )


class CoordinatorWebinarAttendeeView(_CoordinatorActionMixin, View):
    """``POST /coordinator/webinars/<id>/attendees/`` – ``readmit`` (wpuść ponownie usuniętą osobę)."""

    def post(self, request, pk: int):
        webinar = self.webinar(request, pk)
        if request.POST.get("action") != "readmit":
            raise Http404("Nieznana czynność.")
        identity = request.POST.get("identity", "")
        return self.run(
            request,
            webinar,
            lambda: services.readmit(webinar, request.user, identity, request=request),
            "Ta osoba może wejść ponownie.",
        )


# --- odbiorcy (uczestnik, komisja, kapitan, prowadzący) -------------------------------------------


class _AudienceMixin(LoginRequiredMixin, WebinarAvailableMixin):
    """Zalogowany z jakąkolwiek rolą w konkursie. Prawo do konkretnego webinaru – ``Viewer.role``."""

    def viewer(self, request) -> services.Viewer:
        viewer = services.Viewer.of(request.user, request.competition)
        if not services.has_any_role(viewer):
            raise PermissionDenied(_("Ta strona jest dostępna dla uczestników i komisji konkursu."))
        return viewer

    def webinar(self, request, pk: int) -> Webinar:
        return get_object_or_404(
            Webinar.objects.for_competition(request.competition).select_related("competition", "stage"), pk=pk
        )

    def allowed_webinar(self, request, pk: int) -> tuple[services.Viewer, Webinar, str]:
        viewer = self.viewer(request)
        webinar = self.webinar(request, pk)
        role = viewer.role(webinar)
        if role is None:
            raise Http404("Nie ma takiego webinaru.")
        return viewer, webinar, role


@method_decorator(never_cache, name="dispatch")
class WebinarsView(_AudienceMixin, View):
    """``GET /webinars/`` – nadchodzące webinary, nagrania minionych, ustawienie listów."""

    def get(self, request):
        viewer = self.viewer(request)
        now = timezone.now()
        upcoming, past = services.webinars_for(viewer, now)
        rows = [
            {
                "webinar": webinar,
                "state": services.window_state(webinar, now),
                "moderator": viewer.is_moderator(webinar),
                "opens_at": services.join_window(webinar)[0],
            }
            for webinar in upcoming
        ]
        past_rows = [
            {"webinar": webinar, "recordings": services.published_recordings(webinar)} for webinar in past
        ]
        context = {
            "rows": rows,
            "past_rows": [row for row in past_rows if row["recordings"]],
            "zone": services.competition_zone(request.competition),
            "notifications": services.notifications_enabled(request.user),
            "is_participant": viewer.participant is not None,
        }
        return _form_page(TemplateResponse(request, AUDIENCE_TEMPLATE, context))


class WebinarNotificationsView(_AudienceMixin, View):
    """``POST /webinars/notifications/`` – włącza albo wyłącza listy o webinarach."""

    def post(self, request):
        self.viewer(request)
        enabled = request.POST.get("email_on_webinar") == "1"
        services.save_notifications(request.user, enabled)
        messages.success(
            request,
            _("Listy o webinarach włączone.") if enabled else _("Listy o webinarach wyłączone."),
        )
        return redirect(f"{reverse('web:webinars')}#powiadomienia")


#: Plik SDK ``livekit-client`` (wersja przypięta w ``static/vendor/livekit-client/VERSION``, wgrywany
#: skryptem ``scripts/vendor_livekit_client.sh`` ze sprawdzeniem sumy z rejestru npm).
SDK_PATH = "vendor/livekit-client/livekit-client.umd.js"


def sdk_url() -> str:
    """Adres SDK albo pusty napis, gdy pliku nie ma w statykach (pokój mówi wtedy, czego brakuje).

    ``static()`` z magazynem z manifestem podnosi ``ValueError`` dla pliku spoza manifestu – bez tej
    osłony brak jednego pliku dostawcy zamieniałby stronę pokoju w błąd 500.
    """
    from django.contrib.staticfiles import finders
    from django.templatetags.static import static

    if not finders.find(SDK_PATH):
        return ""
    try:
        return static(SDK_PATH)
    except ValueError:
        return ""


def room_strings() -> dict[str, str]:
    """Napisy dla ``webinar-room.js`` – w języku żądania, przez ``json_script`` (dane, nie kod)."""
    return {
        "connecting": _("Łączenie…"),
        "connected": _("Jesteś w pokoju."),
        "disconnected": _("Rozłączono. Odśwież stronę, żeby wrócić."),
        "retry": _("Spróbuj ponownie"),
        "failed": _("Nie udało się połączyć z pokojem."),
        "you": _("Ty"),
        "presenter": _("prowadzący"),
        "hand": _("podniesiona ręka"),
        "raiseHand": _("Podnieś rękę"),
        "lowerHand": _("Opuść rękę"),
        "grant": _("Daj głos"),
        "revoke": _("Odbierz głos"),
        "remove": _("Usuń z pokoju"),
        "removeConfirm": _("Usunąć tę osobę z pokoju?"),
        "gridView": _("Widok siatki"),
        "speakerView": _("Widok prelegenta"),
        "record": _("Nagrywaj"),
        "stopRecord": _("Zatrzymaj nagrywanie"),
        "recording": _("Trwa nagrywanie"),
        "speakerGranted": _("Prowadzący dał Ci głos – możesz włączyć mikrofon i kamerę."),
        "speakerRevoked": _("Prowadzący odebrał Ci głos."),
        "screen": _("Udostępnij ekran"),
        "stopScreen": _("Zakończ udostępnianie"),
        "screenOf": _("Ekran: %(name)s"),
        "error": _("Coś poszło nie tak. Spróbuj ponownie."),
    }


def _room_context(
    webinar, *, role: str, token_url: str, control_url: str, back_url: str, can_record: bool
) -> dict:
    return {
        "webinar": webinar,
        "role": role,
        "token_url": token_url,
        "control_url": control_url,
        "back_url": back_url,
        "can_record": can_record,
        "zone": services.competition_zone(webinar.competition),
        "strings": room_strings(),
        "sdk_url": sdk_url(),
    }


@method_decorator(never_cache, name="dispatch")
class WebinarRoomView(_AudienceMixin, View):
    """``GET /webinars/<id>/room/`` – strona pokoju. Bez tokenu: ten pobiera JS osobnym POST-em."""

    def get(self, request, pk: int):
        viewer, webinar, role = self.allowed_webinar(request, pk)
        presenter = role == services.ROLE_PRESENTER
        back = reverse("web:webinars")
        if presenter and viewer.coordinator:
            back = reverse("web:coordinator-webinar", args=[webinar.pk])
        context = _room_context(
            webinar,
            role=role,
            token_url=reverse("web:webinar-token", args=[webinar.pk]),
            control_url=reverse("web:webinar-control", args=[webinar.pk]) if presenter else "",
            back_url=back,
            can_record=presenter and webinar.record,
        )
        return _form_page(TemplateResponse(request, ROOM_TEMPLATE, context))


class WebinarTokenView(JsonThrottleMixin, _AudienceMixin, ThrottledFormMixin, View):
    """``POST /webinars/<id>/token/`` – token wejścia (JSON, ``no-store``). Limit per konto.

    POST z CSRF, a nie GET: token jest poświadczeniem, a prowadzący przy tym **rozpoczyna** webinar.
    Odmowa niesie zdanie dla człowieka (okno, „jeszcze nie rozpoczęty”) – JS pokazuje je w pokoju.
    """

    throttle_scope = "webinar_join"

    @method_decorator(never_cache)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, pk: int):
        viewer = self.viewer(request)
        webinar = self.webinar(request, pk)
        try:
            data = services.join_token(webinar, viewer, request=request)
        except DomainError as exc:
            if exc.status_code == 404:
                raise Http404(str(exc.detail)) from exc
            return _json_error(exc)
        return _json(data)


class WebinarControlView(JsonThrottleMixin, _AudienceMixin, ThrottledFormMixin, View):
    """``POST /webinars/<id>/control/`` – polecenia prowadzącego z pokoju (JSON).

    ``action``: ``speaker`` / ``listener`` (daj / odbierz głos), ``remove`` – z polem ``identity``;
    ``record_start`` / ``record_stop``. Wyłącznie prowadzący **tego** webinaru (``Viewer.is_moderator``);
    reszta – 404, jak webinar, którego nie ma. Własny kubełek ``webinar_control`` (600/h): sesja
    pytań to dziesiątki „Daj głos” na godzinę i wspólny limit z tokenami odcinał prowadzącego.
    """

    throttle_scope = "webinar_control"

    @method_decorator(never_cache)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, pk: int):
        _viewer, webinar, role = self.allowed_webinar(request, pk)
        if role != services.ROLE_PRESENTER:
            raise Http404("Nie ma takiego webinaru.")
        action = request.POST.get("action", "")
        identity = request.POST.get("identity", "")
        try:
            if action in ("speaker", "listener"):
                services.set_speaker(webinar, request.user, identity, action == "speaker", request=request)
            elif action == "remove":
                services.remove_from_room(webinar, request.user, identity, request=request)
            elif action == "record_start":
                services.start_recording(webinar, request.user, request=request)
            elif action == "record_stop":
                services.stop_recording(webinar, request.user, request=request)
            else:
                return _json({"code": "UNKNOWN_ACTION", "detail": "Nieznana czynność."}, status=400)
        except DomainError as exc:
            return _json_error(exc)
        return _json({"ok": True})


@method_decorator(never_cache, name="dispatch")
class WebinarRecordingView(_AudienceMixin, View):
    """``GET /webinars/<id>/recordings/<pk>/`` – sprawdzenie dostępu, potem 302 na krótki adres pliku."""

    def get(self, request, pk: int, recording_pk: int):
        viewer = self.viewer(request)
        webinar = self.webinar(request, pk)
        try:
            url = services.recording_url_for(webinar, viewer, recording_pk)
        except DomainError as exc:
            raise Http404(str(exc.detail)) from exc
        return _no_referrer(HttpResponseRedirect(url))


# --- gość z linku ---------------------------------------------------------------------------------


class _GuestMixin(WebinarAvailableMixin):
    def resolve(self, request, key: str) -> Webinar:
        webinar = services.webinar_for_key(request.competition, key)
        if webinar is None:
            raise Http404("Nie ma takiego zaproszenia.")
        return webinar

    @staticmethod
    def guest(request, webinar) -> dict | None:
        return (request.session.get(GUEST_SESSION_KEY) or {}).get(str(webinar.pk))


@method_decorator(never_cache, name="dispatch")
class WebinarGuestView(_GuestMixin, ThrottledFormMixin, View):
    """``GET|POST /zaproszenie/webinar/<klucz>/`` – gość bez konta podaje nazwę i przechodzi do pokoju.

    GET pokazuje tytuł, termin i pole nazwy. POST zapisuje w **sesji** pseudonim gościa (``g-…``) i
    nazwę, i przekierowuje do pokoju gościa – token powstaje dopiero tam, osobnym POST-em. Podgląd
    linku w komunikatorze ani skaner poczty nie wchodzą więc do pokoju. Klucz spoza konkursu, link
    wyłączony, webinar odwołany – 404 bez śladu, że webinar istniał. Limit po IP.
    """

    throttle_scope = "webinar_guest"

    def render(self, request, webinar, *, error: str = "", name: str = "", status: int = 200):
        from apps.competitions.jitsi_jwt import short_name

        context = {
            "webinar": webinar,
            "state": services.window_state(webinar),
            "zone": services.competition_zone(request.competition),
            "error": error,
            "name": name,
            "account_name": short_name(request.user) if request.user.is_authenticated else "",
            "name_max_length": services.GUEST_NAME_MAX_LENGTH,
        }
        return _form_page(TemplateResponse(request, GUEST_TEMPLATE, context, status=status))

    def get(self, request, key: str):
        return self.render(request, self.resolve(request, key))

    def post(self, request, key: str):
        from apps.competitions.jitsi_jwt import short_name

        webinar = self.resolve(request, key)
        account_name = short_name(request.user) if request.user.is_authenticated else ""
        name = services.clean_guest_name(request.POST.get("name", "")) or account_name
        if not name:
            return self.render(
                request,
                webinar,
                error=_("Podaj imię i nazwisko albo nazwę, pod którą zobaczą Cię inni."),
                status=400,
            )
        guests = dict(request.session.get(GUEST_SESSION_KEY) or {})
        previous = guests.get(str(webinar.pk)) or {}
        guests[str(webinar.pk)] = {
            "identity": previous.get("identity") or services.new_guest_identity(),
            "name": name,
        }
        request.session[GUEST_SESSION_KEY] = guests
        return redirect("web:webinar-guest-room", key=key)


@method_decorator(never_cache, name="dispatch")
class WebinarGuestRoomView(_GuestMixin, View):
    """``GET /zaproszenie/webinar/<klucz>/room/`` – pokój gościa (bez nazwy w sesji – do formularza)."""

    def get(self, request, key: str):
        webinar = self.resolve(request, key)
        if self.guest(request, webinar) is None:
            return redirect("web:webinar-guest", key=key)
        context = _room_context(
            webinar,
            role=services.ROLE_VIEWER,
            token_url=reverse("web:webinar-guest-token", args=[key]),
            control_url="",
            back_url=reverse("web:webinar-guest", args=[key]),
            can_record=False,
        )
        return _form_page(TemplateResponse(request, ROOM_TEMPLATE, context))


class WebinarGuestTokenView(JsonThrottleMixin, _GuestMixin, ThrottledFormMixin, View):
    """``POST /zaproszenie/webinar/<klucz>/token/`` – token gościa (widz) dla pseudonimu z sesji."""

    throttle_scope = "webinar_guest"

    @method_decorator(never_cache)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, key: str):
        webinar = self.resolve(request, key)
        guest = self.guest(request, webinar)
        if guest is None:
            raise Http404("Brak nazwy gościa.")
        try:
            data = services.guest_token(
                webinar,
                identity=guest.get("identity", ""),
                name=guest.get("name", ""),
                user=request.user,
                request=request,
            )
        except DomainError as exc:
            return _json_error(exc)
        return _json(data)


# --- webhook LiveKit ------------------------------------------------------------------------------


@method_decorator(csrf_exempt, name="dispatch")
class LiveKitWebhookView(View):
    """``POST /integrations/livekit/webhook/`` – zdarzenia serwera LiveKit (podpis **obowiązkowy**).

    CSRF wyłączony, bo nadawcą jest serwer, nie przeglądarka; w jego miejsce stoi podpis JWT
    z kluczem API i skrótem treści (``livekit.verify_webhook``). Bez podpisu, z obcym kluczem albo
    z podmienioną treścią – 401 i żadnej zmiany. Instalacja bez LiveKit – 404 (adresu „nie ma”).
    Webhook jest wspólny dla instalacji: konkurs rozstrzyga nazwa pokoju w zdarzeniu.
    """

    def post(self, request):
        if not livekit.configured():
            raise Http404("LiveKit nie jest skonfigurowany.")
        try:
            event = livekit.verify_webhook(request.body, request.headers.get("Authorization", ""))
        except livekit.WebhookInvalid:
            return HttpResponse(status=401)
        result = services.handle_webhook(event)
        return HttpResponse(json.dumps({"result": result}), content_type="application/json")
