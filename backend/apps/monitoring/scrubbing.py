"""Filtr danych osobowych dla zdarzeń wysyłanych do GlitchTipa (OPS-02 § 2).

Moduł **nie importuje** ``sentry_sdk`` – to czysta funkcja na słowniku zdarzenia. Dzięki temu
testy sprawdzają filtr bez klienta, a instalacja bez ``SENTRY_DSN`` nie ładuje niczego.

Zasada jest odwrotna niż w domyślnym filtrze Sentry: nie „usuń to, co znamy jako wrażliwe”, tylko
„zostaw to, czego potrzebujemy do naprawy błędu”. Serwis przetwarza dane osób niepełnoletnich,
paszporty, dane o zdrowiu i diecie (LOG-01) – lista wrażliwych pól w kodzie będzie zawsze o krok
za kodem, który je dokłada. Dlatego:

- z ``request`` zostaje metoda, nagłówki z **listy dozwolonych** i adres, w którym ścieżka jest
  **wzorcem trasy** Django (``/reset/{uidb64}/{token}/`` – z ``transaction`` o źródle ``route``),
  a gdy trasy nie ma (404) – ścieżką z zamaskowanymi segmentami (:func:`mask_path`); treść żądania,
  ciasteczka, zapytanie i adres IP znikają zawsze,
- ``user`` znika w całości – także identyfikator, bo konto ucznia jest kontem osoby
  niepełnoletniej, a identyfikator łączy zdarzenie z osobą w bazie,
- zmienne lokalne ramek znikają (klient i tak ich nie zbiera – ``include_local_variables=False``),
- w ``extra``, ``contexts``, danych okruszków i parametrach logu klucz, którego nazwa zawiera
  któryś z :data:`SENSITIVE_KEY_PARTS`, dostaje ``[Filtered]``,
- **każdy** napis zdarzenia przechodzi przez :func:`scrub_text` (e-mail, PESEL, telefon, adresy IP,
  tokeny w adresach – w zapytaniu **i w ścieżce**, ``Bearer``, JWT, ``DETAIL:``/``Failing row
  contains`` z błędów Postgresa),
- okruszki Redisa: wyłącznie nazwa polecenia (klucz cache'u bywa adresem IP albo e-mailem).

Wyrażenia są liniowe: napis jest najpierw przycinany (:data:`MAX_TEXT`), a każde wyrażenie zaczyna
dopasowanie wyłącznie na granicy „ciągu” (negatywne spojrzenie wstecz) i ma ograniczone powtórzenia –
spreparowany komunikat (``"a-" * 4000``) nie zatrzyma wątku wysyłki (test czasu w testach).

Nadmiarowe filtrowanie jest tu tanie (gorzej czytelny komunikat), niedomiarowe – nie.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

FILTERED = "[Filtered]"

#: Najdłuższy napis, który przepuszczamy przez wyrażenia; reszta jest ucinana. Komunikat dłuższy niż
#: 2 KB nic nie dodaje do naprawy błędu, a długość wejścia jest jedynym parametrem kosztu filtrów.
MAX_TEXT = 2048

#: Fragmenty nazw kluczy, których wartości nie wolno wysłać. Dopasowanie po fragmencie, bez względu
#: na wielkość liter: ``passport_number``, ``guardianEmail``, ``health_notes``, ``birth_date``…
#: ``args``/``kwargs`` – argumenty zadań Celery (``extra["celery-job"]``) bywają adresem e-mail albo
#: identyfikatorem osoby; ``data``/``body`` – treść żądania pod inną nazwą; ``argv`` – linia poleceń
#: procesu (``manage.py … --email …``); ``query``/``fragment`` – części adresu z tokenami; ``request`` –
#: obiekt żądania przekazany do logu (``extra={"request": …}`` w loggerach Django) jako ``repr``.
SENSITIVE_KEY_PARTS = (
    "passport",
    "pesel",
    "health",
    "diet",
    "allerg",
    "medic",
    "birth",
    "phone",
    "email",
    "mail",
    "address",
    "file",
    "photo",
    "password",
    "passwd",
    "secret",
    "token",
    "key",
    "auth",
    "cookie",
    "session",
    "csrf",
    "name",
    "guardian",
    "parent",
    "visa",
    "document",
    "iban",
    "account",
    "card",
    "signature",
    "args",
    "argv",
    "kwargs",
    "data",
    "body",
    "ip",
    "query",
    "fragment",
    "request",
)

#: Nagłówki żądania, które zostają. Wszystkie pozostałe (``Authorization``, ``Cookie``,
#: ``X-Maintenance-Bypass``, ``Referer`` z tokenem w zapytaniu, ``User-Agent``, ``X-Real-IP``…)
#: znikają. Lista krótka, bo do zrozumienia błędu wystarczy wiedzieć, na jaki host i co przyszło.
ALLOWED_HEADERS = frozenset(
    {"host", "content-type", "content-length", "accept", "accept-language", "x-forwarded-proto"}
)

#: Klucze ``contexts`` opisujące środowisko uruchomieniowe (wersje Pythona, systemu, śledzenie).
#: Nie niosą danych osób, a filtr kluczy zjadłby np. ``os.name`` czy ``runtime.name``.
TECHNICAL_CONTEXTS = frozenset(
    {"runtime", "os", "trace", "device", "app", "browser", "culture", "cloud_resource"}
)

#: Segmenty ścieżki, **po których** wszystko jest sekretem albo kodem osoby – niezależnie od długości:
#: ``reset/<uid>/<token>/``, ``zgoda/<token>/``, ``zaproszenie/<token>/``, ``activate/<token>/``,
#: ``unsubscribe/<token>/`` (forum, absolwenci), ``visa/verify/<kod>/``, ``dyplomy/<kod>/``,
#: ``me/messages/new/<token>/``, przepustki i linki jednorazowe.
TOKEN_ROUTE_KEYWORDS = frozenset(
    {
        "reset",
        "zgoda",
        "zgody",
        "consent",
        "zaproszenie",
        "zaproszenia",
        "invite",
        "invitation",
        "accept",
        "activate",
        "aktywacja",
        "unsubscribe",
        "wypisz",
        "verify",
        "weryfikacja",
        "dyplomy",
        "diplomas",
        "certificate",
        "new",
        "token",
        "confirm",
        "potwierdz",
        "bypass",
        "sso",
        "magic",
        "share",
    }
)

#: Segment „wygląda na token”: co najmniej 12 znaków alfabetu base64url/hex **i** cyfra, wielka litera,
#: podkreślnik albo ``=``. Bez kropki: pliki statyczne z odciskiem (``app.3f2a1b9c.js``) zostają.
#: Slugi (``processing-register``, ``delegation-logistics``) – małe litery i myślniki – też.
_SEGMENT_CHARS = re.compile(r"[A-Za-z0-9_\-=%~:]{12,}")
_SEGMENT_MARK = re.compile(r"[0-9A-Z_=%]")

# Każde wyrażenie zaczyna się od negatywnego spojrzenia wstecz na znaki własnej klasy – dopasowanie
# rusza wyłącznie od początku ciągu, a nie od każdej jego pozycji (bez tego „a-a-a-…” bez @ kosztuje
# kwadratowo). Powtórzenia ograniczone.
_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]{1,63}(?:\.[A-Za-z0-9\-]{1,63}){1,8}"
)
#: Jedenaście cyfr pod rząd (PESEL) – nie fragment dłuższej liczby (znaczniki czasu w ms mają 13 cyfr).
_PESEL_RE = re.compile(r"(?<!\d)\d{11}(?!\d)")
#: Numery telefonu: międzynarodowe (``+`` i 8–15 cyfr z odstępami/myślnikami) albo krajowe 3-3-3
#: (``600 700 800``, ``600-700-800``, ``600700800``). Daty (``2026-10-04``) i godziny tu nie pasują.
_PHONE_RE = re.compile(r"(?<![\w.:+\-])(?:\+\d[\d \-]{6,16}\d|\d{3}[ \-]?\d{3}[ \-]?\d{3})(?![\w.:\-])")
#: Adresy IPv4 i IPv6 (klucze cache'u limitów, ``X-Real-IP`` w komunikatach). IPv6: co najmniej trzy
#: dwukropki albo ``::`` – godziny ``12:00:00`` (dwa dwukropki) zostają.
_IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_IPV6_RE = re.compile(
    r"(?<![\w:])(?:(?:[0-9A-Fa-f]{1,4}:){3,7}[0-9A-Fa-f]{1,4}"
    r"|(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4}){0,6})?::(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4}){0,6})?)"
    r"(?![\w:])"
)
#: Wartości par ``nazwa=wartość``/``nazwa: wartość`` o nazwach wskazujących na sekret.
_SECRET_PARAM_RE = re.compile(
    r"(?i)(?<![\w\-])([\w\-]{0,40}?(?:token|key|code|secret|signature|password|passwd|sig|jwt|auth|session"
    r"|csrf)[\w\-]{0,40})(\s{0,3}[=:]\s{0,3})([^&\s\"',;)]{1,512})"
)
_BEARER_RE = re.compile(r"(?i)(?<![\w\-])(bearer|basic|token)\s{1,3}[A-Za-z0-9._~+/\-]{1,4096}=*")
#: Błędy Postgresa niosą **wartości z bazy**: ``DETAIL:  Key (email)=(jan@…) already exists.``,
#: ``DETAIL:  Failing row contains (17, Jan, Kowalski, 2011-04-03, …)``. Wszystko od ``DETAIL:`` albo
#: ``Failing row contains (`` do końca tekstu znika – wartość w wierszu bywa wielolinijkowa (notatka
#: o zdrowiu z enterem), więc koniec linii nie jest końcem danych (przegląd 5.10.2026). Tekst jest
#: wcześniej ucięty do 2 KB, więc ``.{0,2048}`` nie kosztuje więcej niż sam tekst.
_PG_DETAIL_RE = re.compile(r"(?s)(DETAIL:[ \t]{0,8}).{1,2048}")
_PG_FAILING_ROW_RE = re.compile(r"(?s)(Failing row contains )\(.{0,2048}")
_PG_KEY_RE = re.compile(r"Key \(([^)\n]{0,200})\)=\([^\n]{0,2048}?\)")
#: JWT (trzy segmenty base64url rozdzielone kropkami) – przepustki Jitsi/LiveKit w komunikatach.
_JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_\-])eyJ[A-Za-z0-9_\-]{1,4096}\.[A-Za-z0-9_\-]{1,4096}\.[A-Za-z0-9_\-]{0,4096}"
)
#: Adres z hostem: ścieżka do maskowania, zapytanie i fragment do usunięcia.
_URL_RE = re.compile(
    r"(?<![\w.+\-])(https?://[^\s/?#\"'<>]{1,253})(/[^\s?#\"'<>]{0,1024})?([?#][^\s\"'<>]{0,2048})?"
)
#: Sama ścieżka w tekście (``GET /reset/MQ/abc-123/``, ``next=/zgoda/…``, ``repr`` żądania Django
#: ``<WSGIRequest: POST '/x/?imie=Jan'>``). Zaczyna się od ``/`` po znaku, który nie należy do ścieżki
#: – nie łapie środka adresu z hostem (ten obsłużył ``_URL_RE``); zapytanie za nią znika w całości.
_PATH_RE = re.compile(r"(?<![\w/.:%~\-\]])(/[A-Za-z0-9_\-.%~=:/\[\]]{1,1024})(\?[^\s\"'<>]{0,2048})?")


def _token_like(segment: str) -> bool:
    stem = segment[:-5] if segment.lower().endswith(".html") else segment
    return bool(_SEGMENT_CHARS.fullmatch(stem)) and bool(_SEGMENT_MARK.search(stem))


def mask_path(path: str) -> str:
    """Ścieżka z segmentami-tokenami zamienionymi na ``[Filtered]``.

    Segment jest maskowany, gdy wygląda na token (:func:`_token_like`) albo stoi **za** słowem
    z :data:`TOKEN_ROUTE_KEYWORDS` (``/reset/MQ/abc/`` → ``/reset/[Filtered]/[Filtered]/``).
    """
    if not path:
        return path
    out: list[str] = []
    after_keyword = False
    for segment in path.split("/"):
        if not segment or segment == FILTERED:
            out.append(segment)
            continue
        if after_keyword or _token_like(segment):
            out.append(FILTERED)
        else:
            out.append(segment)
        if segment.lower() in TOKEN_ROUTE_KEYWORDS:
            after_keyword = True
    return "/".join(out)


def scrub_url(url: str) -> str:
    """Adres bez zapytania i fragmentu, z zamaskowaną ścieżką."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return FILTERED
    return urlunsplit((parts.scheme, parts.netloc, mask_path(parts.path), "", ""))


#: Zgodność wsteczna nazwy – kiedyś tylko zdejmowała zapytanie, dziś także maskuje ścieżkę.
strip_query = scrub_url


def _url_sub(match: re.Match) -> str:
    return match.group(1) + mask_path(match.group(2) or "")


def scrub_text(value: str) -> str:
    """Zamienia w napisie wszystko, co wygląda na daną osobową albo sekret, na ``[Filtered]``."""
    if not value:
        return value
    if len(value) > MAX_TEXT:
        value = value[:MAX_TEXT] + "…[truncated]"
    value = _PG_DETAIL_RE.sub(lambda m: f"{m.group(1)}{FILTERED}", value)
    value = _PG_FAILING_ROW_RE.sub(lambda m: f"{m.group(1)}({FILTERED})", value)
    value = _PG_KEY_RE.sub(lambda m: f"Key ({m.group(1)})=({FILTERED})", value)
    value = _JWT_RE.sub(FILTERED, value)
    value = _URL_RE.sub(_url_sub, value)
    value = _PATH_RE.sub(lambda m: mask_path(m.group(1)), value)
    value = _BEARER_RE.sub(lambda m: f"{m.group(1)} {FILTERED}", value)
    value = _SECRET_PARAM_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{FILTERED}", value)
    value = _EMAIL_RE.sub(FILTERED, value)
    value = _PESEL_RE.sub(FILTERED, value)
    value = _PHONE_RE.sub(FILTERED, value)
    value = _IPV4_RE.sub(FILTERED, value)
    value = _IPV6_RE.sub(lambda m: m.group(0) if m.group(0) in ("::", ":") else FILTERED, value)
    return value


#: Klucze, które pasują do fragmentu z listy wyżej, a niosą wyłącznie nazwę techniczną
#: (``celery-job.task_name``, kod odpowiedzi). Lista dokładnych nazw, nie fragmentów.
SAFE_KEYS = frozenset({"task_name", "task_id", "status_code", "reason", "retries", "logger_name"})


def is_sensitive_key(key: Any) -> bool:
    lowered = str(key).lower()
    if lowered in SAFE_KEYS:
        return False
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def scrub_mapping(value: Any, depth: int = 0) -> Any:
    """Rekurencyjnie: wartości kluczy wrażliwych → ``[Filtered]``, napisy → :func:`scrub_text`."""
    if depth > 20:
        return FILTERED
    if isinstance(value, dict):
        return {
            k: (FILTERED if is_sensitive_key(k) else scrub_mapping(v, depth + 1)) for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [scrub_mapping(v, depth + 1) for v in value]
    if isinstance(value, str):
        return scrub_text(value)
    return value


def scrub_strings(value: Any, depth: int = 0) -> Any:
    """Rekurencyjnie tylko napisy (bez filtra kluczy) – dla części strukturalnych zdarzenia."""
    if depth > 30:
        return value
    if isinstance(value, dict):
        return {k: scrub_strings(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_strings(v, depth + 1) for v in value]
    if isinstance(value, str):
        return scrub_text(value)
    return value


def _route(event: dict) -> str | None:
    """Wzorzec trasy Django z ``transaction`` (integracja Django, ``transaction_style="url"``)."""
    info = event.get("transaction_info")
    transaction = event.get("transaction")
    if isinstance(info, dict) and info.get("source") == "route" and isinstance(transaction, str):
        return transaction
    return None


def _scrub_request(request: dict, route: str | None) -> dict:
    headers = request.get("headers") or {}
    if isinstance(headers, dict):
        items = headers.items()
    else:  # lista par – tak też bywa w protokole
        items = [tuple(pair) for pair in headers if isinstance(pair, (list, tuple)) and len(pair) == 2]
    kept = {k: scrub_text(str(v)) for k, v in items if str(k).lower() in ALLOWED_HEADERS}
    clean = {"headers": kept}
    if request.get("method"):
        clean["method"] = str(request["method"])
    if request.get("url"):
        try:
            parts = urlsplit(str(request["url"]))
            if route:
                # Wzorzec trasy pochodzi z urlconfu, nie od klienta – bez dalszego filtrowania (maska
                # zjadłaby ``{token}`` za słowem ``reset``, a to jest nazwa parametru, nie wartość).
                clean["url"] = urlunsplit((parts.scheme, parts.netloc, route, "", ""))
            else:
                clean["url"] = scrub_text(
                    urlunsplit((parts.scheme, parts.netloc, mask_path(parts.path), "", ""))
                )
        except ValueError:
            clean["url"] = FILTERED
    return clean


def _scrub_frames(stacktrace: dict | None) -> None:
    if not isinstance(stacktrace, dict):
        return
    for frame in stacktrace.get("frames") or ():
        if isinstance(frame, dict):
            frame.pop("vars", None)


def _scrub_span(span: dict) -> None:
    op = str(span.get("op") or "")
    description = span.get("description")
    if isinstance(description, str):
        if op.startswith("db.redis") or op.startswith("cache."):
            # Polecenie Redisa bez klucza i wartości (klucz limitu niesie adres IP, cache – dane).
            span["description"] = description.split(" ", 1)[0][:32]
        else:
            span["description"] = scrub_text(description)
    if isinstance(span.get("data"), dict):
        span["data"] = scrub_mapping(span["data"])


def scrub_event(event: dict, hint: Any = None) -> dict:
    """``before_send``/``before_send_transaction``: zdarzenie bez danych osobowych.

    Działa **w miejscu** i oddaje to samo zdarzenie (tak wymaga protokół klienta). Nigdy nie
    zwraca ``None`` – odrzucenie zdarzenia to decyzja o próbkowaniu, a nie o prywatności.
    """
    if not isinstance(event, dict):
        return event
    route = _route(event)
    if isinstance(event.get("request"), dict):
        event["request"] = _scrub_request(event["request"], route)
    event.pop("user", None)
    # Nazwa serwera to identyfikator kontenera – zostaje; adres IP klienta – nigdy.
    if isinstance(event.get("extra"), dict):
        extra = event["extra"]
        extra.pop("sys.argv", None)
        event["extra"] = scrub_mapping(extra)
    # Tagi ustawiają integracje i my (``competition`` = slug) – nazwy są techniczne, wartości krótkie.
    if isinstance(event.get("tags"), dict):
        event["tags"] = scrub_strings(event["tags"])
    contexts = event.get("contexts")
    if isinstance(contexts, dict):
        event["contexts"] = {
            name: (scrub_strings(ctx) if name in TECHNICAL_CONTEXTS else scrub_mapping(ctx))
            for name, ctx in contexts.items()
        }
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        for key in ("message", "formatted"):
            if isinstance(logentry.get(key), str):
                logentry[key] = scrub_text(logentry[key])
        if "params" in logentry:
            logentry["params"] = scrub_mapping(logentry["params"])
    if isinstance(event.get("message"), str):
        event["message"] = scrub_text(event["message"])
    exception = event.get("exception")
    values = exception.get("values") if isinstance(exception, dict) else exception
    for exc in values or ():
        if isinstance(exc, dict):
            if isinstance(exc.get("value"), str):
                exc["value"] = scrub_text(exc["value"])
            _scrub_frames(exc.get("stacktrace"))
    threads = event.get("threads")
    for thread in (threads.get("values") if isinstance(threads, dict) else threads) or ():
        if isinstance(thread, dict):
            _scrub_frames(thread.get("stacktrace"))
    _scrub_frames(event.get("stacktrace"))
    breadcrumbs = event.get("breadcrumbs")
    crumbs = breadcrumbs.get("values") if isinstance(breadcrumbs, dict) else breadcrumbs
    if isinstance(crumbs, list):
        cleaned = [scrub_breadcrumb(c) for c in crumbs if isinstance(c, dict)]
        if isinstance(breadcrumbs, dict):
            breadcrumbs["values"] = cleaned
        else:
            event["breadcrumbs"] = cleaned
    # Transakcje (gdy ``SENTRY_TRACES_SAMPLE_RATE`` > 0): opisy i dane spanów – zapytania SQL,
    # adresy wywołań HTTP, polecenia Redisa.
    for span in event.get("spans") or ():
        if isinstance(span, dict):
            _scrub_span(span)
    if isinstance(event.get("transaction"), str) and route is None:
        event["transaction"] = scrub_text(mask_path(event["transaction"].split("?", 1)[0]))
    return event


def strip_query_in_text(text: str) -> str:
    """Adresy wewnątrz dłuższego napisu: bez zapytania i fragmentu, z zamaskowaną ścieżką."""
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT]
    return _URL_RE.sub(_url_sub, text)


def scrub_breadcrumb(crumb: dict, hint: Any = None) -> dict:
    """``before_breadcrumb``: komunikat przez :func:`scrub_text`, dane przez filtr kluczy.

    Redis: zostaje wyłącznie nazwa polecenia (``GET``, ``SET``) – klucz limitu żądań to adres IP,
    a klucz cache'u bywa adresem e-mail. Adresy (``data.url``, także wywołania wychodzące i webhooki):
    bez zapytania, z zamaskowaną ścieżką.
    """
    if not isinstance(crumb, dict):
        return crumb
    data = crumb.get("data") if isinstance(crumb.get("data"), dict) else None
    if crumb.get("category") == "redis" or crumb.get("type") == "redis":
        command = (data or {}).get("redis.command") or str(crumb.get("message") or "").split(" ", 1)[0]
        crumb["message"] = str(command)[:32]
        crumb["data"] = {"redis.command": str(command)[:32]}
        return crumb
    if isinstance(crumb.get("message"), str):
        crumb["message"] = scrub_text(crumb["message"])
    if data is not None:
        if isinstance(data.get("url"), str):
            data["url"] = scrub_url(data["url"])
        crumb["data"] = scrub_mapping(data)
    return crumb
