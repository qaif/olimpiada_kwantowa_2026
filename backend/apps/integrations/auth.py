"""Uwierzytelnienie kluczem API: rozpoznanie klucza, sprawdzenie zakresu i limit żądań.

Trzy klasy, bo w DRF to trzy różne pytania i trzy różne odpowiedzi HTTP:

- ``ApiKeyAuthentication`` – **kim jesteś** (401, gdy nagłówka nie ma albo klucz nie istnieje),
- ``HasScope`` – **czy wolno ci to** (403, gdy klucz jest prawdziwy, ale bez zakresu),
- ``ApiKeyRateThrottle`` – **czy nie za często** (429, z nagłówkiem ``Retry-After``).

Rozdzielenie nie jest formalnością: partner, który dostaje 403 zamiast 401, wie, że klucz działa
i że trzeba poprosić o szerszą umowę, a nie że przekręcił poświadczenie.

Czwarte pytanie dokłada wielokonkursowość: **czy to twoje drzwi**. Klucz wystawiony w konkursie A,
użyty pod domeną konkursu B, dostaje **404** (``ForeignApiKey``), a nie 401 ani 403 – tak samo,
jak zasób spoza edycji objętej kluczem (``apps.integrations.api``). Istnienie konkursu B nie jest
informacją partnera konkursu A, więc odpowiedź ma brzmieć „nie ma tego tutaj”.

Żądanie z kluczem **nie ma użytkownika**: ``request.user`` zostaje anonimowy, a cała tożsamość
żądania siedzi w ``request.auth`` (obiekt ``ApiKey``). Podstawienie tu konta koordynatora –
kuszące, bo „ktoś ten klucz wystawił” – znaczyłoby, że każdy widok pytający o rolę użytkownika
wpuszcza serwer partnera tak samo jak człowieka przy klawiaturze.
"""

from __future__ import annotations

import hmac
import logging
import time

from django.core.cache import cache
from django.utils import timezone
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework import status as http
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated
from rest_framework.permissions import BasePermission
from rest_framework.throttling import BaseThrottle

from apps.competitions.scoping import competition_of
from apps.core.api import DomainError

from .models import SCOPES, TOKEN_PREFIX, ApiKey, hash_secret

logger = logging.getLogger(__name__)

#: Nagłówek i schemat. ``Bearer``, a nie własny schemat, bo to jedyny, który klienty HTTP,
#: bramki i narzędzia diagnostyczne obsługują bez konfiguracji.
AUTH_SCHEME = "Bearer"

#: Jak często wolno odświeżyć ``last_used_at`` (sekundy). Znacznik odpowiada na pytanie „czy ten
#: klucz jeszcze żyje”, a nie „ile było żądań”, więc minutowa rozdzielczość w zupełności starcza,
#: a zapis przy każdym żądaniu byłby UPDATE-em na gorącym wierszu przy każdym odczycie.
LAST_USED_REFRESH_SECONDS = 60


class InvalidApiKey(AuthenticationFailed):
    """401 z maszynowym kodem. Treść jest ta sama dla „nie ma takiego klucza” i „zły sekret”.

    Rozróżnienie byłoby wyrocznią: pozwalałoby po odpowiedziach ustalić, które przedrostki
    istnieją w systemie, a przedrostek jest jawną częścią klucza.
    """

    default_code = "INVALID_API_KEY"
    default_detail = "Nieprawidłowy klucz API."


class RevokedApiKey(AuthenticationFailed):
    """401 dla klucza, który istniał i został unieważniony.

    Tutaj rozróżnienie jest **wskazane**: partner ma się dowiedzieć, że jego klucz odwołano,
    a nie szukać literówki w konfiguracji. Nic to nie zdradza – wiedzę o istnieniu tego klucza
    ma już z faktu, że go używa.
    """

    default_code = "API_KEY_REVOKED"
    default_detail = "Ten klucz API został unieważniony."


class ForeignApiKey(DomainError):
    """404 dla klucza konkursu A użytego pod adresem konkursu B.

    404, a nie 403 – i to jest ta sama reguła, którą ten moduł stosuje już do edycji spoza
    zakresu klucza (``apps.integrations.api``, „czego klucz nie obejmuje, tego nie ma”). Partner
    organizatora A nie ma prawa dowiedzieć się z kodu odpowiedzi, że pod tym adresem w ogóle stoi
    jakiś konkurs; 403 („jesteś, ale nie tobie”) potwierdzałoby jego istnienie i pozwalało
    przejechać listę domen instalacji jednym kluczem.

    Odmowa jest **uwierzytelnieniem**, a nie uprawnieniem, bo dotyczy samego poświadczenia: ten
    klucz nie jest kluczem do tych drzwi, niezależnie od tego, jakie zakresy ma wypisane.
    """

    status_code = http.HTTP_404_NOT_FOUND
    default_code = "NOT_FOUND"
    default_detail = "Nie znaleziono zasobu."


class ApiKeyRequired(NotAuthenticated):
    """401 dla żądania **bez** klucza – z nagłówkiem ``WWW-Authenticate``.

    Osobno od 403 „klucz bez zakresu” i to rozróżnienie jest tu całym sensem: brak poświadczenia
    znaczy „zaloguj się kluczem”, a brak zakresu – „twój klucz działa, ale nie na to”. Klasa jest
    podklasą ``NotAuthenticated``, bo DRF dokłada nagłówek ``WWW-Authenticate`` wyłącznie wtedy,
    gdy wyjątek jest tego rodzaju – a bez tego nagłówka klient HTTP nie wie, czym ma się
    przedstawić.
    """

    default_code = "API_KEY_REQUIRED"
    default_detail = "Ten zasób wymaga klucza API organizatora."


def parse_token(raw: str) -> tuple[str, str] | None:
    """Rozbija ``ok_<prefix>_<sekret>`` na parę (przedrostek, sekret). ``None`` = nie nasz format.

    Sekret może zawierać znaki ``-`` i ``_`` (base64url), więc dzielimy **dokładnie dwa razy** od
    lewej: trzeci podział urwałby sekret w miejscu pierwszego podkreślenia i klucz co kilkanaście
    wystawień przestawałby działać bez żadnego wzorca.
    """
    parts = (raw or "").split("_", 2)
    if len(parts) != 3:
        return None
    marker, prefix, secret = parts
    if marker != TOKEN_PREFIX or not prefix or not secret:
        return None
    return prefix, secret


def _touch(key: ApiKey) -> None:
    """Odświeża ``last_used_at`` nie częściej niż raz na ``LAST_USED_REFRESH_SECONDS``."""
    marker = f"integrations:key-used:{key.pk}"
    if cache.get(marker) is not None:
        return
    cache.set(marker, 1, LAST_USED_REFRESH_SECONDS)
    now = timezone.now()
    ApiKey.objects.filter(pk=key.pk).update(last_used_at=now)
    key.last_used_at = now


class ApiKeyAuthentication(BaseAuthentication):
    """``Authorization: Bearer ok_<prefix>_<sekret>`` → obiekt ``ApiKey`` w ``request.auth``.

    Brak nagłówka zwraca ``None``, a nie błąd – i to jest celowe. Odmowę wystawia dopiero
    uprawnienie (``ApiKeyRequired``, 401 z ``WWW-Authenticate``), bo dopiero ono wie, czy widok
    w ogóle wymaga klucza. Uwierzytelnienie odpowiada wyłącznie na pytanie „czy ten nagłówek
    jest poprawny”, a nie „czy wolno tu wejść”.
    """

    keyword = AUTH_SCHEME

    def authenticate(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header:
            return None
        scheme, _, raw = header.partition(" ")
        if scheme.lower() != self.keyword.lower():
            return None
        parsed = parse_token(raw.strip())
        if parsed is None:
            raise InvalidApiKey()
        prefix, secret = parsed
        key = ApiKey.objects.select_related("competition", "edition").filter(prefix=prefix).first()
        if key is None:
            raise InvalidApiKey()
        # Porównanie w stałym czasie: zwykłe ``==`` kończy się na pierwszym różnym bajcie,
        # więc czas odpowiedzi zdradzałby, ile początkowych znaków skrótu się zgadza.
        if not hmac.compare_digest(key.key_hash, hash_secret(secret)):
            raise InvalidApiKey()
        if not key.is_active:
            raise RevokedApiKey()
        # Konkurs rozstrzyga się **po** sprawdzeniu sekretu, a nie przez zawężenie wyszukiwania
        # klucza. Gdyby wchodził do filtra, klucz z cudzego konkursu byłby nie do odróżnienia od
        # klucza nieistniejącego – czyli dałby 401 („przekręciłeś poświadczenie”) zamiast 404
        # („tego tu nie ma”), i partner szukałby literówki w konfiguracji zamiast adresu.
        if not key.covers_competition(competition_of(request)):
            logger.info(
                "Klucz API %s (konkurs %s) użyty pod adresem innego konkursu.",
                key.prefix,
                key.competition_id,
            )
            raise ForeignApiKey()
        _touch(key)
        # Użytkownik zostaje anonimowy – tożsamością żądania jest klucz, patrz docstring modułu.
        from django.contrib.auth.models import AnonymousUser

        return (AnonymousUser(), key)

    def authenticate_header(self, request) -> str:
        return self.keyword


class ApiKeyScheme(OpenApiAuthenticationExtension):
    """Opis poświadczenia dla drf-spectacular.

    Bez tego generator nie umiałby opisać naszej klasy i dokładałby ostrzeżenie do każdego
    endpointu ``/api/v1/`` – a liczba ostrzeżeń schematu jest w tym projekcie pilnowana.
    """

    target_class = "apps.integrations.auth.ApiKeyAuthentication"
    name = "ApiKey"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "ok_<prefix>_<secret>",
            "description": "Klucz API organizatora. Zakresy: " + ", ".join(SCOPES) + ".",
        }


class ScopePermission(BasePermission):
    """Baza uprawnienia zakresowego. Podklasy ustawiają ``scope`` – składa je :func:`HasScope`."""

    scope = ""

    def has_permission(self, request, view) -> bool:
        key = getattr(request, "auth", None)
        if not isinstance(key, ApiKey):
            raise ApiKeyRequired()
        if not key.has_scope(self.scope):
            raise DomainError(
                f"Klucz nie ma zakresu „{self.scope}”.",
                "MISSING_SCOPE",
                http.HTTP_403_FORBIDDEN,
            )
        return True


def HasScope(scope: str) -> type[ScopePermission]:  # noqa: N802 - czytamy to jako klasę uprawnienia
    """Uprawnienie „klucz ma zakres X”, zapisywane w widoku jako ``HasScope("read:results")``.

    Fabryka, a nie klasa z argumentem, bo DRF wkłada do ``permission_classes`` **klasy** i sam je
    tworzy bez argumentów. Zakres podany obok, w osobnym atrybucie widoku, dałoby się przeoczyć
    przy kopiowaniu widoku – tutaj jest w jedynym miejscu, w którym widać uprawnienie.
    """
    if scope not in SCOPES:  # pragma: no cover - błąd programisty, nie danych
        raise ValueError(f"Nieznany zakres klucza API: {scope}.")
    return type(f"HasScope_{scope.replace(':', '_')}", (ScopePermission,), {"scope": scope})


class HasApiKey(BasePermission):
    """Sam ważny klucz, bez dodatkowego zakresu – dla katalogu edycji i etapów.

    Katalog nie niesie niczego, czego nie ma na publicznych stronach serwisu (nazwa edycji,
    terminy etapów), więc osobny zakres byłby biurokracją: partner i tak musi go odczytać, żeby
    wiedzieć, o które identyfikatory pytać w zasobach chronionych zakresami.
    """

    def has_permission(self, request, view) -> bool:
        if not isinstance(getattr(request, "auth", None), ApiKey):
            raise ApiKeyRequired()
        return True


class ApiKeyRateThrottle(BaseThrottle):
    """Limit żądań na minutę, liczony **per klucz** z jego własnego pola ``rate_limit_per_minute``.

    Własna implementacja, a nie ``SimpleRateThrottle``: tamta bierze stawkę ze słownika ustawień
    po nazwie scope'u, czyli jedną dla wszystkich. Tutaj limit jest atrybutem klucza – partner
    synchronizujący co minutę i partner odpytujący raz dziennie nie mają powodu dzielić tej samej
    liczby, a zmiana limitu jednemu z nich nie może być zmianą ustawień serwisu.

    Okno jest **kalendarzowe** (pełna minuta zegara), a nie przesuwane: licznik w cache'u kosztuje
    jedno ``incr``, podczas gdy okno przesuwane wymaga listy znaczników czasu na każdy klucz.
    Ceną jest to, że na styku dwóch minut da się wysłać dwa limity pod rząd – przy limicie
    rzędu stu żądań to różnica bez znaczenia dla obciążenia.
    """

    def _window(self) -> int:
        return int(time.time() // 60)

    def allow_request(self, request, view) -> bool:
        key = getattr(request, "auth", None)
        if not isinstance(key, ApiKey):
            return True
        limit = key.rate_limit_per_minute or 0
        if limit <= 0:  # pragma: no cover - constraint w bazie nie dopuszcza zera
            return True
        cache_key = f"integrations:rate:{key.pk}:{self._window()}"
        # ``add`` zakłada licznik tylko wtedy, gdy go nie ma – dwa równoległe żądania nie wyzerują
        # sobie nawzajem okna. Ważność 120 s, żeby klucz z poprzedniej minuty zniknął sam.
        cache.add(cache_key, 0, 120)
        try:
            used = cache.incr(cache_key)
        except ValueError:  # pragma: no cover - wpis wygasł między ``add`` a ``incr``
            cache.set(cache_key, 1, 120)
            used = 1
        if used > limit:
            logger.info("Klucz API %s przekroczył limit %s żądań na minutę.", key.prefix, limit)
            return False
        return True

    def wait(self) -> float:
        """Ile sekund do początku następnego okna – trafia do nagłówka ``Retry-After``."""
        return max(1.0, 60.0 - (time.time() % 60))
