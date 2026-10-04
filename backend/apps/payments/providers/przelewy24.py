"""Adapter Przelewy24 (REST API v1): rejestracja transakcji, powiadomienie, ``verify`` i zwrot.

Przelewy24 obsługuje wyłącznie PLN w tym wdrożeniu (konkursy polskie). Przebieg dostawcy:

1. ``POST /api/v1/transaction/register`` – dostajemy ``token``, płacący idzie na
   ``/trnRequest/{token}`` (strona banku/BLIK u dostawcy – nic z tego nie przechodzi przez nas),
2. dostawca woła ``urlStatus`` (nasz webhook) z podpisem SHA-384 pól + CRC,
3. **musimy** potwierdzić transakcję ``PUT /api/v1/transaction/verify`` – bez tego P24 nie
   rozlicza wpłaty. Dopiero udany ``verify`` zapisuje wpłatę u nas (``confirm``),
4. zwrot – ``POST /api/v1/transaction/refund``; wynik przychodzi osobnym powiadomieniem.

Podpis P24 to SHA-384 z JSON-a o **stałej kolejności pól**, bez spacji i bez escapowania
ukośników/Unicode (tak liczy go dostawca w PHP) – stąd :func:`_sign` z ``separators`` i
``ensure_ascii=False``.

Zmienne: ``P24_MERCHANT_ID``, ``P24_POS_ID`` (zwykle = merchant), ``P24_API_KEY`` (klucz raportów
z panelu), ``P24_CRC`` (klucz CRC), ``P24_SANDBOX`` (``true`` = ``sandbox.przelewy24.pl``).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

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

PRODUCTION_HOST = "secure.przelewy24.pl"
SANDBOX_HOST = "sandbox.przelewy24.pl"

#: Limit czasu transakcji u dostawcy (minuty). Po nim P24 nie przyjmie zapłaty tej sesji – i po nim
#: wolno założyć kolejną próbę tego samego zamówienia bez ryzyka podwójnej zapłaty.
TIME_LIMIT_MINUTES = 15

LANGUAGES = {"pl": "pl", "en": "en", "es": "es", "fr": "fr", "pt": "pt", "ru": "ru"}


def _config():
    return {
        "merchant_id": int(getattr(settings, "P24_MERCHANT_ID", 0) or 0),
        "pos_id": int(getattr(settings, "P24_POS_ID", 0) or getattr(settings, "P24_MERCHANT_ID", 0) or 0),
        "api_key": getattr(settings, "P24_API_KEY", "") or "",
        "crc": getattr(settings, "P24_CRC", "") or "",
        "host": SANDBOX_HOST if getattr(settings, "P24_SANDBOX", False) else PRODUCTION_HOST,
    }


def _sign(fields: dict) -> str:
    raw = json.dumps(fields, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha384(raw.encode("utf-8")).hexdigest()


def notification_sign(data: dict, crc: str) -> str:
    """Podpis powiadomienia o transakcji – kolejność pól z dokumentacji P24."""
    return _sign(
        {
            "merchantId": data.get("merchantId"),
            "posId": data.get("posId"),
            "sessionId": data.get("sessionId"),
            "amount": data.get("amount"),
            "originAmount": data.get("originAmount"),
            "currency": data.get("currency"),
            "orderId": data.get("orderId"),
            "methodId": data.get("methodId"),
            "statement": data.get("statement"),
            "crc": crc,
        }
    )


def refund_notification_sign(data: dict, crc: str) -> str:
    return _sign(
        {
            "orderId": data.get("orderId"),
            "sessionId": data.get("sessionId"),
            "refundsUuid": data.get("refundsUuid"),
            "merchantId": data.get("merchantId"),
            "amount": data.get("amount"),
            "currency": data.get("currency"),
            "status": data.get("status"),
            "crc": crc,
        }
    )


class Przelewy24Provider(PaymentProviderBase):
    code = "przelewy24"
    redirect_hosts = (PRODUCTION_HOST, SANDBOX_HOST)

    def is_configured(self) -> bool:
        config = _config()
        return bool(config["merchant_id"] and config["api_key"] and config["crc"])

    def supports_currency(self, currency: str) -> bool:
        return currency == "PLN"

    def _request(self, method: str, path: str, payload: dict) -> dict:
        config = _config()
        try:
            response = requests.request(
                method,
                f"https://{config['host']}/api/v1{path}",
                json=payload,
                auth=(str(config["pos_id"]), config["api_key"]),
                timeout=HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            logger.warning("Przelewy24 nie odpowiedział (%s): %s", path, exc.__class__.__name__)
            raise ProviderError("Przelewy24 is not responding.") from exc
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400:
            logger.warning("Przelewy24 odmówił (%s): HTTP %s", path, response.status_code)
            raise ProviderError(f"HTTP {response.status_code}")
        return body

    def create_checkout(self, request: CheckoutRequest) -> CheckoutResult:
        config = _config()
        amount = to_minor(request.amount)
        session_id = request.payment_uuid
        payload = {
            "merchantId": config["merchant_id"],
            "posId": config["pos_id"],
            "sessionId": session_id,
            "amount": amount,
            "currency": request.currency,
            "description": request.description[:1024],
            "email": request.customer_email,
            "country": "PL",
            "language": LANGUAGES.get(request.language, "en"),
            "urlReturn": request.return_url,
            "urlStatus": request.notify_url,
            "timeLimit": TIME_LIMIT_MINUTES,
            "encoding": "UTF-8",
            "sign": _sign(
                {
                    "sessionId": session_id,
                    "merchantId": config["merchant_id"],
                    "amount": amount,
                    "currency": request.currency,
                    "crc": config["crc"],
                }
            ),
        }
        body = self._request("POST", "/transaction/register", payload)
        token = (body.get("data") or {}).get("token")
        if not token:
            raise ProviderError("missing-token")
        return CheckoutResult(
            redirect_url=f"https://{config['host']}/trnRequest/{token}", provider_ref=session_id
        )

    def parse_webhook(self, body: bytes, headers) -> WebhookResult:
        config = _config()
        try:
            data = json.loads(body.decode("utf-8"))
        except ValueError as exc:
            raise SignatureError("p24-payload") from exc
        if not isinstance(data, dict) or not config["crc"]:
            raise SignatureError("p24")
        expected = notification_sign(data, config["crc"])
        if not hmac.compare_digest(expected, str(data.get("sign", ""))):
            raise SignatureError("p24")
        if (
            int(data.get("merchantId") or 0) != config["merchant_id"]
            or int(data.get("posId") or 0) != config["pos_id"]
        ):
            raise SignatureError("p24-merchant")
        session_id = str(data.get("sessionId", ""))
        order_id = str(data.get("orderId", ""))
        return WebhookResult(
            event_id=f"{session_id}:{order_id}",
            event_type="transaction.notification",
            outcome="succeeded",
            provider_ref=session_id,
            payment_uuid=session_id,
            provider_payment_id=order_id,
            amount_minor=data.get("amount"),
            currency=str(data.get("currency", "")).upper(),
            extra={"order_id": data.get("orderId")},
        )

    def parse_refund_webhook(self, body: bytes) -> WebhookResult:
        config = _config()
        try:
            data = json.loads(body.decode("utf-8"))
        except ValueError as exc:
            raise SignatureError("p24-payload") from exc
        if not isinstance(data, dict) or not config["crc"]:
            raise SignatureError("p24")
        if not hmac.compare_digest(refund_notification_sign(data, config["crc"]), str(data.get("sign", ""))):
            raise SignatureError("p24")
        refunds_uuid = str(data.get("refundsUuid", ""))
        return WebhookResult(
            event_id=f"refund:{refunds_uuid}:{data.get('status')}",
            event_type="refund.notification",
            outcome="refund",
            refund_id=refunds_uuid,
            refund_status="succeeded" if str(data.get("status")) == "0" else "failed",
        )

    def confirm(self, payment, result: WebhookResult) -> bool:
        """``PUT /transaction/verify`` – bez niego P24 nie rozlicza wpłaty, a my jej nie zapisujemy."""
        config = _config()
        amount = to_minor(payment.amount)
        order_id = int(result.extra.get("order_id") or 0)
        payload = {
            "merchantId": config["merchant_id"],
            "posId": config["pos_id"],
            "sessionId": payment.provider_ref,
            "amount": amount,
            "currency": payment.currency,
            "orderId": order_id,
            "sign": _sign(
                {
                    "sessionId": payment.provider_ref,
                    "orderId": order_id,
                    "amount": amount,
                    "currency": payment.currency,
                    "crc": config["crc"],
                }
            ),
        }
        body = self._request("PUT", "/transaction/verify", payload)
        return (body.get("data") or {}).get("status") == "success"

    def refund(self, payment, amount, *, refund_uuid: str, reason: str, notify_url: str = "") -> RefundResult:
        if not payment.provider_payment_id:
            raise ProviderError("missing-order-id")
        payload = {
            "requestId": refund_uuid,
            "refundsUuid": refund_uuid,
            "urlStatus": notify_url,
            "refunds": [
                {
                    "orderId": int(payment.provider_payment_id),
                    "sessionId": payment.provider_ref,
                    "amount": to_minor(amount),
                    "description": reason[:35],
                }
            ],
        }
        body = self._request("POST", "/transaction/refund", payload)
        rows = body.get("data") or []
        if rows and rows[0].get("status") is False:
            return RefundResult(provider_refund_id=refund_uuid, status="failed")
        return RefundResult(provider_refund_id=refund_uuid, status="pending")
