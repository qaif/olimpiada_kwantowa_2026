"""Pokoje rozmów etapu w LiveKit (STAGE-LK-01): strona pokoju, token, polecenia moderatora.

Do pokoju LiveKit prowadzą **te same** widoki wejścia, co do Jitsi (``apps.web.views.video``:
``interview-join``, ``…-slot-join``, próby sprzętu) – po sprawdzeniu roli i okna przekierowują tu,
na stronę pokoju na platformie. Ta strona nie niesie poświadczeń: token pobiera JS (``webinar-room.js``,
ten sam interfejs, co webinary) osobnym POST-em, a widok tokenu **ponownie** pyta tę samą regułę
(``apps.competitions.room_access``) – kto wejdzie z pominięciem widoku wejścia, dostanie dokładnie to
samo rozstrzygnięcie albo odmowę.

Bramki ról są te same, co Jitsi, bo widoki dziedziczą mixiny widoków Jitsi: uczestnik –
``_ParticipantInterviewMixin`` (własny, niezdyskwalifikowany zapis), koordynator – ``_SlotJoinMixin``,
komisja – ``_CommitteeSlotMixin``. Pokój spoza LiveKit (Jitsi, link) – 404: ma własną drogę.
"""

from __future__ import annotations

from django.http import Http404, JsonResponse
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import View

from apps.competitions import room_access
from apps.core.api import DomainError
from apps.proctoring import stage_rooms
from apps.web.views.video import (
    _audit_join,
    _CommitteeSlotMixin,
    _form_page,
    _no_referrer,
    _ParticipantInterviewMixin,
    _SlotJoinMixin,
    _when,
)
from apps.web.views.webinars import room_strings, sdk_url

ROOM_TEMPLATE = "web/stage_room.html"
KINDS = (room_access.INTERVIEW, room_access.PRECHECK)


def _json(data: dict, status: int = 200) -> JsonResponse:
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "no-store"
    return _no_referrer(response)


def _error(exc: DomainError) -> JsonResponse:
    return _json({"code": exc.machine_code, "detail": str(exc.detail)}, status=exc.status_code)


def _closed(exc) -> DomainError:
    if isinstance(exc, room_access.RoomNotYet):
        return DomainError(
            _("Pokój rozmowy otworzy się %(when)s (czas polski) – wróć wtedy do panelu.")
            % {"when": _when(exc.opens_at)},
            "ROOM_NOT_YET",
            409,
        )
    return DomainError(_("Czas tej rozmowy minął – pokój jest już zamknięty."), "ROOM_OVER", 409)


def _livekit_only(url: str) -> None:
    if room_access.provider_of(url) != "livekit":
        raise Http404("Ten pokój nie jest pokojem LiveKit.")


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise Http404("Nieznany rodzaj pokoju.")
    return kind


def _page(request, *, title: str, moderator: bool, token_url: str, control_url: str, back_url: str):
    context = {
        "room_title": title,
        "role": "presenter" if moderator else "viewer",
        "token_url": token_url,
        "control_url": control_url,
        "back_url": back_url,
        "can_record": False,
        "strings": room_strings(),
        "sdk_url": sdk_url(),
    }
    return _form_page(TemplateResponse(request, ROOM_TEMPLATE, context))


def _title(stage, kind: str) -> str:
    if kind == room_access.PRECHECK:
        return _("Próba sprzętu – %(stage)s") % {"stage": stage.display_name}
    return _("Rozmowa – %(stage)s") % {"stage": stage.display_name}


# --- uczestnik -----------------------------------------------------------------------------------


class _ParticipantRoomMixin(_ParticipantInterviewMixin):
    throttle_methods = ("GET", "POST")


class InterviewRoomView(_ParticipantRoomMixin, View):
    """``GET /me/stages/<id>/interview/room/<rodzaj>/`` – strona pokoju LiveKit uczestnika."""

    def get(self, request, stage_id: int, kind: str):
        stage, _booking, url = self.booking(request, stage_id)
        _livekit_only(url)
        kind = _kind(kind)
        return _page(
            request,
            title=_title(stage, kind),
            moderator=False,
            token_url=reverse("web:interview-room-token", args=[stage.pk, kind]),
            control_url="",
            back_url=f"{reverse('web:me')}#rozmowa",
        )


class InterviewRoomTokenView(_ParticipantRoomMixin, View):
    """``POST /me/stages/<id>/interview/room/<rodzaj>/token/`` – token uczestnika (ta sama reguła)."""

    http_method_names = ["post"]

    def post(self, request, stage_id: int, kind: str):
        stage, booking, url = self.booking(request, stage_id)
        _livekit_only(url)
        kind = _kind(kind)
        try:
            room_pass = room_access.participant_pass(booking, url, request.user, kind=kind)
        except (room_access.RoomNotYet, room_access.RoomOver) as exc:
            return _error(_closed(exc))
        try:
            if kind == room_access.INTERVIEW:
                stage_rooms.proctoring_check(stage, self.participant)
            data = stage_rooms.access_token(room_pass, request.user)
        except DomainError as exc:
            return _error(exc)
        _audit_join(request.user, booking.entry, role="participant", kind=kind, request=request)
        return _json(data)


# --- koordynator i komisja ------------------------------------------------------------------------


class _StaffRoomMixin:
    """Strona, token i polecenia pokoju terminu – wspólne dla koordynatora i komisji."""

    throttle_methods = ("GET", "POST")
    token_url_name = ""
    control_url_name = ""

    def staff_pass(self, request, pk: int, kind: str):
        slot, url = self.slot(request, pk)
        _livekit_only(url)
        return slot, room_access.staff_pass(slot, url, request.user, role=self.join_role, kind=_kind(kind))

    def get(self, request, pk: int, kind: str):
        slot, url = self.slot(request, pk)
        _livekit_only(url)
        kind = _kind(kind)
        moderator = kind == room_access.INTERVIEW
        return _page(
            request,
            title=_title(slot.stage, kind),
            moderator=moderator,
            token_url=reverse(self.token_url_name, args=[slot.pk, kind]),
            control_url=reverse(self.control_url_name, args=[slot.pk]) if moderator else "",
            back_url=self.back(slot)["Location"],
        )


class _StaffTokenMixin(_StaffRoomMixin):
    http_method_names = ["post"]

    def post(self, request, pk: int, kind: str):
        try:
            slot, room_pass = self.staff_pass(request, pk, kind)
        except (room_access.RoomNotYet, room_access.RoomOver) as exc:
            return _error(_closed(exc))
        try:
            data = stage_rooms.access_token(room_pass, request.user)
        except DomainError as exc:
            return _error(exc)
        _audit_join(request.user, slot, role=self.join_role, kind=room_pass.kind, request=request)
        return _json(data)


class _StaffControlMixin(_StaffRoomMixin):
    """Polecenia moderatora: tylko z biletem rozmowy (moderator) w oknie terminu – jak w Jitsi."""

    http_method_names = ["post"]

    def post(self, request, pk: int):
        from apps.core.models import audit

        try:
            slot, room_pass = self.staff_pass(request, pk, room_access.INTERVIEW)
        except (room_access.RoomNotYet, room_access.RoomOver) as exc:
            return _error(_closed(exc))
        action = request.POST.get("action", "")
        try:
            stage_rooms.control(room_pass, action, request.POST.get("identity", ""))
        except DomainError as exc:
            return _error(exc)
        audit(
            request.user,
            "interview.room_control",
            slot,
            {"action": action, "role": self.join_role},
            request=request,
        )
        return _json({"ok": True})


class CoordinatorSlotRoomView(_StaffRoomMixin, _SlotJoinMixin, View):
    """``GET /coordinator/interview-slots/<id>/room/<rodzaj>/``."""

    token_url_name = "web:coordinator-interview-slot-room-token"
    control_url_name = "web:coordinator-interview-slot-room-control"


class CoordinatorSlotRoomTokenView(_StaffTokenMixin, _SlotJoinMixin, View):
    """``POST /coordinator/interview-slots/<id>/room/<rodzaj>/token/``."""


class CoordinatorSlotRoomControlView(_StaffControlMixin, _SlotJoinMixin, View):
    """``POST /coordinator/interview-slots/<id>/room/control/``."""


class CommitteeSlotRoomView(_StaffRoomMixin, _CommitteeSlotMixin, View):
    """``GET /review/interview-slots/<id>/room/<rodzaj>/``."""

    token_url_name = "web:committee-interview-slot-room-token"
    control_url_name = "web:committee-interview-slot-room-control"


class CommitteeSlotRoomTokenView(_StaffTokenMixin, _CommitteeSlotMixin, View):
    """``POST /review/interview-slots/<id>/room/<rodzaj>/token/``."""


class CommitteeSlotRoomControlView(_StaffControlMixin, _CommitteeSlotMixin, View):
    """``POST /review/interview-slots/<id>/room/control/``."""
