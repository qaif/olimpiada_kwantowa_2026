"""Wspólny interfejs dostawców płatności online (PAY-01 § 3–5).

Serwis (``apps.payments.services``) zna wyłącznie ten interfejs – nazwy pól Stripe'a i Przelewy24
żyją w adapterach. Dzięki temu reguły, które **nie zależą** od dostawcy (kwota z serwera,
idempotencja doręczenia, porównanie kwoty, numer faktury), są napisane raz.

Każdy adapter rozmawia z dostawcą **wyłącznie** przez HTTPS ``requests`` z limitem czasu i niczego
nie zapisuje w bazie – zapis jest pracą serwisu, w jego transakcji. Sekrety czyta z ustawień
(``config/settings/base.py`` ← zmienne środowiskowe), nigdy z bazy i nigdy od klienta.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

#: Waluty z dwoma miejscami po przecinku – jedyne, które obsługujemy (kwota w groszach/centach
#: = kwota × 100). Waluta spoza listy (JPY, KWD) wymagałaby innej jednostki i jest odmową.
TWO_DECIMAL_CURRENCIES = frozenset(
    {"PLN", "EUR", "USD", "GBP", "CHF", "CZK", "SEK", "NOK", "DKK", "HUF", "RON", "CAD", "AUD", "NZD"}
)

#: Limit czasu rozmowy z dostawcą (sekundy). Żądanie użytkownika czeka na odpowiedź, więc krótko.
HTTP_TIMEOUT = 20


class ProviderError(Exception):
    """Dostawca odmówił albo nie odpowiedział. Komunikat bez sekretów i bez danych płacącego.

    ``transient=True`` – brak odpowiedzi, przekroczony czas, 5xx albo 429: wynik u dostawcy jest
    **nieznany**, więc operację powtarza się z tym samym kluczem idempotencji, a nie uznaje za odmowę.
    """

    def __init__(self, message: str = "", *, transient: bool = False):
        super().__init__(message)
        self.transient = transient


class SignatureError(Exception):
    """Podpis webhooka nie zgadza się (albo go nie ma). Odpowiedź: 400, bez szczegółów."""


def to_minor(amount: Decimal) -> int:
    """Kwota w najmniejszej jednostce (centy, grosze) – tak liczą obaj dostawcy."""
    return int((Decimal(amount) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def from_minor(value) -> Decimal:
    return (Decimal(int(value)) / 100).quantize(Decimal("0.01"))


@dataclass
class CheckoutRequest:
    """Wszystko, czego adapter potrzebuje do założenia sesji płatności. Kwota – z ``Payment``."""

    payment_uuid: str
    amount: Decimal
    currency: str
    description: str
    order_reference: str
    competition_slug: str
    customer_email: str
    language: str
    return_url: str
    notify_url: str = ""


@dataclass
class CheckoutResult:
    redirect_url: str
    provider_ref: str


@dataclass
class WebhookResult:
    """Zdarzenie od dostawcy przełożone na słownik serwisu.

    ``outcome``: ``succeeded`` / ``failed`` / ``expired`` / ``refund`` / ``ignored``.
    ``amount_minor`` i ``currency`` – to, co twierdzi dostawca; serwis porównuje je z ``Payment``.
    """

    event_id: str
    event_type: str
    outcome: str
    provider_ref: str = ""
    payment_uuid: str = ""
    provider_payment_id: str = ""
    amount_minor: int | None = None
    currency: str = ""
    refund_id: str = ""
    refund_status: str = ""
    #: Nasz identyfikator zwrotu z metadanych zdarzenia – odwrót, gdy identyfikator dostawcy jeszcze
    #: nie został zapisany (zwrot, którego odpowiedź API zgubiła się po drodze).
    refund_uuid: str = ""
    #: Zdarzenie z innego trybu niż skonfigurowany klucz (test ↔ live) – zapisujemy i pomijamy.
    mode_mismatch: bool = False
    extra: dict = field(default_factory=dict)


@dataclass
class RefundResult:
    provider_refund_id: str
    #: ``succeeded`` / ``pending`` / ``failed``
    status: str


class PaymentProviderBase:
    """Interfejs adaptera. Metody podnoszą :class:`ProviderError` przy odmowie dostawcy."""

    code: str = ""
    #: Hosty, na które wolno przekierować płacącego. Adres z odpowiedzi dostawcy spoza tej listy
    #: jest odmową – przekierowanie jest ostatnim krokiem, w którym da się jeszcze podmienić cel.
    redirect_hosts: tuple[str, ...] = ()

    def is_configured(self) -> bool:  # pragma: no cover - interfejs
        raise NotImplementedError

    def supports_currency(self, currency: str) -> bool:
        return currency in TWO_DECIMAL_CURRENCIES

    def create_checkout(self, request: CheckoutRequest) -> CheckoutResult:  # pragma: no cover
        raise NotImplementedError

    def parse_webhook(self, body: bytes, headers) -> WebhookResult:  # pragma: no cover
        raise NotImplementedError

    def confirm(self, payment, result: WebhookResult) -> bool:
        """Dodatkowe potwierdzenie u dostawcy przed zapisaniem wpłaty (P24 ``verify``)."""
        return True

    def expire(self, payment) -> bool:
        """Wygasza otwartą sesję. ``True`` = sesja zamknięta i nie przyjmie już zapłaty."""
        return False

    def session_status(self, payment) -> dict | None:
        """Stan sesji u dostawcy (``status``, ``payment_status``, kwota) albo ``None``, gdy nieznany."""
        return None

    def refund(
        self, payment, amount: Decimal, *, refund_uuid: str, reason: str, notify_url: str = ""
    ) -> RefundResult:  # pragma: no cover
        raise NotImplementedError
