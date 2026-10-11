"""Listy o webinarach: zaproszenie (ręcznie, raz) i przypomnienie (beat, raz).

Co list niesie, a czego **nie**:

- tytuł, termin w strefie konkursu, czas trwania, opis od organizatora i odnośnik do panelu
  (``/webinars/``) – tam jest przycisk „Dołącz”,
- **nie** niesie tokenu ani adresu serwera LiveKit. Token powstaje przy wejściu po zalogowaniu
  (``apps.webinars.services.join_token``); list przekazany dalej nie otwiera nikomu pokoju,
- odnośnik do wyłączenia listów o webinarach – ten sam ekran.

Odbiorców liczy ``services.audience_recipients`` (ta sama reguła, co panel). Każdy list w języku
odbiorcy (``language_for``), temat przez ``branding.subject`` – jak pozostałe listy platformy.
"""

from __future__ import annotations

import logging

from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from .models import Webinar

logger = logging.getLogger(__name__)

INVITE = "invite"
REMINDER = "reminder"
KINDS = (INVITE, REMINDER)

#: Tematy. ``%(title)s`` podstawiamy sami – przy wyłączonej marce konkursu ``branding.subject``
#: oddaje odwrót bez podstawień, więc odwrót składamy już z tytułem.
INVITE_SUBJECT = gettext_lazy("Zaproszenie na webinar: %(title)s – Olimpiada Kwantowa")
INVITE_SUBJECT_TEMPLATE = gettext_lazy("Zaproszenie na webinar: %(title)s – %(competition)s")
REMINDER_SUBJECT = gettext_lazy("Przypomnienie o webinarze: %(title)s – Olimpiada Kwantowa")
REMINDER_SUBJECT_TEMPLATE = gettext_lazy("Przypomnienie o webinarze: %(title)s – %(competition)s")

#: Kotwica sekcji ustawień listów na ekranie webinarów.
SETTINGS_ANCHOR = "#powiadomienia"


def _subject(kind: str, webinar: Webinar) -> str:
    from apps.tenancy import branding

    template, fallback = (
        (INVITE_SUBJECT_TEMPLATE, INVITE_SUBJECT)
        if kind == INVITE
        else (REMINDER_SUBJECT_TEMPLATE, REMINDER_SUBJECT)
    )
    return branding.subject(
        template, str(fallback) % {"title": webinar.title}, webinar.competition, title=webinar.title
    )


def message(kind: str, webinar: Webinar) -> str:
    """Treść listu w **aktywnym** języku (wołający ustawia język odbiorcy)."""
    from apps.accounts.activation import absolute_url, signature_lines

    from .services import competition_zone

    competition = webinar.competition
    zone = competition_zone(competition)
    when = timezone.localtime(webinar.starts_at, zone).strftime("%d.%m.%Y, %H:%M")
    panel = absolute_url(reverse("web:webinars"), competition=competition)
    if kind == INVITE:
        first = _("Zapraszamy na webinar „%(title)s”.") % {"title": webinar.title}
    else:
        first = _("Przypominamy: webinar „%(title)s” zaczyna się wkrótce.") % {"title": webinar.title}
    lines = [
        first,
        "",
        _("Termin: %(when)s (strefa %(zone)s), czas trwania: %(minutes)s min.")
        % {"when": when, "zone": zone.key, "minutes": webinar.duration_minutes},
    ]
    if webinar.description:
        lines += ["", webinar.description]
    lines += [
        "",
        _("Dołączysz po zalogowaniu, przyciskiem „Dołącz” na stronie: %(link)s") % {"link": panel},
        _("Przycisk działa od kilkunastu minut przed początkiem webinaru."),
        "",
        _("Nie chcesz dostawać listów o webinarach? Wyłącz je tutaj: %(link)s")
        % {"link": f"{panel}{SETTINGS_ANCHOR}"},
        "",
        *signature_lines(competition),
    ]
    return "\n".join(lines)


def send(webinar: Webinar, kind: str) -> int:
    """Kolejkuje list do każdego odbiorcy webinaru. Zwraca liczbę listów."""
    from apps.accounts.activation import queue_mail
    from apps.accounts.preferences import language_for

    from .services import audience_recipients

    if kind not in KINDS:
        raise ValueError(kind)
    competition = webinar.competition
    sent = 0
    for user in audience_recipients(webinar).iterator(chunk_size=500):
        with language_for(user, competition):
            subject = _subject(kind, webinar)
            body = message(kind, webinar)
        queue_mail(subject, body, user.email, competition=competition, essential=False)
        sent += 1
    logger.info("Webinar %s: %s – %s listów.", webinar.pk, kind, sent)
    return sent
