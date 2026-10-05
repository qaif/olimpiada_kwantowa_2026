"""Backend SMTP, który zapisuje odmowy relaya (MAIL-02 § 2.1, droga a).

Relay (``mail``) sam odrzuca adres z nieistniejącą domeną (``reject_unknown_recipient_domain``),
zanim przyjmie list – sprawdzone 5.10.2026 na obrazie relaya:
``550 5.1.2 <kcadera@o2.plo>: Recipient address rejected: Domain not found``. Takiej odmowy nie ma
w żadnym zawiadomieniu o niedoręczeniu: dowiaduje się o niej wyłącznie nadawca, czyli my – w wyjątku
``smtplib.SMTPRecipientsRefused`` (wszyscy odbiorcy odrzuceni) albo w słowniku zwracanym przez
``sendmail`` (część odrzucona). Django oba gubi: pierwszy zamienia w błąd zadania, drugi ignoruje.

Odmowa **trwała** (5xx) nie jest już wyjątkiem: zadanie ``send_mail_task`` ponawiałoby ją trzy razy
z rosnącym odstępem (i trzy razy zapisało to samo odbicie), choć relay odpowie identycznie. Kończy
się wynikiem 0 i ostrzeżeniem w logu – dokładnie tak, jak list do kilku odbiorców, z których część
odrzucono. Odmowa chwilowa (4xx – np. awaria DNS relaya) rzuca wyjątek jak dotąd i jest ponawiana.

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


def record_refusals(refused: dict) -> None:
    """Zapis odmów ``{adres: (kod, odpowiedź)}``. Błąd zapisu nie może zatrzymać wysyłki."""
    from .models import Source
    from .services import record_bounce

    for address, (code, message) in (refused or {}).items():
        text = clean_reason(f"{code} {_decode(message)}")
        status = status_from(text)
        try:
            record_bounce(
                Bounce(
                    email=address,
                    hard=classify(status, text, failed=int(code) >= 500),
                    status=status,
                    reason=text,
                ),
                source=Source.SMTP,
            )
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
                record_refusals(exc.recipients)
                if all(int(code) >= 500 for code, _message in exc.recipients.values()):
                    refused_all = True
                    logger.warning(
                        "Relay trwale odrzucił wszystkich odbiorców listu (%s) – bez ponowień.",
                        ", ".join(sorted({str(code) for code, _message in exc.recipients.values()})),
                    )
                    return dict(exc.recipients)
                raise
            if refused:
                record_refusals(refused)
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
