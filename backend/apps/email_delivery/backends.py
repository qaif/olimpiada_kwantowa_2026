"""Backend SMTP, który zapisuje odmowy relaya (MAIL-02 § 2.1, droga a).

Relay (``mail``) potrafi odrzucić odbiorcę, zanim przyjmie list – np. adres z nieistniejącą domeną
(``reject_unknown_recipient_domain``, sprawdzone 5.10.2026 na obrazie relaya: ``450 4.1.2 <x@o2.plo>:
Recipient address rejected: Domain not found``). Takiej odmowy nie ma w żadnym zawiadomieniu
o niedoręczeniu: dowiaduje się o niej wyłącznie nadawca – w wyjątku ``smtplib.SMTPRecipientsRefused``
(wszyscy odbiorcy odrzuceni) albo w słowniku zwracanym przez ``sendmail`` (część odrzucona).

Zasady (przegląd PR #98, M1):

- wyjątek jest **połykany** tylko wtedy, gdy ``bounces.classify`` uznaje odmowę za twardą dla
  **każdego** odbiorcy (adres/domena nie istnieje) – ponowienie nic by nie zmieniło. Zadanie kończy
  się wynikiem 0 i ostrzeżeniem w logu,
- każda inna odmowa (4xx, ``554 5.7.1`` – polityka relaya, błąd konfiguracji, nadawca spoza
  ``ALLOWED_SENDER_DOMAINS``) leci dalej jak dotąd: ponowienia ``send_mail_task``, błąd w logu workera
  i w GlitchTipie,
- jako odbicie adresu zapisujemy wyłącznie odmowy dotyczące adresu (``Bounce.recipient_related``:
  twarde i klasy ``x.1.x``/``x.2.x``). Odmowa z powodu polityki czy konfiguracji relaya **nie** jest
  przypisywana adresowi odbiorcy – to nasz problem, nie jego.

Podstawiany w ``MAILERS`` zamiast backendu SMTP Django przy ``EMAIL_BOUNCE_TRACKING``
(``config/settings/base.py``); bez przełącznika – backend Django bez zmian.
"""

from __future__ import annotations

import logging
import smtplib

from django.core.mail.backends.smtp import EmailBackend

from .bounces import Bounce, classify, clean_reason, status_from

logger = logging.getLogger(__name__)


def _decode(message) -> str:
    if isinstance(message, bytes):
        return message.decode("utf-8", errors="replace")
    return str(message or "")


def refusal_bounces(refused: dict) -> list[Bounce]:
    """Odmowy ``{adres: (kod, odpowiedź)}`` jako odbicia (bez zapisu)."""
    bounces = []
    for address, (code, message) in (refused or {}).items():
        text = clean_reason(f"{code} {_decode(message)}")
        status = status_from(text)
        bounces.append(
            Bounce(
                email=address,
                hard=classify(status, text, failed=int(code) >= 500),
                status=status,
                reason=text,
            )
        )
    return bounces


def record_refusals(bounces: list[Bounce]) -> None:
    """Zapis odmów dotyczących adresu. Błąd zapisu nie może zatrzymać wysyłki."""
    from .models import Source
    from .services import record_bounce

    for bounce in bounces:
        if not bounce.recipient_related:
            continue
        try:
            record_bounce(bounce, source=Source.SMTP)
        except Exception:  # noqa: BLE001 - zapis odbicia jest dodatkiem do wysyłki, nie jej warunkiem
            logger.exception("Nie udało się zapisać odmowy relaya.")


class TrackingSMTPBackend(EmailBackend):
    def _send(self, email_message):
        connection = self.connection
        original = connection.sendmail
        refused_all = False

        def sendmail(*args, **kwargs):
            nonlocal refused_all
            try:
                refused = original(*args, **kwargs)
            except smtplib.SMTPRecipientsRefused as exc:
                bounces = refusal_bounces(exc.recipients)
                record_refusals(bounces)
                if bounces and all(bounce.hard for bounce in bounces):
                    refused_all = True
                    logger.warning(
                        "Relay odrzucił wszystkich odbiorców listu (%s) – adres nie istnieje, bez ponowień.",
                        ", ".join(sorted({bounce.status for bounce in bounces})),
                    )
                    return dict(exc.recipients)
                raise
            if refused:
                record_refusals(refusal_bounces(refused))
                logger.warning("Relay odrzucił %s odbiorców listu, pozostałym wysłano.", len(refused))
            return refused

        # Atrybut instancji przykrywa metodę klasy ``smtplib.SMTP`` na czas jednego listu.
        connection.sendmail = sendmail
        try:
            sent = super()._send(email_message)
        finally:
            try:
                del connection.sendmail
            except AttributeError:
                pass
        return False if refused_all else sent
