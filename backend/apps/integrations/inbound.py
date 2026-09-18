"""Webhook przychodzący dostawcy płatności – **stub, i dokładnie stub** (§ 1.5.1).

Do wydania K cały ruch integracyjny szedł w jedną stronę: partner pytał kluczem
(``apps.integrations.api``) albo my dzwoniliśmy do niego podpisanym ładunkiem
(``apps.integrations.webhooks``). To jest pierwszy adres, pod którym **cudzy serwer zmienia stan
systemu bez konta i bez klucza API** – i dlatego dostaje komplet zabezpieczeń od razu, zanim
jakikolwiek dostawca zostanie wybrany.

**Żadnego dostawcy tu nie ma i to jest celowe (decyzja D18, § 6).** Nie ma SDK, nie ma nazw pól
Przelewów24, PayU, Tpaya ani Stripe'a, nie ma adresu do odpytania zwrotnego. Jest jedno: HMAC
z surowego ciała, tolerancja czasowa i idempotencja po identyfikatorze operacji – czyli część
wspólna wszystkich czterech. Adapter przekładający ładunek konkretnego dostawcy na kształt z
:func:`read_confirmation` ma być zmianą na sto linijek, a nie zmianą modelu.

Weryfikacja podpisu jest **symetryczna** wobec tej, którą sami wysyłamy: ten sam nagłówek
(``X-Olimpiada-Signature: t=<unix>,v1=<hex>``), ta sama funkcja licząca podpis
(``apps.integrations.webhooks.signature_header``) i ta sama tolerancja 300 s, co opisuje
``docs/API.md`` § 3.3. Jedna funkcja po obu stronach znaczy, że nie da się poprawić podpisywania,
zapominając o sprawdzaniu – a to jest dokładnie ten błąd, którym kończą się dwie kopie tej samej
formuły.

Cztery reguły, każda z powodem:

1. **sekret per konkurs i per dostawca** (``PaymentEndpoint.secret``) – sekret instalacji znaczyłby,
   że jeden wyciek dotyczy wszystkich organizatorów;
2. **podpis z surowych bajtów** ``request.body``, nigdy z JSON-a odtworzonego z obiektu: kolejność
   kluczy zmienia podpis (ta sama uwaga stoi w ``docs/API.md`` § 3.3). Dlatego ciało czytamy
   **zanim** dotkniemy ``request.data`` – po sparsowaniu przez DRF surowe bajty są już nie do
   odzyskania;
3. **idempotencja po kluczu doręczenia**: powtórzone doręczenie nie tworzy drugiej wpłaty i nie
   zmienia ``paid_at``. Pilnują jej dwie niezależne rzeczy – więz unikalności na
   ``PaymentEvent(endpoint, delivery_key)`` i sam ``record_payment``, który po identyfikatorze
   wpłaty jest idempotentny (T47);
4. **2xx po zapisaniu wiersza**, i wiersz zapisujemy **zawsze**, także dla nieznanej należności
   (``matched=False``) – dostawca przestanie ponawiać, a organizator musi mieć co dopasować ręcznie.

**Czego odpowiedź nie zdradza.** Ciałem jest ``{"matched": true|false}`` i nic ponadto. Nie mówimy,
czyja to należność, ile wynosi ani dlaczego się nie dopasowała: powód (``unmatched_reason``) zostaje
w wierszu dla organizatora, bo pytający jest tu serwerem, który zna wyłącznie swój identyfikator
wpłaty, i nie ma powodu dowiadywać się z kodów odpowiedzi, które identyfikatory istnieją w bazie.
Z tego samego powodu konkurs bez flagi ``fees`` odpowiada **404**, a nie 403 (§ 3): Konkurs #1
wpisowego nie pobiera, więc pod tym adresem nie ma u niego niczego.

**CSRF.** ``APIView.as_view()`` opakowuje widok w ``csrf_exempt``, a ``authentication_classes = []``
znaczy brak ``SessionAuthentication``, czyli brak sprawdzania ciasteczka sesji. Nie ma tu więc czego
wyłączać ręcznie – i nie ma też czego obejść: tożsamością żądania jest wyłącznie podpis.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status as http
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.competitions.scoping import competition_of
from apps.core.api import DomainError
from apps.core.models import audit
from apps.tenancy.fees import fees_enabled, find_fee_by_reference, record_payment

from .models import (
    UNMATCHED_IGNORED_STATUS,
    UNMATCHED_REASONS,
    UNMATCHED_UNKNOWN_REFERENCE,
    PaymentEndpoint,
    PaymentEvent,
    generate_secret,
)
from .webhooks import SIGNATURE_HEADER, signature_header

logger = logging.getLogger(__name__)

#: Ile sekund wstecz i wprzód wolno się rozjechać zegarom, zanim podpis przestaje być ważny.
#: Pięć minut – dokładnie tyle, ile zalecamy odbiorcom naszych webhooków (``docs/API.md`` § 3.3).
#: Krócej znaczyłoby odrzucanie doręczeń z serwerów bez synchronizacji czasu; dłużej – dłuższe okno,
#: w którym nagrane żądanie da się odtworzyć u kogoś, kto nie prowadzi dziennika doręczeń.
TOLERANCE_SECONDS = 300

#: Nagłówek żądania w postaci, w której widzi go Django (``request.META``).
SIGNATURE_META = "HTTP_" + SIGNATURE_HEADER.upper().replace("-", "_")

#: Wartość pola ``status`` uznawana za potwierdzenie wpłaty. Dostawcy nazywają to różnie
#: („paid”, „success”, „AUTHORIZED”, „TRANSACTION_COMPLETED”) i **to jest praca adaptera**, a nie
#: tego modułu: zgadywanie cudzego słownika skończyłoby się zapisaniem nieudanej płatności jako
#: udanej. Brak pola znaczy „to jest potwierdzenie” – tak wygląda najprostszy możliwy ładunek.
PAID_STATUS = "paid"

#: Maksymalna długość identyfikatorów przyjmowanych z ładunku – tyle, ile mają kolumny
#: (``ParticipantFee.external_reference``, ``PaymentEvent.delivery_key``). Dłuższy identyfikator
#: odrzucamy, zamiast przycinać: przycięty nie dopasowałby się do niczego, a wyglądałby na zapisany.
MAX_REFERENCE_LENGTH = 120


# --- podpis ---------------------------------------------------------------------------------------


def parse_signature(header: str) -> tuple[int, str] | None:
    """Rozbija ``t=<unix>,v1=<hex>`` na parę (znacznik czasu, podpis). ``None`` = nie nasz kształt.

    Kolejność pól i odstępy są tolerowane, bo nagłówek składa cudzy kod; brak któregokolwiek pola
    albo znacznik, który nie jest liczbą, nie jest. Tolerancja kończy się dokładnie tam, gdzie
    zaczyna się zgadywanie.
    """
    parts: dict[str, str] = {}
    for item in (header or "").split(","):
        name, _, value = item.strip().partition("=")
        if name and value:
            parts[name.strip()] = value.strip()
    try:
        timestamp = int(parts["t"])
    except (KeyError, ValueError):
        return None
    digest = parts.get("v1")
    if not digest:
        return None
    return timestamp, digest


def verify_signature(secret: str, body: bytes, header: str, *, now=None) -> bool:
    """Czy nagłówek jest poprawnym podpisem tego ciała tym sekretem, złożonym w oknie tolerancji.

    Oczekiwany podpis liczy :func:`apps.integrations.webhooks.signature_header` – **ta sama**
    funkcja, którą podpisujemy webhooki wychodzące. Porównanie idzie przez
    ``hmac.compare_digest``: zwykłe ``==`` kończy się na pierwszym różnym znaku, więc czas
    odpowiedzi zdradzałby, ile początkowych znaków podpisu zgadł atakujący, a podpis da się
    dobierać w nieskończoność (żądanie nie wymaga żadnego innego poświadczenia).
    """
    parsed = parse_signature(header)
    if parsed is None:
        return False
    timestamp, digest = parsed
    current = int((now or timezone.now()).timestamp())
    if abs(current - timestamp) > TOLERANCE_SECONDS:
        # Powtórka nagranego żądania: podpis się zgadza, bo to ten sam bajt w bajt ładunek.
        # Odrzuca ją wyłącznie znacznik czasu – i dlatego jest on częścią podpisu, a nie dodatkiem.
        return False
    expected = parse_signature(signature_header(secret, body, timestamp))
    return expected is not None and hmac.compare_digest(expected[1], digest)


def payload_digest(body: bytes) -> str:
    """SHA-256 surowych bajtów ciała. Jedyny ślad ładunku, który zostaje w bazie – patrz model."""
    return hashlib.sha256(body).hexdigest()


# --- poświadczenia dostawcy (ekran koordynatora, T42) ----------------------------------------------


def payment_endpoints_for(competition=None):
    """Webhooki płatności tego konkursu – lista do ekranu koordynatora."""
    return list(PaymentEndpoint.objects.for_competition(competition).order_by("provider"))


def endpoint_for(competition, provider: str) -> PaymentEndpoint | None:
    """Aktywny webhook tego konkursu dla tego dostawcy albo ``None``.

    Zawężenie idzie przez ``for_competition``, a nie przez samo ``provider``: ten sam slug
    („blik”, „przelewy24”) ma prawo stać u każdego organizatora, a sekret organizatora A nie ma
    prawa przyjąć wpłaty w konkursie B.
    """
    if not provider:
        return None
    return (
        PaymentEndpoint.objects.for_competition(competition).filter(provider=provider, is_active=True).first()
    )


def create_payment_endpoint(competition, *, provider: str, actor=None, request=None) -> PaymentEndpoint:
    """Zakłada poświadczenie dostawcy wraz z wylosowanym sekretem podpisu.

    Sekret losuje **serwis**, a nie człowiek: sekret wpisany ręcznie jest sekretem o entropii
    hasła, a tutaj jest jedyną przeszkodą między cudzym żądaniem a zapisem wpłaty.
    """
    endpoint = PaymentEndpoint(
        competition=competition,
        provider=(provider or "").strip(),
        secret=generate_secret(),
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )
    endpoint.full_clean(exclude=["created_by"])
    endpoint.save()
    audit(
        actor,
        "payment_endpoint.created",
        endpoint,
        {"provider": endpoint.provider, "competition": endpoint.competition_id},
        request=request,
    )
    logger.info("Założono webhook płatności %s (konkurs %s).", endpoint.provider, endpoint.competition_id)
    return endpoint


def rotate_payment_secret(endpoint: PaymentEndpoint, *, actor=None, request=None) -> PaymentEndpoint:
    """Wymienia sekret podpisu. **Natychmiast i bez okresu przejściowego** – i to jest właściwe.

    Dwa ważne sekrety naraz znaczyłyby, że wyciek trwa dokładnie tak długo, jak okres przejściowy,
    a sekret się wymienia właśnie dlatego, że wyciekł. Ceną jest kilka odrzuconych doręczeń, zanim
    organizator wklei nowy sekret u dostawcy – dostawcy ponawiają, a nieprzyjęta wpłata zostaje
    należnością „do zapłaty”, czyli w stanie, w którym i tak była.

    Do audytu **nie trafia żaden sekret**: ani stary, ani nowy, ani ich skróty. Wpis audytowy
    czyta się w panelu i eksportuje do pliku, więc sekret w nim byłby sekretem w dwóch kolejnych
    miejscach, w których nie ma prawa się znaleźć.
    """
    endpoint.secret = generate_secret()
    endpoint.rotated_at = timezone.now()
    endpoint.save(update_fields=["secret", "rotated_at"])
    audit(
        actor,
        "payment_endpoint.secret_rotated",
        endpoint,
        {"provider": endpoint.provider, "rotated_at": endpoint.rotated_at.isoformat()},
        request=request,
    )
    logger.info("Wymieniono sekret webhooka płatności %s.", endpoint.pk)
    return endpoint


def payment_events_for(competition=None, *, matched=None, limit: int = 50) -> list[PaymentEvent]:
    """Ostatnie doręczenia od dostawców – dziennik do ekranu uzgodnień.

    ``matched=False`` daje dokładnie tę listę, po którą organizator tu przychodzi: potwierdzenia,
    których system nie potrafił dopasować i które ktoś musi rozstrzygnąć ręcznie.
    """
    queryset = PaymentEvent.objects.for_competition(competition).select_related("endpoint", "fee")
    if matched is not None:
        queryset = queryset.filter(matched=matched)
    return list(queryset[:limit])


# --- ładunek --------------------------------------------------------------------------------------


class InvalidPayload(DomainError):
    """400 dla ciała, z którego nie da się wyjąć ani identyfikatora wpłaty, ani klucza doręczenia.

    400, a nie 2xx z ``matched=False``: reguła „zapisz i potwierdź” dotyczy potwierdzeń, których
    nie umiemy **dopasować**, a nie bajtów, których nie umiemy **przeczytać**. Wiersz bez klucza
    doręczenia nie chroniłby przed powtórką i nie dałby organizatorowi czego dopasować, a ponowienie
    tego samego ładunku przez dostawcę i tak skończyłoby się tak samo – to jest błąd adaptera.
    """

    status_code = http.HTTP_400_BAD_REQUEST
    default_code = "INVALID_PAYLOAD"
    default_detail = "Nieprawidłowy ładunek potwierdzenia wpłaty."


def read_confirmation(body: bytes) -> dict:
    """Wyjmuje z ciała to, czego potrzebuje rejestr należności – i **nic więcej**.

    Kształt neutralny wobec dostawcy (D18), pola opcjonalne poza jednym:

    ``reference``
        identyfikator wpłaty, ten sam, który stoi w ``ParticipantFee.external_reference``.
        Wymagany, bo bez niego nie ma czego dopasować.
    ``event_id``
        identyfikator zdarzenia u dostawcy. Gdy jest, to on jest kluczem powtórki – dostawcy
        potrafią przysłać dwa różne zdarzenia o tej samej wpłacie (wpłata i jej zwrot).
    ``status``
        gdy jest, musi brzmieć ``paid``; wszystko inne zapisujemy jako niedopasowane.
    ``paid_at``
        chwila wpłaty w ISO 8601. Brak znaczy „teraz”.
    ``amount``
        kwota **wyłącznie do porównania** z należnością (``record_payment`` zapisze rozbieżność
        w audycie). Nie jest księgowana i nie zostaje w ``PaymentEvent`` – decyzja D15.

    Imiona, adresy, numery rachunków i cokolwiek innego, co dostawca dołoży, są tu **pomijane**:
    nie przepisujemy do bazy danych osobowych, których nie zamówiliśmy.
    """
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidPayload("Ciało żądania nie jest poprawnym JSON-em.") from exc
    if not isinstance(payload, dict):
        raise InvalidPayload("Ciałem żądania ma być obiekt JSON.")
    reference = str(payload.get("reference") or "").strip()
    event_id = str(payload.get("event_id") or "").strip()
    if not reference:
        raise InvalidPayload("Potwierdzenie musi nieść identyfikator wpłaty („reference”).")
    if len(reference) > MAX_REFERENCE_LENGTH or len(event_id) > MAX_REFERENCE_LENGTH:
        raise InvalidPayload(f"Identyfikator dłuższy niż {MAX_REFERENCE_LENGTH} znaków.")
    raw_paid_at = payload.get("paid_at")
    paid_at = None
    if raw_paid_at:
        paid_at = parse_datetime(str(raw_paid_at))
        if paid_at is None:
            raise InvalidPayload("Pole „paid_at” nie jest datą w formacie ISO 8601.")
        if timezone.is_naive(paid_at):
            # Dostawca bez strefy znaczy „mój czas”, a my nie wiemy który – bierzemy czas serwisu.
            # Rejestr odpowiada na pytanie „kiedy”, więc data bez strefy jest tu gorsza niż data
            # przybliżona o strefę: ta pierwsza bywa przesunięta o godzinę i nikt tego nie widzi.
            paid_at = timezone.make_aware(paid_at, timezone.get_current_timezone())
    return {
        "reference": reference,
        "event_id": event_id,
        "status": str(payload.get("status") or PAID_STATUS).strip().lower(),
        "paid_at": paid_at,
        "amount": payload.get("amount"),
    }


# --- widok ----------------------------------------------------------------------------------------


def _not_found() -> DomainError:
    """404 w kształcie błędu domenowego – ten sam, co wszędzie pod ``/api/v1/``."""
    return DomainError("Nie znaleziono zasobu.", "NOT_FOUND", http.HTTP_404_NOT_FOUND)


def _unauthorized() -> DomainError:
    """401 dla żądania bez poprawnego podpisu. Jedna treść dla wszystkich powodów odmowy.

    Rozróżnienie „brak nagłówka”, „zły kształt”, „po tolerancji” i „zły podpis” byłoby wyrocznią:
    pozwalałoby dobierać podpis, mając na każdym kroku informację, jak blisko jest się celu.
    """
    return DomainError("Nieprawidłowy podpis żądania.", "INVALID_SIGNATURE", http.HTTP_401_UNAUTHORIZED)


def apply_confirmation(endpoint: PaymentEndpoint, confirmation: dict, *, request=None):
    """Próbuje zapisać wpłatę. Zwraca parę (należność albo ``None``, powód niedopasowania).

    Każdy powód kończy się **tak samo** dla dostawcy – 2xx – a różni się wyłącznie tym, co zobaczy
    organizator na ekranie uzgodnień. ``DomainError`` z ``record_payment`` (należność już zapłacona
    innym identyfikatorem, należność zwolniona albo umorzona) jest tu **odpowiedzią**, a nie
    awarią: to są sytuacje dla człowieka, a nie dla automatu, który miałby je po cichu nadpisać.
    """
    if confirmation["status"] != PAID_STATUS:
        return None, UNMATCHED_IGNORED_STATUS
    fee = find_fee_by_reference(endpoint.competition, confirmation["reference"])
    if fee is None:
        return None, UNMATCHED_UNKNOWN_REFERENCE
    try:
        record_payment(
            fee,
            external_reference=confirmation["reference"],
            paid_at=confirmation["paid_at"],
            amount=confirmation["amount"],
            request=request,
        )
    except DomainError as exc:
        reason = exc.machine_code if exc.machine_code in UNMATCHED_REASONS else UNMATCHED_IGNORED_STATUS
        logger.info("Doręczenie płatności %s niedopasowane: %s.", endpoint.provider, reason)
        # Należność **zostaje** przy wierszu także wtedy, gdy wpłaty nie zapisaliśmy: organizator
        # uzgadniający nadpłatę potrzebuje wiedzieć, o którą należność dostawcy chodziło.
        return fee, reason
    return fee, ""


def record_event(
    endpoint: PaymentEndpoint,
    confirmation: dict,
    *,
    delivery_key: str,
    body: bytes,
    fee=None,
    reason: str = "",
    request=None,
) -> PaymentEvent:
    """Zapisuje ślad doręczenia. Kolizja klucza znaczy równoległą powtórkę, a nie błąd.

    Dwa doręczenia tego samego zdarzenia potrafią wejść równolegle (dostawca ponawia, bo pierwsza
    próba jeszcze się nie skończyła). Więz unikalności rozstrzyga to w bazie, a przegrany oddaje
    wiersz zwycięzcy – wpłata jest już zapisana, bo ``record_payment`` jest idempotentny po
    identyfikatorze wpłaty. Dlatego wyścig kończy się jednym wierszem i jedną wpłatą, a nie
    wyjątkiem w odpowiedzi dla dostawcy.
    """
    try:
        with transaction.atomic():
            event = PaymentEvent.objects.create(
                endpoint=endpoint,
                delivery_key=delivery_key,
                external_reference=confirmation["reference"],
                payload_hash=payload_digest(body),
                matched=not reason,
                unmatched_reason=reason,
                fee=fee,
            )
    except IntegrityError:
        return PaymentEvent.objects.get(endpoint=endpoint, delivery_key=delivery_key)
    PaymentEndpoint.objects.filter(pk=endpoint.pk).update(last_event_at=event.received_at)
    # Audyt bez kwoty, bez identyfikatora wpłaty i bez ładunku: wpis audytowy czyta się w panelu
    # i eksportuje do pliku, a kwota razem z tytułem przelewu prowadzi wprost do osoby. Co się
    # stało z pieniędzmi, zapisuje ``fee.payment_recorded`` z ``record_payment``.
    audit(
        None,
        "payment_webhook.received",
        event,
        {"provider": endpoint.provider, "matched": event.matched, "reason": reason},
        request=request,
    )
    return event


class PaymentWebhookView(APIView):
    """Odbiór potwierdzenia płatności. **Bez integracji z jakimkolwiek dostawcą.**

    Kolejność odmów jest częścią kontraktu i wynika z § 3: najpierw „czy ten konkurs w ogóle pobiera
    wpisowe” (404 – u Konkursu #1 pod tym adresem nie ma niczego i nie pada ani jedno zapytanie),
    potem „czy ten dostawca jest tu skonfigurowany” (404), a dopiero na końcu podpis (401). Slug
    dostawcy zdradza się w ten sposób nieuwierzytelnionemu pytającemu – i tak ma być: inaczej
    trzeba by liczyć podpis nieistniejącym sekretem, czyli udawać, że coś tu stoi, a organizator
    diagnozujący literówkę w adresie nie miałby jak jej znaleźć.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "payments"

    @extend_schema(
        tags=["Integracje"],
        summary="Potwierdzenie wpłaty od dostawcy płatności",
        description=(
            "Adres przyjmujący potwierdzenia wpłat. Podpis: `X-Olimpiada-Signature: "
            "t=<unix>,v1=<hex>`, HMAC-SHA256 z sekretu webhooka i surowych bajtów ciała, "
            "tolerancja 300 s. Szczegóły w `docs/API.md` § 3.5."
        ),
        parameters=[
            OpenApiParameter(
                name="provider",
                location=OpenApiParameter.PATH,
                type=str,
                description="Identyfikator dostawcy nadany przez organizatora.",
            )
        ],
        request=None,
        responses={200: None},
    )
    def post(self, request, provider: str):
        competition = competition_of(request)
        if not fees_enabled(competition):
            # Flaga czyta się z wiersza konkursu, który warstwa ma już w ręku – bez tej odmowy
            # Konkurs #1 wykonywałby zapytanie o poświadczenie dostawcy, którego nigdy nie założy.
            raise _not_found()
        endpoint = endpoint_for(competition, provider)
        if endpoint is None:
            raise _not_found()
        # Surowe bajty **przed** czymkolwiek innym: po dotknięciu ``request.data`` strumień jest
        # już wyczerpany, a podpis liczy się z bajtów, nie z odtworzonego JSON-a (§ 1.5.1, reguła 2).
        body = request.body
        if not verify_signature(endpoint.secret, body, request.META.get(SIGNATURE_META, "")):
            logger.warning("Odrzucono doręczenie płatności %s: podpis nie zgadza się.", endpoint.pk)
            raise _unauthorized()
        confirmation = read_confirmation(body)
        delivery_key = confirmation["event_id"] or confirmation["reference"]
        existing = PaymentEvent.objects.filter(endpoint=endpoint, delivery_key=delivery_key).first()
        if existing is not None:
            # Powtórka. Nie zapisujemy drugiego wiersza, nie ruszamy ``paid_at`` i nie dopisujemy
            # drugiego wpisu audytowego – dostawca ponawia po każdym timeoucie, a dziennik ma
            # odpowiadać „co przyszło”, a nie „ile razy sieć się zacięła”.
            logger.info("Powtórzone doręczenie płatności %s (%s).", endpoint.pk, delivery_key)
            return Response({"matched": existing.matched})
        fee, reason = apply_confirmation(endpoint, confirmation, request=request)
        event = record_event(
            endpoint,
            confirmation,
            delivery_key=delivery_key,
            body=body,
            fee=fee,
            reason=reason,
            request=request,
        )
        return Response({"matched": event.matched})
