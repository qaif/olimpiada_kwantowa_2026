"""Listy do właściciela konta o zdarzeniach drugiego składnika (SEC-01 § 7).

Po co listy, skoro wszystko jest w audycie: audyt czyta organizator, a list – **właściciel**, czyli
jedyna osoba, która wie, czy to on wyłączył zabezpieczenie albo zużył kod zapasowy. List „ktoś
wyłączył drugi składnik na Twoim koncie” jest często jedynym sygnałem przejęcia, który ofiara
w ogóle dostaje.

Treść bez sekretów i bez linków logowania: list może przeczytać ktoś, kto ma skrzynkę, a nie
konto. Wysyłka po commicie, kolejka ``mail`` (``apps.accounts.activation.queue_mail``), w języku
odbiorcy (``recipient_language`` – reset składa koordynator w swoim języku).
"""

from __future__ import annotations

from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

#: Zdarzenie → (temat, zdanie główne). ``gettext_noop`` – tłumaczymy w chwili składania listu,
#: w języku odbiorcy.
EVENTS: dict[str, tuple[str, str]] = {
    "enabled": (
        gettext_noop("Włączono logowanie dwuskładnikowe"),
        gettext_noop("Na Twoim koncie włączono logowanie dwuskładnikowe (kod z aplikacji w telefonie)."),
    ),
    "disabled": (
        gettext_noop("Wyłączono logowanie dwuskładnikowe"),
        gettext_noop("Na Twoim koncie wyłączono logowanie dwuskładnikowe. Konto chroni teraz samo hasło."),
    ),
    "codes_regenerated": (
        gettext_noop("Nowe kody zapasowe"),
        gettext_noop(
            "Wygenerowano nowy komplet kodów zapasowych do logowania dwuskładnikowego. "
            "Poprzednie kody przestały działać."
        ),
    ),
    "backup_used": (
        gettext_noop("Użyto kodu zapasowego"),
        gettext_noop("Do zalogowania na Twoje konto użyto jednego z kodów zapasowych."),
    ),
    "reset": (
        gettext_noop("Organizator zdjął logowanie dwuskładnikowe"),
        gettext_noop(
            "Organizator zdjął logowanie dwuskładnikowe z Twojego konta (np. po zgłoszeniu zgubionego "
            "telefonu). Po zalogowaniu hasłem skonfiguruj je od nowa na nowym urządzeniu."
        ),
    ),
    "locked": (
        gettext_noop("Wstrzymano logowanie po błędnych kodach"),
        gettext_noop(
            "Na Twoje konto kilka razy z rzędu wpisano błędny kod logowania dwuskładnikowego. "
            "Logowanie kodem jest wstrzymane na %(minutes)s min."
        ),
    ),
}


def _body(event: str, user, *, competition, params: dict) -> str:
    from apps.accounts.activation import signature_lines

    lead = _(EVENTS[event][1]) % params if "%(" in EVENTS[event][1] else _(EVENTS[event][1])
    lines = [lead, ""]
    if event == "backup_used":
        lines += [_("Zostało kodów zapasowych: %(left)s.") % {"left": params.get("codes_left", 0)}, ""]
    lines += [
        _("Konto: %(email)s") % {"email": user.email},
        _("Kiedy: %(when)s") % {"when": timezone.localtime().strftime("%Y-%m-%d %H:%M %Z")},
        "",
        _(
            "Jeśli ta wiadomość Cię zaskoczyła, natychmiast zmień hasło i skontaktuj się "
            "z organizatorem – ktoś może znać Twoje hasło."
        ),
        "",
        *signature_lines(competition),
    ]
    return "\n".join(lines)


def notify(user, event: str, *, request=None, **params) -> None:
    """Kolejkuje list o zdarzeniu ``event`` (klucz :data:`EVENTS`) do właściciela konta."""
    if event not in EVENTS or not getattr(user, "email", ""):
        return
    from apps.accounts.activation import mail_competition, queue_mail, recipient_language
    from apps.tenancy import branding

    competition = mail_competition(getattr(request, "competition", None) if request is not None else None)
    with recipient_language(user, competition, request=request):
        names = branding.brand_names(competition)
        subject = _("%(what)s – %(competition)s") % {
            "what": _(EVENTS[event][0]),
            "competition": names["competition"],
        }
        body = _body(event, user, competition=competition, params=params)
    queue_mail(subject, body, user.email, competition=competition)
    if event in SENT_TO_PREVIOUS_ADDRESSES:
        # Przegląd SEC-01 (H1): adres zmieniony niedawno mógł zmienić przejmujący – list o zdjęciu
        # zabezpieczenia idzie też tam, gdzie właściciel go jeszcze czyta.
        from .security import recent_previous_emails

        for previous in recent_previous_emails(user):
            queue_mail(subject, body, previous, competition=competition)


#: Zdarzenia, o których zawiadamiamy także poprzednie adresy konta z ostatnich dni.
SENT_TO_PREVIOUS_ADDRESSES = frozenset({"reset", "disabled"})
