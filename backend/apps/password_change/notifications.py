"""List „hasło do konta zostało zmienione” (AUTH-01b § 4).

Po co ten list, skoro zmianę robi sam właściciel konta: to jest jedyny sygnał, który dostaje
właściciel skrzynki, jeśli zmiany **nie** robił – ktoś znał jego hasło i siedział w jego sesji.
Dlatego treść kończy się drogą ratunkową (link do „Nie pamiętasz hasła?”, który działa, bo adres
konta się nie zmienił), a nie podziękowaniem.

Czego w liście nie ma i mieć nie może: hasła (ani starego, ani nowego), adresu IP i nazwy
przeglądarki. Skrzynka bywa współdzielona (rodzic, szkolny sekretariat), a adres IP jest daną
osobową, której odbiorca i tak nie umie odczytać.

Kopertę (nadawca, kolejka, wysyłka po commicie), język i adres bezwzględny bierzemy z tych samych
funkcji, co listy aktywacji i zmiany adresu (``apps.accounts.activation``) – list z tej aplikacji ma
wyglądać i zachowywać się dokładnie tak, jak tamte.
"""

from __future__ import annotations

from django.urls import reverse
from django.utils import formats, timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.accounts.activation import (
    absolute_url,
    mail_competition,
    queue_mail,
    recipient_language,
    signature_lines,
)
from apps.tenancy import branding

#: Temat listu. Leniwy, bo moduł ładuje się przed aktywacją jakiegokolwiek języka; brzmienie jest
#: zamrożone w ``apps/tenancy/tests/test_invariants.py`` jak każdy inny temat.
PASSWORD_CHANGED_SUBJECT = gettext_lazy("Hasło do konta zostało zmienione – Olimpiada Kwantowa")
#: Ten sam temat z marką konkursu (flaga ``competition_branding_in_mail``) – IQO dostaje swoją nazwę.
PASSWORD_CHANGED_SUBJECT_TEMPLATE = gettext_lazy("Hasło do konta zostało zmienione – %(competition)s")


def _when(moment) -> str:
    """Chwila zmiany w czytelnym zapisie bieżącego języka, ze strefą (list czyta się później)."""
    local = timezone.localtime(moment)
    return f"{formats.date_format(local, 'DATETIME_FORMAT')} ({local.tzname()})"


def password_changed_message(reset_link: str, *, changed_at, competition=None) -> str:
    """Treść listu. Kolejność zdań: co się stało → co to znaczy → co zrobić, jeśli to nie Ty."""
    return "\n".join(
        [
            _("Hasło do Twojego konta w serwisie %(competition_genitive)s zostało zmienione (%(when)s).")
            % {**branding.brand_names(competition), "when": _when(changed_at)},
            "",
            _(
                "Pozostałe urządzenia, na których było otwarte to konto, zostały wylogowane. "
                "Na urządzeniu, na którym zmieniono hasło, sesja trwa dalej."
            ),
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
    )


def send_password_changed_notice(user, *, request=None, competition=None, changed_at=None) -> None:
    """Kolejkuje list do właściciela konta (po commicie – ``queue_mail``).

    Link do resetu liczymy z **żądania**: host, schemat i prefiks ścieżki konkursu są wtedy tymi,
    pod którymi człowiek właśnie był zalogowany (``iqo-official.org``, ``/<prefiks>/…``). Bez
    żądania (przyszłe API, komenda) – z konkursu, tą samą drogą co inne listy (``absolute_url``).
    """
    competition = mail_competition(competition)
    changed_at = changed_at or timezone.now()
    with recipient_language(user, competition, request=request):
        link = absolute_url(reverse("web:password-reset"), request, competition)
        subject = branding.subject(PASSWORD_CHANGED_SUBJECT_TEMPLATE, PASSWORD_CHANGED_SUBJECT, competition)
        message = password_changed_message(link, changed_at=changed_at, competition=competition)
    queue_mail(subject, message, user.email, competition=competition)
