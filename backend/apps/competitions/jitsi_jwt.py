"""Przepustki platformy do własnego Jitsi: token JWT (HS256) na **jeden** pokój, wystawiany przy wejściu.

Do v0.38 własne Jitsi (``meet.<domena>``) było otwarte: każdy, kto otworzył
``meet.<domena>/<cokolwiek>``, zakładał pokój i w nim rozmawiał, a jedyną ochroną pokoju rozmowy
była losowa końcówka nazwy (``apps.competitions.video``). Od v0.39.0 („wariant A”, decyzja
właściciela) Prosody przyjmuje wyłącznie token podpisany sekretem, który zna platforma
(``deploy/jitsi/docker-compose.jitsi.yml``), a token powstaje **w chwili kliknięcia „Dołącz”**
w panelu – po sprawdzeniu, kim jest klikający i czy wolno mu do tego pokoju teraz wejść.

Czego ten moduł pilnuje i dlaczego:

- **jeden pokój na token.** Claim ``room`` jest dokładną nazwą pokoju, nigdy ``*``. Prosody
  (``mod_token_verification``) porównuje go z pokojem, do którego ktoś wchodzi, i przy różnicy
  odmawia („room-mismatch”) – sprawdzone na obrazie stable-11031, także dla pary
  ``pokój`` / ``pokój-test`` (docs/OPERACJE.md § 25.6). Przepustka na próbę sprzętu nie otwiera
  więc rozmowy i odwrotnie,
- **krótko i od-do.** ``nbf``/``exp`` wynikają z terminu rozmowy (``interview_window``), a nie
  z chwili kliknięcia: przepustka wystawiona kwadrans przed rozmową nie działa tydzień później.
  Prosody sprawdza ``nbf`` bez żadnej tolerancji zegara, więc cofamy go o
  :data:`CLOCK_SKEW_SECONDS` – inaczej serwer Jitsi spieszący się o sekundę odrzucałby świeży token,
- **moderator wyłącznie z tokenu.** ``context.user.moderator = true`` dostaje komisja (koordynator,
  gospodarz pokoju), nigdy uczestnik. Moderatora nadaje ``mod_token_affiliation``; jicofo ma
  wyłączone i „pierwszy zostaje moderatorem”, i własne uwierzytelnianie, które nadawało
  właściciela każdemu z tokenem (sprawdzone – opis w pliku compose),
- **żadnych danych osobowych ponad nazwę wyświetlaną.** Bez e-maila, awatara i identyfikatora
  konta: token czyta każdy, kto ma go w ręku (podpis to nie szyfrowanie), a w pokoju i tak widać
  wyłącznie nazwę,
- **token nie jest nigdzie zapisywany.** Ani w bazie, ani w liście, ani w logu: powstaje w widoku,
  trafia do nagłówka ``Location`` odpowiedzi 302 (``Cache-Control: no-store``) i tyle. Do adresu
  pokoju dokładamy go we **fragmencie** (``#jwt="…"``), a nie w zapytaniu (``?jwt=…``): fragment
  nie jedzie do serwera, więc nie ląduje w logu nginx-a w ``jitsi-web`` ani w ``Referer``.
  Front Jitsi stable-11031 czyta najpierw fragment, potem zapytanie (sprawdzone w paczce
  ``app.bundle.min.js`` i w przeglądarce: ``parseJWTFromURLParams``). Wartość we fragmencie jest
  JSON-em, stąd cudzysłowy (``%22``).

Podpis HS256 liczymy biblioteką standardową (``hmac`` + ``hashlib``), a nie PyJWT. PyJWT jest
w środowisku wyłącznie przechodnio (przez ``django-allauth``), a tutaj potrzebne jest samo
**wystawienie** tokenu o stałym kształcie – dwadzieścia linijek, które da się przeczytać w całości,
zamiast nowej zależności deklarowanej po to, żeby wołać z niej jedną funkcję. Weryfikacji tokenów
platforma nie robi wcale (robi ją Prosody), więc pułapki bibliotek weryfikujących (``alg: none``,
pomylenie kluczy) tego kodu nie dotyczą. Testy sprawdzają podpis niezależną implementacją – PyJWT,
o ile jest zainstalowane.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from datetime import timedelta
from urllib.parse import quote, unquote, urlsplit

from django.conf import settings
from django.utils import timezone

#: Najkrótszy sekret, przy którym platforma w ogóle wystawia tokeny. 32 znaki – ten sam próg, co
#: przy pozostałych sekretach współdzielonych (``cms.W010``, ``cms.W013``); ``deploy_jitsi.sh``
#: generuje 64 znaki z ``[A-Za-z0-9]``.
SECRET_MIN_LENGTH = 32

#: Ile sekund cofamy ``nbf`` (patrz docstring modułu – Prosody nie ma tolerancji zegara).
CLOCK_SKEW_SECONDS = 60

#: Dopuszczalna nazwa pokoju w claimie ``room``. Nazwy generowane przez platformę są węższe
#: (``[a-z0-9-]``, ``apps.competitions.video``); tu dopuszczamy jeszcze kropkę i podkreślnik dla
#: pokoi wpisanych ręcznie przy terminie. Wszystko inne (ukośnik = tenant, ``*``, spacje) nie jest
#: pokojem, do którego platforma wystawi przepustkę.
_ROOM_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")


def app_secret() -> str:
    return getattr(settings, "JITSI_JWT_APP_SECRET", "") or ""


def jwt_enabled() -> bool:
    """Czy platforma wystawia przepustki. Sekret krótszy niż próg działa jak pusty (``competitions.W001``)."""
    return len(app_secret()) >= SECRET_MIN_LENGTH


def jwt_host() -> str:
    """Host własnego Jitsi, dla którego wystawiamy tokeny – małymi literami, bez portu i kropki końcowej."""
    return _normalise_host(getattr(settings, "JITSI_JWT_HOST", "") or "")


def _normalise_host(value: str) -> str:
    return (value or "").strip().lower().partition(":")[0].rstrip(".")


def room_of(url: str) -> str:
    """Nazwa pokoju z adresu ``https://meet.<domena>/<pokój>`` albo ``""``, gdy adres nie jest pokojem.

    Wyłącznie **jeden** segment ścieżki: ``/tenant/pokój`` to w Jitsi pokój w innym tenancie,
    a token na „pokój” do niego nie wpuści – lepiej nie wystawiać go wcale. Małe litery, bo tak
    nazwę pokoju widzi Prosody (i tak porównuje claim – sprawdzone).
    """
    try:
        path = urlsplit((url or "").strip()).path
    except ValueError:
        return ""
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) != 1:
        return ""
    room = unquote(segments[0]).lower()
    return room if _ROOM_PATTERN.fullmatch(room) else ""


def is_platform_room(url: str) -> bool:
    """Czy do tego pokoju wchodzi się przepustką platformy (a nie zwykłym linkiem).

    Rozstrzyga **adres pokoju**, nie ustawienie etapu: link wpisany ręcznie przy terminie, który
    prowadzi na nasze Jitsi, też wymaga przepustki (bez niej zamknięte Jitsi nikogo nie wpuści),
    a link do BBB uczelni w etapie z naszym Jitsi jako dostawcą – nie (pokazujemy go jak dotąd).
    Dla pokoi generowanych z ``video_base_url`` etapu to jest dokładnie warunek „host adresu
    serwera etapu równy ``JITSI_JWT_HOST``”.
    """
    if not jwt_enabled() or not url:
        return False
    try:
        host = urlsplit(url.strip()).hostname or ""
    except ValueError:
        return False
    return bool(host) and _normalise_host(host) == jwt_host() and bool(room_of(url))


def room_url(room: str) -> str:
    """Adres pokoju o tej nazwie na naszym Jitsi (pokoje koordynatora nie mają zapisanego adresu)."""
    return f"https://{jwt_host()}/{room}"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def encode_hs256(claims: dict, secret: str) -> str:
    """``base64url(nagłówek).base64url(treść).base64url(HMAC-SHA256)`` – RFC 7519 w najprostszej postaci."""
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(claims, separators=(",", ":"), ensure_ascii=False).encode())
    signing_input = f"{header}.{body}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header}.{body}.{_b64(signature)}"


def claims_for(
    room: str,
    *,
    not_before,
    expires_at,
    display_name: str = "",
    moderator: bool = False,
) -> dict:
    """Komplet claimów jednej przepustki. Osobno od podpisu, żeby testy czytały kształt wprost.

    - ``aud`` – ``JITSI_JWT_AUDIENCE`` (Prosody: ``JWT_ACCEPTED_AUDIENCES``),
    - ``iss`` – identyfikator aplikacji (Prosody: ``JWT_APP_ID`` i ``JWT_ACCEPTED_ISSUERS``),
    - ``sub`` – domena XMPP instancji,
    - ``room`` – dokładna nazwa pokoju, nigdy ``*``,
    - ``iat``/``nbf``/``exp`` – sekundy epoki; ``nbf`` cofnięte o :data:`CLOCK_SKEW_SECONDS`,
    - ``context.user`` – wyłącznie ``name`` (gdy jest) i ``moderator: true`` (gdy ma być). Klucza
      ``moderator`` przy uczestniku **nie ma wcale**, a nie ``false``: ``mod_token_affiliation``
      sprawdza wartość prawdziwą, więc brak klucza i ``false`` znaczą to samo, a mniej pól to mniej
      okazji do pomyłki przy następnej zmianie tego modułu.
    """
    if not _ROOM_PATTERN.fullmatch(room or ""):
        raise ValueError("Nazwa pokoju nie nadaje się do przepustki (pusta, z ukośnikiem albo '*').")
    if expires_at <= not_before:
        raise ValueError("Przepustka musi wygasać po chwili, od której obowiązuje.")
    user: dict = {}
    if display_name:
        user["name"] = display_name
    if moderator:
        user["moderator"] = True
    return {
        "aud": settings.JITSI_JWT_AUDIENCE,
        "iss": settings.JITSI_JWT_APP_ID,
        "sub": settings.JITSI_JWT_SUBJECT,
        "room": room,
        "iat": int(timezone.now().timestamp()),
        "nbf": int((not_before - timedelta(seconds=CLOCK_SKEW_SECONDS)).timestamp()),
        "exp": int(expires_at.timestamp()),
        "context": {"user": user},
    }


def issue(
    room: str,
    *,
    not_before,
    expires_at,
    display_name: str = "",
    moderator: bool = False,
) -> str:
    """Podpisana przepustka na pokój ``room``. Wołający odpowiada za to, **komu** ją daje."""
    if not jwt_enabled():
        raise RuntimeError("Przepustki Jitsi są wyłączone (JITSI_JWT_APP_SECRET pusty albo za krótki).")
    claims = claims_for(
        room,
        not_before=not_before,
        expires_at=expires_at,
        display_name=display_name,
        moderator=moderator,
    )
    return encode_hs256(claims, app_secret())


def join_url(url: str, token: str) -> str:
    """Adres pokoju z przepustką we fragmencie (``#jwt="…"``) – uzasadnienie w docstringu modułu."""
    base = (url or "").strip().split("#", 1)[0]
    return f"{base}#jwt={quote(json.dumps(token), safe='')}"


def short_name(user) -> str:
    """Nazwa w pokoju: imię i inicjał nazwiska („Jan K.”). Pusta, gdy konto nie ma imienia.

    Mniej, niż widzi komisja na ekranie terminów (pełne nazwisko, e-mail, kod), bo nazwę w pokoju
    widzą **wszyscy** w pokoju – także drugi uczestnik terminu o kilku miejscach.
    """
    first = (getattr(user, "first_name", "") or "").strip()
    last = (getattr(user, "last_name", "") or "").strip()
    if not first:
        return ""
    return f"{first} {last[0]}." if last else first


# --- okna czasowe -------------------------------------------------------------------------------


def lead() -> timedelta:
    return timedelta(minutes=max(0, settings.JITSI_JWT_LEAD_MINUTES))


def grace() -> timedelta:
    return timedelta(minutes=max(0, settings.JITSI_JWT_GRACE_MINUTES))


def precheck_lifetime() -> timedelta:
    return timedelta(minutes=max(1, settings.JITSI_JWT_PRECHECK_MINUTES))


def session_lifetime() -> timedelta:
    return timedelta(minutes=max(1, settings.JITSI_JWT_SESSION_MINUTES))


def gateway_lifetime() -> timedelta:
    return timedelta(minutes=max(1, settings.JITSI_JWT_GATEWAY_MINUTES))


def interview_window(slot) -> tuple:
    """Od kiedy do kiedy wolno wejść do pokoju rozmowy: ``LEAD`` przed początkiem, ``GRACE`` po końcu.

    Zapas po końcu nie jest grzecznością: przepustka jest sprawdzana także przy **ponownym**
    połączeniu (zmiana sieci, uśpiony laptop), więc rozmowa, która przeciągnęła się o kwadrans,
    nie może skończyć się na tym, że uczestnik po zerwaniu łącza już nie wróci.
    """
    return slot.starts_at - lead(), slot.ends_at + grace()
