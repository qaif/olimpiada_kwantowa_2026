"""Filtr danych osobowych dla zdarzeń wysyłanych do GlitchTipa (OPS-02 § 2).

Moduł **nie importuje** ``sentry_sdk`` – to czysta funkcja na słowniku zdarzenia. Dzięki temu
testy sprawdzają filtr bez klienta, a instalacja bez ``SENTRY_DSN`` nie ładuje niczego.

Zasada jest odwrotna niż w domyślnym filtrze Sentry: nie „usuń to, co znamy jako wrażliwe”, tylko
„zostaw to, czego potrzebujemy do naprawy błędu”. Serwis przetwarza dane osób niepełnoletnich,
paszporty, dane o zdrowiu i diecie (LOG-01) – lista wrażliwych pól w kodzie będzie zawsze o krok
za kodem, który je dokłada. Dlatego:

- z ``request`` zostaje metoda, ścieżka bez zapytania i kilka nagłówków z **listy dozwolonych**;
  treść żądania, ciasteczka, zapytanie i adres IP znikają zawsze,
- ``user`` znika w całości – także identyfikator, bo konto ucznia jest kontem osoby
  niepełnoletniej, a identyfikator łączy zdarzenie z osobą w bazie,
- zmienne lokalne ramek znikają (klient i tak ich nie zbiera – ``include_local_variables=False``),
- w ``extra``, ``contexts``, danych okruszków i parametrach logu klucz, którego nazwa zawiera
  któryś z :data:`SENSITIVE_KEY_PARTS`, dostaje ``[Filtered]``,
- **każdy** napis zdarzenia przechodzi przez :func:`scrub_text` (e-mail, PESEL, telefon, tokeny
  w adresach, ``Bearer``, ``Key (kolumna)=(wartość)`` z błędów Postgresa).

Nadmiarowe filtrowanie jest tu tanie (gorzej czytelny komunikat), niedomiarowe – nie.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

FILTERED = "[Filtered]"

#: Fragmenty nazw kluczy, których wartości nie wolno wysłać. Dopasowanie po fragmencie, bez względu
#: na wielkość liter: ``passport_number``, ``guardianEmail``, ``health_notes``, ``birth_date``…
#: ``args``/``kwargs`` – argumenty zadań Celery (``extra["celery-job"]``) bywają adresem e-mail albo
#: identyfikatorem osoby; ``data``/``body`` – treść żądania pod inną nazwą.
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
    "kwargs",
    "data",
    "body",
    "ip",
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

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
#: Jedenaście cyfr pod rząd (PESEL) – granica słowa po obu stronach, więc nie zjada fragmentów
#: dłuższych liczb (znaczniki czasu w milisekundach mają 13 cyfr).
_PESEL_RE = re.compile(r"(?<!\d)\d{11}(?!\d)")
#: Numery telefonu: międzynarodowe (``+`` i 8–15 cyfr z odstępami/myślnikami) albo krajowe 3-3-3
#: (``600 700 800``, ``600-700-800``, ``600700800``). Daty (``2026-10-04``) i godziny tu nie pasują.
_PHONE_RE = re.compile(r"(?<![\w.:+\-])(?:\+\d[\d \-]{6,16}\d|\d{3}[ \-]?\d{3}[ \-]?\d{3})(?![\w.:\-])")
#: Wartości parametrów zapytania i par ``nazwa=wartość`` o nazwach wskazujących na sekret.
_SECRET_PARAM_RE = re.compile(
    r"(?i)\b([\w\-]*(?:token|key|code|secret|signature|password|passwd|sig|jwt|auth|session|csrf)[\w\-]*)"
    r"(\s*[=:]\s*)([^&\s\"',;)]+)"
)
_BEARER_RE = re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/\-]+=*")
#: ``DETAIL:  Key (email)=(jan@example.org) already exists.`` – wartość z bazy w komunikacie błędu.
_PG_KEY_RE = re.compile(r"Key \(([^)]*)\)=\((.*?)\)(?= already| is not|\s*$|\.)")
#: JWT (trzy segmenty base64url rozdzielone kropkami) – przepustki Jitsi/LiveKit w komunikatach.
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")


def scrub_text(value: str) -> str:
    """Zamienia w napisie wszystko, co wygląda na daną osobową albo sekret, na ``[Filtered]``."""
    if not value:
        return value
    value = _PG_KEY_RE.sub(lambda m: f"Key ({m.group(1)})=({FILTERED})", value)
    value = _JWT_RE.sub(FILTERED, value)
    value = _BEARER_RE.sub(lambda m: f"{m.group(1)} {FILTERED}", value)
    value = _SECRET_PARAM_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{FILTERED}", value)
    value = _EMAIL_RE.sub(FILTERED, value)
    value = _PESEL_RE.sub(FILTERED, value)
    value = _PHONE_RE.sub(FILTERED, value)
    return value


def strip_query(url: str) -> str:
    """Adres bez zapytania i fragmentu – tam jeżdżą tokeny (reset hasła, przepustki, podpisy S3)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return FILTERED
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


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


def _scrub_request(request: dict) -> dict:
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
        clean["url"] = scrub_text(strip_query(str(request["url"])))
    return clean


def _scrub_frames(stacktrace: dict | None) -> None:
    if not isinstance(stacktrace, dict):
        return
    for frame in stacktrace.get("frames") or ():
        if isinstance(frame, dict):
            frame.pop("vars", None)


def scrub_event(event: dict, hint: Any = None) -> dict:
    """``before_send``/``before_send_transaction``: zdarzenie bez danych osobowych.

    Działa **w miejscu** i oddaje to samo zdarzenie (tak wymaga protokół klienta). Nigdy nie
    zwraca ``None`` – odrzucenie zdarzenia to decyzja o próbkowaniu, a nie o prywatności.
    """
    if not isinstance(event, dict):
        return event
    if isinstance(event.get("request"), dict):
        event["request"] = _scrub_request(event["request"])
    event.pop("user", None)
    # Nazwa serwera to identyfikator kontenera – zostaje; adres IP klienta – nigdy.
    if isinstance(event.get("extra"), dict):
        event["extra"] = scrub_mapping(event["extra"])
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
    # adresy wywołań HTTP.
    for span in event.get("spans") or ():
        if isinstance(span, dict):
            if isinstance(span.get("description"), str):
                span["description"] = scrub_text(strip_query_in_text(span["description"]))
            if isinstance(span.get("data"), dict):
                span["data"] = scrub_mapping(span["data"])
    if isinstance(event.get("transaction"), str):
        event["transaction"] = scrub_text(strip_query_in_text(event["transaction"]))
    return event


def strip_query_in_text(text: str) -> str:
    """``GET https://x/y?token=…`` → ``GET https://x/y`` – adresy wewnątrz dłuższego napisu."""
    return re.sub(r"(https?://[^\s?#]+)[?#][^\s]*", r"\1", text)


def scrub_breadcrumb(crumb: dict, hint: Any = None) -> dict:
    """``before_breadcrumb``: komunikat przez :func:`scrub_text`, dane przez filtr kluczy."""
    if not isinstance(crumb, dict):
        return crumb
    if isinstance(crumb.get("message"), str):
        crumb["message"] = scrub_text(strip_query_in_text(crumb["message"]))
    if isinstance(crumb.get("data"), dict):
        data = crumb["data"]
        if isinstance(data.get("url"), str):
            data["url"] = strip_query(data["url"])
        crumb["data"] = scrub_mapping(data)
    return crumb
