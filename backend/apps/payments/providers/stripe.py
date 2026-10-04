"""Adapter Stripe: Checkout Session, webhook z podpisem, wygaszenie sesji i zwrot (PAY-01 § 3–5).

Bez SDK – cztery wywołania REST (``POST /v1/checkout/sessions``, ``…/{id}/expire``,
``POST /v1/refunds``) i weryfikacja podpisu, którą Stripe opisuje wprost (HMAC-SHA256 z
``"{t}.{surowe ciało}"``, nagłówek ``Stripe-Signature: t=…,v1=…``). SDK byłoby nową zależnością
obrazu dla czterech żądań, a jego weryfikacja podpisu i tak sprowadza się do tych samych linijek.

**Danych karty serwer nie widzi nigdy**: płacący wpisuje je na stronie Checkout (``checkout.stripe.com``),
a do nas wraca wyłącznie zdarzenie ``checkout.session.completed`` z kwotą i identyfikatorami.

Klucze: ``STRIPE_SECRET_KEY`` (``sk_test_…`` w trybie testowym) i ``STRIPE_WEBHOOK_SECRET``
(``whsec_…``; kilka po przecinku – rotacja albo kilka adresów w panelu Stripe).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from urllib.parse import urlsplit

import requests
from django.conf import settings

from .base import (
    HTTP_TIMEOUT,
    CheckoutRequest,
    CheckoutResult,
    PaymentProviderBase,
    ProviderError,
    RefundResult,
    SignatureError,
    WebhookResult,
    to_minor,
)

logger = logging.getLogger(__name__)


def send(method: str, url: str, *, timeout: float, **kwargs):
    """Jedno wyjście do sieci tego adaptera – testy podmieniają właśnie je (każdy adapter osobno)."""
    return requests.request(method, url, timeout=timeout, **kwargs)


API_BASE = "https://api.stripe.com/v1"

#: Tolerancja znacznika czasu podpisu – tyle samo, co domyślnie w bibliotekach Stripe i u nas
#: (``apps.integrations.inbound.TOLERANCE_SECONDS``).
TOLERANCE_SECONDS = 300

#: Czas życia sesji Checkout. Stripe przyjmuje 30 min – 24 h; godzina wystarcza na zapłatę,
#: a krótko otwarta sesja to krótsze okno na podwójną zapłatę tego samego zamówienia.
SESSION_TTL_SECONDS = 60 * 60

#: Nasze kody języków → ``locale`` Checkout. Brak na liście = ``auto`` (język przeglądarki).
LOCALES = {
    "en": "en",
    "pl": "pl",
    "es": "es",
    "fr": "fr",
    "pt": "pt",
    "ru": "ru",
    "zh-hans": "zh",
    "id": "id",
}


def secret_key() -> str:
    return getattr(settings, "STRIPE_SECRET_KEY", "") or ""


def webhook_secrets() -> list[str]:
    raw = getattr(settings, "STRIPE_WEBHOOK_SECRET", "") or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


def is_test_mode() -> bool:
    return secret_key().startswith(("sk_test_", "rk_test_"))


def parse_signature_header(header: str) -> tuple[int, list[str]] | None:
    """``t=…,v1=…,v1=…,v0=…`` → (czas, podpisy v1). Inny kształt = ``None``."""
    timestamp = None
    signatures: list[str] = []
    for item in (header or "").split(","):
        name, _, value = item.strip().partition("=")
        if name == "t":
            try:
                timestamp = int(value)
            except ValueError:
                return None
        elif name == "v1" and value:
            signatures.append(value)
    if timestamp is None or not signatures:
        return None
    return timestamp, signatures


def verify_signature(body: bytes, header: str, secrets_: list[str], *, now: float | None = None) -> bool:
    """Czy nagłówek ``Stripe-Signature`` jest poprawnym podpisem tych bajtów którymś z sekretów.

    ``compare_digest`` – stały czas porównania; tolerancja czasu odcina powtórkę nagranego żądania.
    """
    parsed = parse_signature_header(header)
    if parsed is None or not secrets_:
        return False
    timestamp, signatures = parsed
    if abs((now if now is not None else time.time()) - timestamp) > TOLERANCE_SECONDS:
        return False
    signed = f"{timestamp}.".encode() + body
    for secret in secrets_:
        expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        if any(hmac.compare_digest(expected, candidate) for candidate in signatures):
            return True
    return False


def sign(body: bytes, secret: str, timestamp: int | None = None) -> str:
    """Nagłówek ``Stripe-Signature`` dla ciała – do testów i do diagnozy u operatora."""
    timestamp = int(timestamp if timestamp is not None else time.time())
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


class StripeProvider(PaymentProviderBase):
    code = "stripe"
    redirect_hosts = ("checkout.stripe.com",)

    def is_configured(self) -> bool:
        return bool(secret_key()) and bool(webhook_secrets())

    # --- HTTP -------------------------------------------------------------------------------------

    def _request(
        self, method: str, path: str, data: dict | None = None, *, idempotency_key: str = ""
    ) -> dict:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        try:
            response = send(
                method,
                f"{API_BASE}{path}",
                data=data,
                auth=(secret_key(), ""),
                headers=headers,
                timeout=HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            logger.warning("Stripe nie odpowiedział (%s): %s", path, exc.__class__.__name__)
            raise ProviderError("Stripe is not responding.", transient=True) from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 400:
            error = payload.get("error") or {}
            # Do logu kod i typ błędu – bez komunikatu, który bywa echem danych płacącego.
            logger.warning(
                "Stripe odmówił (%s): HTTP %s %s/%s",
                path,
                response.status_code,
                error.get("type", ""),
                error.get("code", ""),
            )
            # 5xx i 429: wynik nieznany – powtórka z tym samym kluczem idempotencji jest bezpieczna.
            transient = response.status_code >= 500 or response.status_code == 429
            raise ProviderError(error.get("code") or f"HTTP {response.status_code}", transient=transient)
        return payload

    def _post(self, path: str, data: dict, *, idempotency_key: str) -> dict:
        return self._request("POST", path, data, idempotency_key=idempotency_key)

    # --- interfejs --------------------------------------------------------------------------------

    def create_checkout(self, request: CheckoutRequest) -> CheckoutResult:
        separator = "&" if "?" in request.return_url else "?"
        data = {
            "mode": "payment",
            "success_url": f"{request.return_url}{separator}checkout=success",
            "cancel_url": f"{request.return_url}{separator}checkout=cancel",
            "client_reference_id": request.payment_uuid,
            "customer_email": request.customer_email,
            "expires_at": int(time.time()) + SESSION_TTL_SECONDS,
            "locale": LOCALES.get(request.language, "auto"),
            "line_items[0][quantity]": 1,
            "line_items[0][price_data][currency]": request.currency.lower(),
            "line_items[0][price_data][unit_amount]": to_minor(request.amount),
            "line_items[0][price_data][product_data][name]": request.description[:250],
            "metadata[payment_uuid]": request.payment_uuid,
            "metadata[order_reference]": request.order_reference,
            "metadata[competition]": request.competition_slug,
            "payment_intent_data[description]": request.description[:250],
            "payment_intent_data[metadata][payment_uuid]": request.payment_uuid,
            "payment_intent_data[metadata][order_reference]": request.order_reference,
        }
        session = self._post("/checkout/sessions", data, idempotency_key=f"checkout-{request.payment_uuid}")
        url = session.get("url") or ""
        if not session.get("id") or urlsplit(url).hostname not in self.redirect_hosts:
            raise ProviderError("unexpected-checkout-response")
        return CheckoutResult(redirect_url=url, provider_ref=session["id"])

    def expire(self, payment) -> bool:
        if not payment.provider_ref:
            return True
        try:
            self._post(
                f"/checkout/sessions/{payment.provider_ref}/expire",
                {},
                idempotency_key=f"expire-{payment.uuid}",
            )
        except ProviderError:
            # Sesja już zakończona (zapłacona albo wygasła sama) – nie wiemy która, więc „nie”.
            return False
        return True

    def session_status(self, payment) -> dict | None:
        """``GET /v1/checkout/sessions/{id}`` – po nieudanym ``expire`` i w sprzątaniu zaległych prób."""
        if not payment.provider_ref:
            return None
        try:
            session = self._request("GET", f"/checkout/sessions/{payment.provider_ref}")
        except ProviderError:
            return None
        return {
            "status": session.get("status") or "",
            "payment_status": session.get("payment_status") or "",
            "amount_minor": session.get("amount_total"),
            "currency": str(session.get("currency") or "").upper(),
            "payment_intent": str(session.get("payment_intent") or ""),
        }

    def refund(self, payment, amount, *, refund_uuid: str, reason: str, notify_url: str = "") -> RefundResult:
        if not payment.provider_payment_id:
            raise ProviderError("missing-payment-intent")
        data = {
            "payment_intent": payment.provider_payment_id,
            "amount": to_minor(amount),
            "metadata[refund_uuid]": refund_uuid,
            "metadata[payment_uuid]": str(payment.uuid),
        }
        refund = self._post("/refunds", data, idempotency_key=f"refund-{refund_uuid}")
        status = refund.get("status") or "pending"
        mapped = {"succeeded": "succeeded", "failed": "failed", "canceled": "failed"}.get(status, "pending")
        return RefundResult(provider_refund_id=refund.get("id", ""), status=mapped)

    def parse_webhook(self, body: bytes, headers) -> WebhookResult:
        if not verify_signature(body, headers.get("Stripe-Signature", ""), webhook_secrets()):
            raise SignatureError("stripe")
        try:
            event = json.loads(body.decode("utf-8"))
            event_id = str(event["id"])
            event_type = str(event.get("type", ""))
            obj = event["data"]["object"]
        except (ValueError, KeyError, TypeError) as exc:
            raise SignatureError("stripe-payload") from exc
        result = WebhookResult(event_id=event_id, event_type=event_type, outcome="ignored")
        livemode = event.get("livemode")
        if isinstance(livemode, bool) and livemode == is_test_mode():
            # Zdarzenie trybu live przy kluczu testowym (albo odwrotnie): dwa endpointy wskazujące ten
            # sam adres. Podpis jest poprawny, ale ta instalacja tych pieniędzy nie prowadzi.
            result.mode_mismatch = True
            return result
        if event_type.startswith("checkout.session."):
            result.provider_ref = str(obj.get("id", ""))
            result.payment_uuid = str(
                (obj.get("metadata") or {}).get("payment_uuid") or obj.get("client_reference_id") or ""
            )
            result.provider_payment_id = str(obj.get("payment_intent") or "")
            result.amount_minor = obj.get("amount_total")
            result.currency = str(obj.get("currency") or "").upper()
            if event_type == "checkout.session.completed":
                # „complete” z ``payment_status=unpaid`` to metoda odroczona (np. przelew SEPA):
                # pieniędzy jeszcze nie ma, a rozstrzygnie ``async_payment_succeeded/failed``.
                result.outcome = "succeeded" if obj.get("payment_status") == "paid" else "ignored"
            elif event_type == "checkout.session.async_payment_succeeded":
                result.outcome = "succeeded"
            elif event_type == "checkout.session.async_payment_failed":
                result.outcome = "failed"
            elif event_type == "checkout.session.expired":
                result.outcome = "expired"
        elif event_type in ("refund.updated", "refund.failed", "refund.created", "charge.refund.updated"):
            result.outcome = "refund"
            result.refund_id = str(obj.get("id", ""))
            result.refund_uuid = str((obj.get("metadata") or {}).get("refund_uuid") or "")
            status = str(obj.get("status", ""))
            result.refund_status = {"succeeded": "succeeded", "failed": "failed", "canceled": "failed"}.get(
                status, "pending"
            )
        return result
