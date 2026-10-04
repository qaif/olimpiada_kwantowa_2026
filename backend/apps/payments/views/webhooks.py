"""Webhooki dostawców: Stripe i Przelewy24 (transakcje i zwroty). PAY-01 § 4.

Bez sesji i bez CSRF – tożsamością żądania jest **wyłącznie podpis** dostawcy liczony z surowych
bajtów ciała (``APIView`` opakowuje widok w ``csrf_exempt``, a ``authentication_classes = []`` nie
czyta ciasteczka). Ciało czytamy jako ``request.body`` **zanim** cokolwiek dotknie ``request.data``.

Kody odpowiedzi:

- 404 – dostawca nieskonfigurowany (brak sekretu w środowisku: nie ma czym sprawdzić podpisu, więc
  adresu w tej instalacji nie ma),
- 400 – podpis nie zgadza się (jedna treść dla wszystkich powodów – nie podpowiadamy, co poprawić),
- 503 – nie udało się potwierdzić transakcji u dostawcy (P24 ``verify``) – dostawca ponowi,
- 200 – wszystko inne, także zdarzenie nieznane albo powtórzone: dostawca ma przestać ponawiać.

Adres nie zależy od konkursu żądania: płatność odnajdujemy po identyfikatorze sesji u dostawcy,
a konkurs bierzemy z niej – jeden adres webhooka na instalację (OPERACJE § 29).
"""

from __future__ import annotations

import logging

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from ..models import Provider
from ..providers import ProviderError
from ..services import WebhookRejected, handle_webhook

logger = logging.getLogger(__name__)


class _WebhookView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "payments"
    provider_code = ""
    refund = False

    def post(self, request):
        body = request.body
        try:
            outcome = handle_webhook(self.provider_code, body, request.headers, refund=self.refund)
        except WebhookRejected:
            return Response({"error": "invalid signature"}, status=400)
        except ProviderError:
            logger.warning(
                "Webhook %s: potwierdzenie u dostawcy nieudane – prosimy o ponowienie.", self.provider_code
            )
            return Response({"error": "temporarily unavailable"}, status=503)
        # Odpowiedź nie zdradza, czy płatność istnieje i do kogo należy – dostawcy wystarczy 200.
        return Response(
            {"received": True, "outcome": outcome if outcome in ("duplicate", "ignored") else "ok"}
        )


class StripeWebhookView(_WebhookView):
    provider_code = Provider.STRIPE


class Przelewy24WebhookView(_WebhookView):
    provider_code = Provider.P24


class Przelewy24RefundWebhookView(_WebhookView):
    provider_code = Provider.P24
    refund = True
