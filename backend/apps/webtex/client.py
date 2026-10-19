"""Klient API serwer–serwer webTEX (TEX-01 § 2): kompilacja do złożenia i polityka udostępniania.

Każde żądanie niesie ``X-Webtex-Signature`` (``keys.api_signature``). Odpowiedzi webTEX zamieniamy
na :class:`WebtexError` z gotowym, polskim komunikatem dla uczestnika – widok go tylko wyświetla.
Sieci w testach nie ma: testy podmieniają :func:`send`.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import requests
from django.utils.translation import gettext as _

from . import keys

#: Kompilacja projektu na żądanie platformy – latexmk potrafi chwilę mielić (TEX-01: „~120 s”).
BUILD_TIMEOUT_SECONDS = 120
#: Zmiana polityki udostępniania i sprawdzenie zdrowia – krótka operacja na bazie webTEX.
API_TIMEOUT_SECONDS = 10
#: Połączenie z usługą w sieci compose – długie oczekiwanie znaczy, że usługi nie ma.
CONNECT_TIMEOUT_SECONDS = 5
#: Najwięcej pozycji listy błędów LaTeX, które pokazujemy (kontrakt: webTEX oddaje do 20).
MAX_ISSUES = 20

BUILD_PATH = "/api/integration/build"
SHARING_PATH = "/api/integration/sharing"
AI_PATH = "/api/integration/ai"
HEALTH_PATH = "/api/integration/health"


class WebtexError(Exception):
    """Odmowa albo awaria webTEX z komunikatem dla człowieka (``message``) i kodem do testów i logów."""

    def __init__(self, code: str, message: str, issues: list[dict] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.issues = issues or []


def send(method: str, url: str, *, data: bytes | None, headers: dict, timeout, stream: bool = False):
    """Jedyne miejsce, które dotyka sieci (testy podmieniają tę funkcję)."""
    return requests.request(
        method, url, data=data, headers=headers, timeout=timeout, stream=stream, allow_redirects=False
    )


def _request(method: str, path: str, payload: dict | None, *, timeout: float, stream: bool = False):
    body = (
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        if payload is not None
        else b""
    )
    url = f"{keys.internal_url()}{path}"
    # Podpis obejmuje ścieżkę tak, jak zobaczy ją webTEX – razem z ewentualnym prefiksem adresu
    # wewnętrznego, bez query stringu.
    signed_path = urlsplit(url).path
    headers = {"X-Webtex-Signature": keys.api_signature(method, signed_path, body)}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    return send(
        method,
        url,
        data=body if payload is not None else None,
        headers=headers,
        timeout=(CONNECT_TIMEOUT_SECONDS, timeout),
        stream=stream,
    )


def _unavailable() -> WebtexError:
    return WebtexError(
        "unreachable",
        _(
            "Edytor LaTeX nie odpowiada. Spróbuj ponownie za chwilę albo pobierz PDF z edytora "
            "i wyślij go zwykłym formularzem."
        ),
    )


def _json(response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _issues(data: dict) -> list[dict]:
    raw = data.get("issues")
    if not isinstance(raw, list):
        return []
    issues = []
    for item in raw[:MAX_ISSUES]:
        if not isinstance(item, dict):
            continue
        line = item.get("line")
        issues.append(
            {
                "file": str(item.get("file") or "")[:200],
                "line": line if isinstance(line, int) and not isinstance(line, bool) else None,
                "message": str(item.get("message") or "")[:500],
            }
        )
    return issues


def _read_limited(response, limit_bytes: int) -> bytes | None:
    """Treść odpowiedzi do ``limit_bytes`` – ``None``, gdy jest dłuższa (nie trzymamy w pamięci więcej)."""
    chunks = []
    size = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        size += len(chunk)
        if size > limit_bytes:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def build_pdf(sub: str, project: str, *, max_bytes: int) -> bytes:
    """Świeża kompilacja projektu uczestnika (``POST /api/integration/build``) – bajty PDF-u.

    Odmowy webTEX (TEX-01 § 2) zamieniamy na komunikaty: ``404`` – uczestnik nie otwierał edytora
    przy tym zadaniu, ``422`` – nie powstał PDF (lista błędów LaTeX), ``503`` – kompilator niedostępny.
    """
    try:
        response = _request(
            "POST", BUILD_PATH, {"sub": sub, "project": project}, timeout=BUILD_TIMEOUT_SECONDS, stream=True
        )
    except requests.RequestException as exc:
        raise _unavailable() from exc
    try:
        status = response.status_code
        if status == 200:
            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type != "application/pdf":
                raise _unavailable()
            try:
                content = _read_limited(response, max_bytes)
            except requests.RequestException as exc:
                raise _unavailable() from exc
            if content is None:
                raise WebtexError(
                    "too_large",
                    _("PDF z edytora jest większy niż limit pliku tego zadania (%(limit)s MB).")
                    % {"limit": max_bytes // (1024 * 1024)},
                )
            return content
        data = _json(response)
        if status == 404:
            raise WebtexError(
                "no_project",
                _(
                    "W edytorze LaTeX nie ma jeszcze Twojego projektu dla tego zadania. Otwórz edytor "
                    "przyciskiem „Napisz rozwiązanie w LaTeX”, napisz rozwiązanie i spróbuj ponownie."
                ),
            )
        if status == 422:
            raise WebtexError(
                "compile_failed",
                _(
                    "Kompilacja w edytorze nie dała pliku PDF – popraw błędy LaTeX wymienione niżej "
                    "i złóż rozwiązanie ponownie. Poprzednia wersja (jeśli była) zostaje bez zmian."
                ),
                _issues(data),
            )
        if status == 503:
            raise WebtexError(
                "compiler_unavailable",
                _(
                    "Kompilator LaTeX jest chwilowo niedostępny. Spróbuj ponownie za kilka minut – "
                    "Twoja praca w edytorze jest zapisana."
                ),
            )
        raise _unavailable()
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def _set_scope_policy(path: str, scope: str, enabled: bool, what: str) -> int:
    """Polityka zakresu (``{"scope", "enabled"}``) – liczba zmienionych projektów (może być 0).

    Błąd sieci i odpowiedź inna niż ``200`` podnoszą :class:`WebtexError` – zadanie Celery ponawia.
    """
    try:
        response = _request(
            "POST", path, {"scope": scope, "enabled": bool(enabled)}, timeout=API_TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:
        raise WebtexError("unreachable", f"webTEX nie odpowiada: {exc}") from exc
    if response.status_code != 200:
        raise WebtexError("http_error", f"webTEX odpowiedział {response.status_code} na zmianę: {what}.")
    projects = _json(response).get("projects")
    return projects if isinstance(projects, int) and not isinstance(projects, bool) else 0


def set_sharing(scope: str, enabled: bool) -> int:
    """Udostępnianie linków w zakresie (``POST /api/integration/sharing``, TEX-01 D5)."""
    return _set_scope_policy(SHARING_PATH, scope, enabled, "udostępnianie linków")


def set_ai(scope: str, enabled: bool) -> int:
    """Asystent AI w zakresie (``POST /api/integration/ai``, TEX-01 Uzupełnienie A, D10)."""
    return _set_scope_policy(AI_PATH, scope, enabled, "asystent AI")


def health() -> dict:
    """``GET /api/integration/health`` – dla operatora (``manage.py shell``), nie dla uczestnika."""
    try:
        response = _request("GET", HEALTH_PATH, None, timeout=API_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise WebtexError("unreachable", f"webTEX nie odpowiada: {exc}") from exc
    if response.status_code != 200:
        raise WebtexError("http_error", f"webTEX odpowiedział {response.status_code}.")
    return _json(response)
