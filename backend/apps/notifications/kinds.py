"""Rodzaje powiadomień: kod w bazie, zdanie dla człowieka i ikona.

W bazie leży **kod** rodzaju i kilka parametrów (tytuł ogłoszenia, nazwa etapu), a zdanie składa się
w chwili wyświetlenia, w języku oglądającego – tak jak komunikaty błędów notatników
(``apps.notebooks.services.ERROR_MESSAGES``). Powiadomienie zapisane w czasie, gdy koordynator
pracował po polsku, uczestnik IQO przeczyta więc po angielsku.

Parametry nie niosą treści rozmów ani wpisów: powiadomienie mówi **że** i **gdzie**, a nie **co** –
ta sama zasada, co listy z forum i czatu (``apps.forum.notifications``, ``apps.chat.notifications``).
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import models
from django.utils.translation import gettext, gettext_noop
from django.utils.translation import gettext_lazy as _


class NotificationKind(models.TextChoices):
    CHAT_ORGANIZER = "chat_organizer", _("Wiadomość od organizatora")
    CHAT_PEER = "chat_peer", _("Wiadomość od uczestnika")
    ANNOUNCEMENT = "announcement", _("Ogłoszenie organizatora")
    STUDENT_STATUS_ACCEPTED = "student_status_accepted", _("Zaświadczenie zaakceptowane")
    STUDENT_STATUS_REJECTED = "student_status_rejected", _("Zaświadczenie odrzucone")
    FORUM_REPLY = "forum_reply", _("Odpowiedź na forum")
    FORUM_DECISION = "forum_decision", _("Decyzja moderatora forum")
    RESULTS_PUBLISHED = "results_published", _("Ogłoszone wyniki")
    APPEAL_DECIDED = "appeal_decided", _("Decyzja w sprawie reklamacji")
    # Panel komisji (UX-PANEL-03 D11): odbiorcami są wyłącznie członkowie komisji tego konkursu.
    REVIEW_ASSIGNED = "review_assigned", _("Nowe prace do oceny")
    REVIEW_TIEBREAK = "review_tiebreak", _("Praca w rundzie rozjemczej")
    REVIEW_DEADLINE = "review_deadline", _("Termin oceniania")
    APPEAL_PENDING = "appeal_pending", _("Reklamacja do rozpatrzenia")


@dataclass(frozen=True)
class KindSpec:
    """Zdanie (``gettext_noop`` – tłumaczone przy wyświetleniu) i ikona przy pozycji listy."""

    text: str
    icon: str


KINDS: dict[str, KindSpec] = {
    NotificationKind.CHAT_ORGANIZER: KindSpec(gettext_noop("Nowa wiadomość od organizatora"), "chat"),
    NotificationKind.CHAT_PEER: KindSpec(
        gettext_noop("Nowa wiadomość w rozmowie z innym uczestnikiem"), "chat"
    ),
    NotificationKind.ANNOUNCEMENT: KindSpec(
        gettext_noop("Nowe ogłoszenie organizatora: %(title)s"), "megaphone"
    ),
    NotificationKind.STUDENT_STATUS_ACCEPTED: KindSpec(
        gettext_noop("Zaświadczenie o statusie ucznia zostało zaakceptowane"), "check"
    ),
    NotificationKind.STUDENT_STATUS_REJECTED: KindSpec(
        gettext_noop("Zaświadczenie o statusie ucznia zostało odrzucone – sprawdź powód"), "alert"
    ),
    NotificationKind.FORUM_REPLY: KindSpec(
        gettext_noop("Nowa odpowiedź w obserwowanym wątku na forum"), "forum"
    ),
    NotificationKind.FORUM_DECISION: KindSpec(
        gettext_noop("Moderator forum podjął decyzję w sprawie Twojego wpisu"), "forum"
    ),
    NotificationKind.RESULTS_PUBLISHED: KindSpec(gettext_noop("Ogłoszono wyniki: %(stage)s"), "trophy"),
    NotificationKind.APPEAL_DECIDED: KindSpec(gettext_noop("Komisja rozpatrzyła Twoją reklamację"), "check"),
    # Komisja (UX-PANEL-03 D12): nazwa etapu albo liczba prac – nigdy kod publiczny ani punkty.
    NotificationKind.REVIEW_ASSIGNED: KindSpec(gettext_noop("Nowe prace do oceny: %(stage)s"), "task"),
    NotificationKind.REVIEW_TIEBREAK: KindSpec(
        gettext_noop("Praca czeka na Twoją ocenę w rundzie rozjemczej: %(stage)s"), "scale"
    ),
    NotificationKind.REVIEW_DEADLINE: KindSpec(
        gettext_noop("Mija termin oceniania – prace po terminie albo tuż przed nim: %(count)s"), "clock"
    ),
    NotificationKind.APPEAL_PENDING: KindSpec(
        gettext_noop("Nowa reklamacja do rozpatrzenia: %(stage)s"), "shield"
    ),
}

#: Zdanie zastępcze dla kodu spoza katalogu (wiersz z wersji, która miała rodzaj usunięty później).
FALLBACK = KindSpec(gettext_noop("Nowe powiadomienie"), "bell")


def spec_for(kind: str) -> KindSpec:
    return KINDS.get(kind, FALLBACK)


def text_for(kind: str, params: dict | None) -> str:
    """Zdanie w aktywnym języku. Brakujący parametr nie wywraca strony – zostaje zdanie bez niego."""
    template = gettext(spec_for(kind).text)
    try:
        return template % (params or {})
    except KeyError, TypeError, ValueError:
        return gettext(FALLBACK.text)
