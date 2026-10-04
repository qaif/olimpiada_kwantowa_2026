"""List „hasło do konta zostało zmienione” (AUTH-01b § 4).

Po co ten list, skoro zmianę robi sam właściciel konta: to jest jedyny sygnał, który dostaje
właściciel skrzynki, jeśli zmiany **nie** robił – ktoś znał jego hasło i siedział w jego sesji.
Dlatego treść kończy się drogą ratunkową (link do „Nie pamiętasz hasła?”, który działa, bo adres
konta się nie zmienił), a nie podziękowaniem.

Czego w liście nie ma i mieć nie może: hasła (ani starego, ani nowego), adresu IP i nazwy
przeglądarki. Skrzynka bywa współdzielona (rodzic, szkolny sekretariat), a adres IP jest daną
osobową, której odbiorca i tak nie umie odczytać.

Język i adres bezwzględny bierzemy z tych samych funkcji, co listy aktywacji i zmiany adresu
(``apps.accounts.activation``). Kolejkowanie jest **własne** i odporne na awarię brokera (przegląd
M1): ``queue_mail`` woła ``send_mail_task.delay`` w ``on_commit`` bez osłony, więc niedostępny Redis
zamieniłby udaną zmianę hasła w błąd 500. List bezpieczeństwa nie jest warunkiem zmiany – jego brak
logujemy po kluczu konta, tak jak list resetu (``apps.accounts.password_reset``).
"""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.accounts.activation import absolute_url, mail_competition, recipient_language, signature_lines
from apps.tenancy import branding

logger = logging.getLogger(__name__)

#: Temat listu. Leniwy, bo moduł ładuje się przed aktywacją jakiegokolwiek języka; brzmienie jest
#: zamrożone w ``apps/tenancy/tests/test_invariants.py`` jak każdy inny temat.
PASSWORD_CHANGED_SUBJECT = gettext_lazy("Hasło do konta zostało zmienione – Olimpiada Kwantowa")
#: Ten sam temat z marką konkursu (flaga ``competition_branding_in_mail``) – IQO dostaje swoją nazwę.
PASSWORD_CHANGED_SUBJECT_TEMPLATE = gettext_lazy("Hasło do konta zostało zmienione – %(competition)s")


def notice_timezone(competition=None, request=None) -> str:
    """Strefa, w której list podaje godzinę zmiany (przegląd L8).

    Kolejność: strefa uczestnika z okien czasowych (TZ-01 – ``participant_timezone``: własna albo
    kraju delegacji, tylko gdy konkurs ma tę funkcję) → strefa konkursu (``Competition.time_zone``)
    → strefa instalacji. List IQO nie może więc mówić „CEST” uczniowi z Tokio.
    """
    candidates = []
    if request is not None:
        from apps.time_windows.middleware import participant_timezone

        candidates.append(participant_timezone(request))
    candidates += [getattr(competition, "time_zone", None), settings.TIME_ZONE]
    for name in candidates:
        if not name:
            continue
        try:
            ZoneInfo(name)
        except ZoneInfoNotFoundError, ValueError:
            continue
        return name
    return "UTC"


def _when(moment, tz_name: str) -> str:
    """Chwila zmiany w zapisie bieżącego języka, ze strefą nazwaną jednoznacznie.

    Nazwa strefy IANA i przesunięcie (``Europe/Warsaw, UTC+02:00``), a nie skrót typu „CEST”:
    skróty są niejednoznaczne i zależą od czasu letniego, a list czyta się później i gdzie indziej.
    """
    from apps.time_windows.zones import utc_offset_label

    local = timezone.localtime(moment, ZoneInfo(tz_name))
    return f"{formats.date_format(local, 'DATETIME_FORMAT')} ({tz_name}, {utc_offset_label(moment, tz_name)})"


def password_changed_message(
    reset_link: str, *, changed_at, competition=None, tz_name: str | None = None, editor: bool = False
) -> str:
    """Treść listu. Kolejność zdań: co się stało → co to znaczy → co zrobić, jeśli to nie Ty."""
    tz_name = tz_name or notice_timezone(competition)
    lines = [
        _("Hasło do Twojego konta w serwisie %(competition_genitive)s zostało zmienione (%(when)s).")
        % {**branding.brand_names(competition), "when": _when(changed_at, tz_name)},
        "",
        _(
            "Pozostałe urządzenia, na których było otwarte to konto, zostały wylogowane. "
            "Na urządzeniu, na którym zmieniono hasło, sesja trwa dalej."
        ),
    ]
    if editor:
        lines.append(djcms_note())
    lines += [
        "",
        _(
            "Jeśli to nie Ty zmieniłeś hasło, natychmiast ustaw nowe przez formularz "
            "„Nie pamiętasz hasła?” i skontaktuj się z organizatorem:"
        ),
        "",
        reset_link,
        "",
        *signature_lines(competition),
    ]
    return "\n".join(lines)


def djcms_note() -> str:
    """Zdanie o sesji edytora django CMS (przegląd L2) – tylko dla kont, które mogą do niego wejść."""
    return _(
        "Otwarta na innym urządzeniu sesja edytora django CMS nie jest kończona od razu – wygasa sama "
        "po kilku godzinach. Jeśli podejrzewasz przejęcie konta, poproś organizatora o jej zamknięcie."
    )


def is_djcms_editor(user) -> bool:
    """Czy konto może mieć sesję w django CMS (SSO z ``/cms/``) – sesji tej nie kończy zmiana hasła.

    Tanio: SSO włączone i dostęp do ``/cms/``. Kto nie wchodzi do ``/cms/``, nie ma czego wygaszać.
    """
    from apps.cms.djcms_sso import sso_enabled

    return sso_enabled() and user.has_perm("wagtailadmin.access_admin")


def send_password_changed_notice(user, *, request=None, competition=None, changed_at=None) -> None:
    """Kolejkuje list do właściciela konta po commicie; awaria brokera jest logowana, nie podnoszona.

    Link do resetu liczymy z **żądania**: host, schemat i prefiks ścieżki konkursu są wtedy tymi,
    pod którymi człowiek właśnie był zalogowany (``iqo-official.org``, ``/<prefiks>/…``). Bez
    żądania (przyszłe API, komenda) – z konkursu, tą samą drogą co inne listy (``absolute_url``).
    """
    from apps.core.tasks import mail_from

    competition = mail_competition(competition)
    changed_at = changed_at or timezone.now()
    tz_name = notice_timezone(competition, request)
    with recipient_language(user, competition, request=request):
        link = absolute_url(reverse("web:password-reset"), request, competition)
        subject = str(
            branding.subject(PASSWORD_CHANGED_SUBJECT_TEMPLATE, PASSWORD_CHANGED_SUBJECT, competition)
        )
        message = password_changed_message(
            link,
            changed_at=changed_at,
            competition=competition,
            tz_name=tz_name,
            editor=is_djcms_editor(user),
        )
    # Nadawca i adresat rozstrzygnięte **teraz**: kontekst konkursu i obiekt konta po commicie mogą
    # być już inne (patrz ``apps.accounts.activation.queue_mail``).
    from_email = mail_from(competition)
    recipient, user_pk = user.email, user.pk

    def _enqueue() -> None:
        from apps.core.tasks import send_mail_task

        try:
            send_mail_task.delay(subject, message, [recipient], from_email)
        except Exception:  # noqa: BLE001 - list bezpieczeństwa nie może wywrócić udanej zmiany
            logger.exception("Nie udało się zakolejkować listu o zmianie hasła dla konta %s.", user_pk)

    transaction.on_commit(_enqueue)
