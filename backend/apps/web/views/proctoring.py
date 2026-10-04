"""Nadzór zdalny etapów online (zadanie PROC-01): konsola ucznia, siatka nadzorującego, panel koordynatora.

Widoki tylko pytają serwis (``apps.proctoring.services``) i zamieniają odpowiedź na stronę albo JSON.
Konsola ucznia i siatka to **nasze** strony na SDK ``livekit-client`` z ``static/vendor/`` (ten sam
plik i ta sama osłona braku pliku, co pokój webinaru); tokeny pobiera JS osobnym ``POST``-em
(CSRF, ``no-store``) – w HTML-u nie ma poświadczeń.

Bramki:

- flaga konkursu ``proctoring`` wyłączona – wszystkie adresy 404,
- etap bez nadzoru, etap innego konkursu, uczeń bez zgłoszenia, nadzorujący bez zakresu – 404,
- uczeń spoza zakresu nadzorującego (np. innej delegacji) – 404 z zawężonego querysetu.

Ekrany koordynatora po polsku (zakres I18N-01, jak WEB-01); konsola ucznia i siatka nadzorującego
(w IQO – opiekunowie drużyn) – przez ``gettext``. Limity per konto: ``proctoring_token`` (tokeny),
``proctoring_action`` (czynności nadzorujących), ``proctoring_client`` (konsola ucznia) – odpowiedź
na przekroczenie w JSON-ie, bo czytają ją skrypty.
"""

from __future__ import annotations

import json
from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.generic import View

from apps.core.api import DomainError
from apps.proctoring import services, windows
from apps.proctoring.forms import AlternativeForm, ProctoringConfigForm
from apps.proctoring.models import (
    AlternativeStatus,
    IdPhoto,
    IncidentCategory,
    IncidentSeverity,
    ProctorAssignment,
    ProctoringConfig,
    ProctoringRecording,
    ProctoringSession,
    ProctorKind,
)
from apps.web.mixins import CoordinatorRequiredMixin, ParticipantRequiredMixin
from apps.web.throttle import ThrottledFormMixin
from apps.web.views.video import _form_page, _no_referrer
from apps.web.views.webinars import sdk_url

CONSOLE_TEMPLATE = "web/proctoring/console.html"
PROCTOR_LIST_TEMPLATE = "web/proctoring/list.html"
GRID_TEMPLATE = "web/proctoring/grid.html"
REPORT_TEMPLATE = "web/proctoring/report.html"
COORDINATOR_LIST_TEMPLATE = "web/coordinator/proctoring.html"
COORDINATOR_STAGE_TEMPLATE = "web/coordinator/proctoring_stage.html"


def _json(data: dict, status: int = 200) -> JsonResponse:
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "no-store"
    return _no_referrer(response)


def _json_error(exc: DomainError) -> JsonResponse:
    return _json({"code": exc.machine_code, "detail": str(exc.detail)}, status=exc.status_code)


class FeatureMixin:
    """Flaga konkursu: bez niej adresy nadzoru nie istnieją (404)."""

    def dispatch(self, request, *args, **kwargs):
        if not services.enabled(getattr(request, "competition", None)):
            raise Http404("Nadzór zdalny jest w tym konkursie wyłączony.")
        return super().dispatch(request, *args, **kwargs)


class JsonThrottleMixin(ThrottledFormMixin):
    """Limit per konto z odpowiedzią JSON 429 – czytają ją skrypty konsoli i siatki."""

    @method_decorator(never_cache)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def throttled_response(self, request, wait: float):
        retry_after = max(1, int(wait) + 1)
        response = _json(
            {
                "code": "THROTTLED",
                "detail": _("Za dużo żądań. Spróbuj za chwilę."),
                "retry_after": retry_after,
            },
            status=429,
        )
        response["Retry-After"] = str(retry_after)
        return response


def _pk_or_404(raw) -> int:
    """Identyfikator z formularza: liczba albo 404 – nigdy ``ValueError`` z bazy (błąd 500)."""
    raw = str(raw or "").strip()
    if not raw.isdigit() or len(raw) > 18:
        raise Http404("Nieprawidłowy identyfikator.")
    return int(raw)


def _stage(request, stage_id: int):
    from apps.competitions.models import Stage

    return get_object_or_404(
        Stage.objects.for_competition(request.competition).select_related("edition__competition"), pk=stage_id
    )


# --- uczeń ----------------------------------------------------------------------------------------


class _StudentMixin(FeatureMixin, ParticipantRequiredMixin):
    """Uczeń zgłoszony do etapu z włączonym nadzorem – inaczej 404."""

    def load(self, request, stage_id: int):
        stage = _stage(request, stage_id)
        config = services.config_for(stage)
        participant = self.participant
        if config is None or services.entry_for(participant, stage) is None:
            raise Http404("Ten etap nie ma nadzoru.")
        session = services.session_for(stage, participant)
        return stage, config, session


def console_strings() -> dict[str, str]:
    """Napisy dla ``proctoring-console.js`` (``json_script`` – dane, nie kod)."""
    return {
        "checking": _("Sprawdzanie…"),
        "ok": _("działa"),
        "missing": _("nie działa"),
        "notRequired": _("niewymagane"),
        "unsupported": _(
            "Ta przeglądarka nie obsługuje nadzoru wideo. "
            "Użyj aktualnej przeglądarki Chrome, Edge, Firefox albo Safari."
        ),
        "cameraDenied": _(
            "Brak dostępu do kamery. Zezwól na kamerę w ustawieniach przeglądarki i spróbuj ponownie."
        ),
        "connecting": _("Łączenie z serwerem nadzoru…"),
        "live": _("Nadzór działa. Zostaw tę kartę otwartą do końca etapu."),
        "dropped": _("Połączenie nadzoru zostało przerwane. Nie zamykaj tej karty – trwa ponowne łączenie."),
        "reconnected": _("Połączenie nadzoru przywrócone."),
        "unavailable": _("Nie udało się połączyć z serwerem nadzoru."),
        "cameraUnavailable": _(
            "Kamera nie działa albo nie masz do niej dostępu. Jeśli nie możesz jej użyć, "
            "poproś o inną formę nadzoru (na dole strony)."
        ),
        "screenStopped": _("Udostępnianie ekranu zostało zatrzymane. Udostępnij ekran ponownie."),
        "shareScreen": _("Udostępnij ekran"),
        "message": _("Wiadomość od osoby nadzorującej"),
        "showRoom": _("Osoba nadzorująca prosi: pokaż kamerą pokój dookoła siebie."),
        "showId": _("Osoba nadzorująca prosi: pokaż do kamery dokument tożsamości."),
        "ack": _("Rozumiem"),
        "photoSaved": _("Zdjęcie zapisane."),
        "error": _("Coś poszło nie tak. Spróbuj ponownie."),
        "retry": _("Spróbuj ponownie"),
    }


@method_decorator(never_cache, name="dispatch")
class ProctoringConsoleView(_StudentMixin, View):
    """``GET /me/proctoring/<etap>/`` – konsola ucznia: informacja i zgoda, sprzęt, zdjęcie, start."""

    def get(self, request, stage_id: int):
        stage, config, session = self.load(request, stage_id)
        required, guardian = services.guardian_requirement(session.participant)
        opens, closes = windows.effective_window(stage, session.participant)
        can_start_from, _closes = windows.student_window(stage, session.participant)
        context = {
            "stage": stage,
            "config": config,
            "session": session,
            "step": services.step(session, config),
            "ready": services.is_ready(session, config),
            "consent": services.active_consent(session, config),
            "consent_statement": services.CONSENT_STATEMENT,
            "guardian_statement": services.GUARDIAN_STATEMENT,
            "consent_version": services.CONSENT_VERSION,
            "guardian_required": required,
            "guardian_missing": required and guardian is None,
            "opens_at": opens,
            "closes_at": closes,
            "start_from": can_start_from,
            "retention_days": settings.PROCTORING_RETENTION_DAYS,
            "alternative_form": AlternativeForm(),
            "alternative_requested": session.alternative_status == AlternativeStatus.REQUESTED,
            "alternative_rejected": session.alternative_status == AlternativeStatus.REJECTED,
            "configured": services.configured(),
            "allow_unproctored": config.on_unavailable == "allow",
            "id_photo_required": config.id_photo == IdPhoto.REQUIRED,
            "id_photo_enabled": config.id_photo != IdPhoto.OFF,
            "strings": console_strings(),
            "sdk_url": sdk_url(),
            "stage_url": reverse("web:me"),
        }
        return _form_page(TemplateResponse(request, CONSOLE_TEMPLATE, context))


class ProctoringConsentView(_StudentMixin, ThrottledFormMixin, View):
    """``POST /me/proctoring/<etap>/consent/`` – zgoda (``consent=1``) albo jej wycofanie (``withdraw=1``)."""

    throttle_scope = "proctoring_client"

    def post(self, request, stage_id: int):
        _stage_, config, session = self.load(request, stage_id)
        if request.POST.get("withdraw") == "1":
            services.withdraw_consent(session, user=request.user, request=request)
            messages.info(request, _("Zgoda na nadzór wycofana."))
        elif request.POST.get("consent") == "1":
            try:
                services.give_consent(
                    session,
                    user=request.user,
                    config=config,
                    guardian_statement=request.POST.get("guardian_statement") == "1",
                    request=request,
                )
            except DomainError as exc:
                messages.error(request, str(exc.detail))
        else:
            messages.error(request, _("Zaznacz pole zgody, żeby przejść dalej."))
        return redirect("web:proctoring-console", stage_id=stage_id)


class ProctoringAlternativeView(_StudentMixin, ThrottledFormMixin, View):
    """``POST /me/proctoring/<etap>/alternative/`` – „Nie mogę użyć kamery”."""

    throttle_scope = "proctoring_client"

    def post(self, request, stage_id: int):
        _stage_, _config, session = self.load(request, stage_id)
        form = AlternativeForm(request.POST)
        if form.is_valid():
            services.request_alternative(
                session,
                form.cleaned_data["reason"],
                form.cleaned_data["note"],
                user=request.user,
                request=request,
            )
            messages.success(request, _("Prośba wysłana. Organizator odpowie w tej konsoli."))
        else:
            messages.error(request, _("Wybierz powód z listy."))
        return redirect("web:proctoring-console", stage_id=stage_id)


class ProctoringStudentApiView(_StudentMixin, JsonThrottleMixin, View):
    """``POST /me/proctoring/<etap>/<czynność>/`` – kroki konsoli (JSON).

    ``check`` (wynik sprawdzenia), ``photo`` (JPEG w treści żądania), ``started`` (serwer sprawdza
    kamerę w LiveKit), ``unproctored`` („kontynuuj bez nadzoru”), ``event`` (zerwanie/powrót, puls),
    ``ack`` (potwierdzenie wiadomości). Token – osobny widok z osobnym limitem.
    """

    throttle_scope = "proctoring_client"
    ACTIONS = ("check", "photo", "started", "unproctored", "event", "ack")

    def post(self, request, stage_id: int, action: str):
        if action not in self.ACTIONS:
            raise Http404("Nieznana czynność.")
        _stage_, config, session = self.load(request, stage_id)
        user = request.user
        try:
            if action == "check":
                try:
                    payload = json.loads(request.body or b"{}")
                except ValueError:
                    payload = {}
                passed = services.record_check(
                    session, config, payload if isinstance(payload, dict) else {}, user=user, request=request
                )
                return _json({"ok": True, "passed": passed, "step": services.step(session, config)})
            if action == "photo":
                services.save_id_photo(
                    session,
                    config,
                    request.body[: services.ID_PHOTO_MAX_BYTES + 1],
                    user=user,
                    request=request,
                )
            elif action == "started":
                services.confirm_started(session, config, user=user, request=request)
            elif action == "unproctored":
                services.continue_unproctored(session, config, user=user, request=request)
            elif action == "event":
                services.client_event(
                    session,
                    request.POST.get("kind", ""),
                    heartbeat=request.POST.get("heartbeat") == "1",
                    reason=request.POST.get("reason", "")[:16],
                )
                if request.POST.get("kind") == "connect_failed":
                    # Konsola pokazuje „Kontynuuj bez nadzoru” wyłącznie wtedy, gdy serwer na to pozwoli.
                    allowed = config.on_unavailable == "allow" and (
                        services.unproctored_reason(session, config) is not None
                    )
                    return _json({"ok": True, "unproctored_allowed": allowed})
            elif action == "ack":
                services.ack_message(session, _pk_or_404(request.POST.get("id")))
        except DomainError as exc:
            return _json_error(exc)
        return _json(
            {"ok": True, "step": services.step(session, config), "ready": services.is_ready(session, config)}
        )


class ProctoringStudentTokenView(_StudentMixin, JsonThrottleMixin, View):
    """``POST /me/proctoring/<etap>/token/`` – token ucznia (JSON, ``no-store``)."""

    throttle_scope = "proctoring_token"

    def post(self, request, stage_id: int):
        _stage_, config, session = self.load(request, stage_id)
        try:
            data = services.student_token(session, config, user=request.user, request=request)
        except DomainError as exc:
            return _json_error(exc)
        return _json(data)


@method_decorator(never_cache, name="dispatch")
class ProctoringStudentMessagesView(_StudentMixin, View):
    """``GET /me/proctoring/<etap>/messages/?after=<id>`` – wiadomości odpytaniem (zapas kanału danych)."""

    def get(self, request, stage_id: int):
        _stage_, config, session = self.load(request, stage_id)
        after = request.GET.get("after", "0")
        rows = services.messages_for_student(session, int(after) if after.isdigit() else 0)
        return _json(
            {
                "messages": rows,
                "ready": services.is_ready(session, config),
                "alternative": session.alternative_status,
                "decision": session.alternative_decision if session.alternative_status else "",
            }
        )


# --- nadzorujący ------------------------------------------------------------------------------------


class _ProctorMixin(LoginRequiredMixin, FeatureMixin):
    def scope(self, request, stage_id: int) -> services.ProctorScope:
        stage = _stage(request, stage_id)
        scope = services.proctor_scope(request.user, stage)
        if scope is None:
            raise Http404("Nie ma takiego etapu z nadzorem.")
        return scope


@method_decorator(never_cache, name="dispatch")
class ProctorStagesView(LoginRequiredMixin, FeatureMixin, View):
    """``GET /proctoring/`` – etapy z nadzorem, na których ta osoba nadzoruje."""

    def get(self, request):
        stages = services.stages_for_proctor(request.user, request.competition)
        if not stages and not services.is_coordinator(request.user, request.competition):
            raise Http404("Brak etapów do nadzoru.")
        return TemplateResponse(request, PROCTOR_LIST_TEMPLATE, {"stages": stages})


def grid_strings() -> dict[str, str]:
    return {
        "connecting": _("Łączenie…"),
        "connected": _("Połączono z pokojem nadzoru."),
        "disconnected": _("Rozłączono z pokojem nadzoru. Odśwież stronę."),
        "failed": _("Nie udało się połączyć z pokojem nadzoru."),
        "page": _("Strona %(page)s z %(pages)s"),
        "empty": _("Brak uczniów w tej grupie."),
        "live": _("nadaje"),
        "waiting": _("czeka na start"),
        "dropped": _("strumień przerwany"),
        "stale": _("brak sygnału"),
        "unproctored": _("bez nadzoru"),
        "alternative": _("alternatywa"),
        "noConsent": _("brak zgody"),
        "message": _("Wiadomość"),
        "messagePrompt": _("Treść wiadomości do ucznia:"),
        "showRoom": _("Pokaż pokój"),
        "showId": _("Pokaż dokument"),
        "present": _("Obecny"),
        "absent": _("Nieobecny"),
        "incident": _("Incydent"),
        "screen": _("Ekran"),
        "hideScreen": _("Ukryj ekran"),
        "incidents": _("incydenty: %(count)s"),
        "sent": _("Wysłano."),
        "saved": _("Zapisano."),
        "error": _("Coś poszło nie tak. Spróbuj ponownie."),
        "startAudio": _("Włącz dźwięk"),
        "audio": _("Dźwięk"),
        "late": _("późny start: %(minutes)s min"),
        "reason_not_configured": _("serwer nadzoru nieskonfigurowany"),
        "reason_server_unreachable": _("serwer nadzoru niedostępny"),
        "reason_connect_failures": _("nieudane połączenia ucznia – serwer działał"),
        "report": _("Raport"),
    }


def incident_categories() -> list[tuple[str, str]]:
    """Kategorie incydentu w języku nadzorującego (w IQO – opiekun drużyny po angielsku).

    Etykiety modelu są polskie (panel koordynatora, eksport); siatka czyta je stąd.
    """
    return [
        (IncidentCategory.ABSENT, _("uczeń poza kadrem")),
        (IncidentCategory.OTHER_PERSON, _("inna osoba w kadrze")),
        (IncidentCategory.DEVICE, _("inne urządzenie")),
        (IncidentCategory.COMMUNICATION, _("rozmowa lub komunikacja")),
        (IncidentCategory.MATERIALS, _("niedozwolone materiały")),
        (IncidentCategory.SCREEN, _("ekran")),
        (IncidentCategory.TECHNICAL, _("problem techniczny")),
        (IncidentCategory.OTHER, _("inne")),
    ]


def incident_severities() -> list[tuple[str, str]]:
    return [
        (IncidentSeverity.INFO, _("informacja")),
        (IncidentSeverity.WARNING, _("ostrzeżenie")),
        (IncidentSeverity.SERIOUS, _("poważny")),
    ]


@method_decorator(never_cache, name="dispatch")
class ProctorGridView(_ProctorMixin, View):
    """``GET /proctoring/<etap>/`` – siatka uczniów w zakresie (lista i obraz po stronie JS)."""

    def get(self, request, stage_id: int):
        scope = self.scope(request, stage_id)
        groups = scope.allowed_groups()
        if scope.is_coordinator:
            groups = sorted(set(groups) | set(scope.sessions().values_list("group", flat=True).distinct()))
        context = {
            "stage": scope.stage,
            "scope": scope,
            "groups": [{"key": group, "label": services.group_label(group)} for group in groups],
            "page_sizes": services.PAGE_SIZES,
            "categories": incident_categories(),
            "severities": incident_severities(),
            "can_review": services.can_review(request.user, request.competition),
            "strings": grid_strings(),
            "sdk_url": sdk_url(),
            "configured": services.configured(),
            "microphone": scope.config.require_microphone,
        }
        return _form_page(TemplateResponse(request, GRID_TEMPLATE, context))


@method_decorator(never_cache, name="dispatch")
class ProctorRosterView(_ProctorMixin, View):
    """``GET /proctoring/<etap>/roster/?group=&page=&size=`` – strona listy w zakresie (JSON)."""

    def get(self, request, stage_id: int):
        scope = self.scope(request, stage_id)
        page = request.GET.get("page", "1")
        size = request.GET.get("size", "12")
        try:
            data = services.roster(
                scope,
                group=request.GET.get("group", "m")[:24],
                page=int(page) if page.isdigit() else 1,
                size=int(size) if size.isdigit() else 12,
            )
        except DomainError as exc:
            if exc.status_code == 404:
                raise Http404(str(exc.detail)) from exc
            return _json_error(exc)
        return _json(data)


class ProctorTokenView(_ProctorMixin, JsonThrottleMixin, View):
    """``POST /proctoring/<etap>/token/`` – token do pokoju **jednej** grupy z zakresu.

    Koordynator przełącza się między grupami (w IQO ~100 delegacji), więc ma osobny, wyższy kubełek
    (``proctoring_coordinator_token``) – nie zjada limitu komisji i opiekunów, a oni – jego.
    """

    throttle_scope = "proctoring_token"

    def dispatch(self, request, *args, **kwargs):
        competition = getattr(request, "competition", None)
        if request.user.is_authenticated and services.is_coordinator(request.user, competition):
            self.throttle_scope = "proctoring_coordinator_token"
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, stage_id: int):
        scope = self.scope(request, stage_id)
        try:
            data = services.proctor_token(scope, request.POST.get("group", "m")[:24], request=request)
        except DomainError as exc:
            if exc.status_code == 404:
                raise Http404(str(exc.detail)) from exc
            return _json_error(exc)
        return _json(data)


def _parse_time(raw: str):
    try:
        value = datetime.fromisoformat(raw)
    except TypeError, ValueError:
        return None
    return value if timezone.is_aware(value) else None


class ProctorActionView(_ProctorMixin, JsonThrottleMixin, View):
    """``POST /proctoring/<etap>/s/<sesja>/action/`` – czynność wobec ucznia z zakresu (JSON).

    ``action``: ``message`` (``body``), ``show_room``, ``show_id``, ``present``, ``absent``,
    ``incident`` (``category``, ``severity``, ``note``, opcjonalnie ``occurred_at`` ISO 8601).
    """

    throttle_scope = "proctoring_action"

    def post(self, request, stage_id: int, pk: int):
        scope = self.scope(request, stage_id)
        try:
            session = services.session_in_scope(scope, pk)
        except DomainError as exc:
            raise Http404(str(exc.detail)) from exc
        action = request.POST.get("action", "")
        try:
            if action in ("message", "show_room", "show_id"):
                kind = "text" if action == "message" else action
                services.send_message(scope, session, kind, request.POST.get("body", ""), request=request)
            elif action in ("present", "absent"):
                services.set_attendance(scope, session, action, request=request)
            elif action == "incident":
                services.create_incident(
                    scope,
                    session,
                    category=request.POST.get("category", ""),
                    severity=request.POST.get("severity", ""),
                    note=request.POST.get("note", ""),
                    occurred_at=_parse_time(request.POST.get("occurred_at", "")),
                    request=request,
                )
            else:
                return _json({"code": "UNKNOWN_ACTION", "detail": "Nieznana czynność."}, status=400)
        except DomainError as exc:
            return _json_error(exc)
        return _json({"ok": True})


class _ReviewMixin(LoginRequiredMixin, FeatureMixin):
    """Raport, eksport i nośniki: koordynator albo komisja odwoławcza – inaczej 404."""

    def stage_for_review(self, request, stage_id: int):
        stage = _stage(request, stage_id)
        if not services.can_review(request.user, request.competition):
            raise Http404("Nie ma takiego raportu.")
        if not ProctoringConfig.objects.filter(stage=stage).exists():
            raise Http404("Ten etap nie ma nadzoru.")
        return stage

    def session(self, stage, pk) -> ProctoringSession:
        return get_object_or_404(
            ProctoringSession.objects.select_related("participant__user", "stage__edition", "proctor__user"),
            stage=stage,
            pk=pk,
        )


@method_decorator(never_cache, name="dispatch")
class ProctoringReportView(_ReviewMixin, View):
    """``GET /proctoring/<etap>/s/<sesja>/`` – raport ucznia dla komisji (HTML do wydruku), z audytem."""

    def get(self, request, stage_id: int, pk: int):
        stage = self.stage_for_review(request, stage_id)
        session = self.session(stage, pk)
        services._audit(request.user, "proctoring.report_viewed", session, request=request)
        context = services.report_for(session)
        context.update(
            {
                "stage": stage,
                "label": services.student_label(session.participant),
                "late_minutes": services.late_start_minutes(session),
                "unproctored_label": services.unproctored_label(session),
            }
        )
        return _no_referrer(TemplateResponse(request, REPORT_TEMPLATE, context))


@method_decorator(never_cache, name="dispatch")
class ProctoringExportView(_ReviewMixin, View):
    """``GET /proctoring/<etap>/export.csv`` – incydenty etapu (CSV, średnik, UTF-8 z BOM)."""

    def get(self, request, stage_id: int):
        stage = self.stage_for_review(request, stage_id)
        services._audit(request.user, "proctoring.exported", stage, request=request)
        response = HttpResponse("﻿" + services.incidents_csv(stage), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="nadzor-incydenty-etap-{stage.pk}.csv"'
        response["Cache-Control"] = "no-store"
        return _no_referrer(response)


@method_decorator(never_cache, name="dispatch")
class ProctoringMediaView(_ReviewMixin, View):
    """``GET /proctoring/<etap>/media/<rodzaj>/<pk>/`` – 302 na adres podpisany na 15 min (audyt)."""

    def get(self, request, stage_id: int, kind: str, pk: int):
        stage = self.stage_for_review(request, stage_id)
        if kind == "rec":
            target = get_object_or_404(ProctoringRecording, session__stage=stage, pk=pk)
        elif kind == "photo":
            target = self.session(stage, pk)
        else:
            raise Http404("Nieznany rodzaj.")
        try:
            url = services.media_url(
                target, user=request.user, competition=request.competition, request=request
            )
        except DomainError as exc:
            raise Http404(str(exc.detail)) from exc
        return _no_referrer(HttpResponseRedirect(url))


# --- koordynator ------------------------------------------------------------------------------------


class _CoordinatorMixin(CoordinatorRequiredMixin, FeatureMixin):
    pass


@method_decorator(never_cache, name="dispatch")
class CoordinatorProctoringView(_CoordinatorMixin, View):
    """``GET /coordinator/proctoring/`` – etapy online bieżącej edycji i stan nadzoru."""

    def get(self, request):
        from apps.competitions.models import Stage
        from apps.competitions.services import current_edition

        edition = current_edition(request.competition)
        stages = (
            list(
                Stage.objects.for_competition(request.competition)
                .filter(edition=edition)
                .select_related("proctoring")
                .order_by("opens_at", "id")
            )
            if edition
            else []
        )
        rows = [
            {
                "stage": stage,
                "config": getattr(stage, "proctoring", None),
                "proctorable": services.proctorable(stage),
            }
            for stage in stages
        ]
        context = {"rows": rows, "configured": services.configured()}
        return TemplateResponse(request, COORDINATOR_LIST_TEMPLATE, context)


@method_decorator(never_cache, name="dispatch")
class CoordinatorProctoringStageView(_CoordinatorMixin, ThrottledFormMixin, View):
    """``GET|POST /coordinator/proctoring/<etap>/`` – ustawienia, przydziały, uczniowie, decyzje.

    ``action``: ``config``, ``assign`` (``user``, ``kind``), ``unassign`` (``assignment``),
    ``distribute``, ``reassign`` (``session``, ``assignment``), ``alternative`` (``session``,
    ``decision`` = approve/reject, ``note``), ``hold`` (``session``, ``reason``).
    """

    throttle_scope = "proctoring_action"

    def render(self, request, stage, form=None, status=200):
        config = ProctoringConfig.objects.filter(stage=stage).first()
        sessions = list(
            ProctoringSession.objects.filter(stage=stage)
            .select_related("participant__user", "proctor__user")
            .order_by("group", "participant__user__last_name", "pk")
        )
        group_labels: dict[str, str] = {}
        for session in sessions:
            session.label = services.student_label(session.participant)
            if session.group not in group_labels:
                group_labels[session.group] = services.group_label(session.group)
            session.group_label = group_labels[session.group]
        context = {
            "stage": stage,
            "config": config,
            "form": form or ProctoringConfigForm(instance=config),
            "proctorable": services.proctorable(stage),
            "assignments": list(ProctorAssignment.objects.filter(stage=stage).select_related("user")),
            "candidates": services.assignment_candidates(stage),
            "kinds": ProctorKind.choices,
            "sessions": sessions,
            "alternatives": [s for s in sessions if s.alternative_status == AlternativeStatus.REQUESTED],
            "summary": services.stage_summary(stage, config) if config else {},
            "configured": services.configured(),
        }
        return _form_page(TemplateResponse(request, COORDINATOR_STAGE_TEMPLATE, context, status=status))

    def get(self, request, stage_id: int):
        return self.render(request, _stage(request, stage_id))

    def post(self, request, stage_id: int):
        from apps.accounts.models import User

        stage = _stage(request, stage_id)
        action = request.POST.get("action", "")
        user = request.user
        back = redirect("web:coordinator-proctoring-stage", stage_id=stage.pk)
        try:
            if action == "config":
                # Formularz bez ``instance`` – zmiany liczy i zapisuje serwis (jak w WEB-01).
                form = ProctoringConfigForm(request.POST)
                if not form.is_valid():
                    return self.render(request, stage, form, status=400)
                services.save_config(stage, user, form.service_data(), request=request)
                messages.success(request, "Ustawienia nadzoru zapisane.")
            elif action == "assign":
                target = get_object_or_404(User, pk=_pk_or_404(request.POST.get("user")))
                services.add_assignment(stage, user, target, request.POST.get("kind", ""), request=request)
                messages.success(request, "Nadzorujący dodany.")
            elif action == "unassign":
                services.remove_assignment(stage, user, request.POST.get("assignment", ""), request=request)
                messages.success(request, "Przydział usunięty.")
            elif action == "distribute":
                result = services.distribute(stage, user, request=request)
                messages.success(request, f"Rozdzielono uczniów: {result['assigned']}.")
            elif action == "reassign":
                services.reassign(
                    stage,
                    request.POST.get("session", ""),
                    request.POST.get("assignment", ""),
                    user,
                    request=request,
                )
                messages.success(request, "Nadzorujący ucznia zmieniony.")
            elif action == "alternative":
                session = get_object_or_404(
                    ProctoringSession, stage=stage, pk=_pk_or_404(request.POST.get("session"))
                )
                services.decide_alternative(
                    session,
                    approve=request.POST.get("decision") == "approve",
                    decision=request.POST.get("note", ""),
                    actor=user,
                    request=request,
                )
                messages.success(request, "Decyzja zapisana – uczeń zobaczy ją w konsoli.")
            elif action == "hold":
                session = get_object_or_404(
                    ProctoringSession, stage=stage, pk=_pk_or_404(request.POST.get("session"))
                )
                services.set_hold(session, user, request.POST.get("reason", ""), request=request)
                messages.success(request, "Zapisano.")
            else:
                raise Http404("Nieznana czynność.")
        except DomainError as exc:
            if exc.status_code == 404:
                raise Http404(str(exc.detail)) from exc
            messages.error(request, str(exc.detail))
        return back
