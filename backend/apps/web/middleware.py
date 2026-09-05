"""Nagłówek ``Content-Security-Policy`` dla całej aplikacji.

Decyzje (T-08, „Wymagania bezpieczeństwa”):

- ``script-src`` **nie** zawiera ``'unsafe-inline'`` ani ``'unsafe-eval'``. W szablonach nie ma
  ani jednego skryptu inline: HTMX, Alpine (build ``@alpinejs/csp``, który nie ewaluuje wyrażeń)
  i pdf.js przychodzą z dwóch pinowanych CDN-ów, a własny kod żyje w ``static/js/*.js``.
  Do listy dochodzi jednorazowy ``nonce`` – jest przypięty do znaczników ``<script>`` interfejsu,
  więc polityka pozostaje szczelna także wtedy, gdy ktoś kiedyś zawęzi listę hostów,
- ``style-src`` ma ``'unsafe-inline'`` **świadomie**: HTMX ustawia style przejść na elementach
  (``htmx-indicator``), a warstwa adnotacji pdf.js pozycjonuje prostokąty przez ``style.left/top``.
  Styl inline nie wykonuje kodu, więc ryzyko jest nieporównywalnie mniejsze niż przy skryptach;
  usunięcie tego wyjątku wymagałoby rezygnacji z HTMX albo własnego builda z nonce na każdym stylu,
- ``connect-src`` zawiera dodatkowo publiczny host MinIO (``S3_PUBLIC_ENDPOINT_URL``): pdf.js
  pobiera plik rozwiązania przez ``fetch``, a endpoint pobrania przekierowuje na presigned URL,
- ``object-src 'none'``, ``base-uri 'self'``, ``frame-ancestors 'none'`` – standardowa domknięta baza.

Nagłówek jest ustawiany na **każdej** odpowiedzi. Dla dwóch prefiksów – ``/cms/`` (Wagtail) i
``/admin/`` (panel Django) – obowiązuje jednak **osobna, luźniejsza** polityka:

- oba panele wstrzykują skrypty i style inline (Wagtail dodatkowo używa telepathu i Draftaila,
  panel Django – widgetów kalendarza), więc polityka nonce-only wyłączyłaby je w całości.
  Przepisanie ich szablonów nie jest w naszej gestii: to kod bibliotek,
- ryzyko jest ograniczone zakresem: obie ścieżki wymagają zalogowania i uprawnień
  (``access_admin`` / ``is_staff``), a treści od anonimów nigdy się w nich nie renderują,
- **strony publiczne pozostają bez ``'unsafe-inline'`` dla skryptów** – to jest testowane
  (``apps/web/tests/test_public.py`` oraz ``apps/cms/tests/test_security.py``).

Uwaga implementacyjna: w polityce panelu **nie ma** nonce'a. Przeglądarka, widząc ``nonce-…``
w ``script-src``, ignoruje ``'unsafe-inline'`` – doklejenie obu naraz dałoby politykę pozornie
luźną i faktycznie blokującą panel.
"""

from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from django.conf import settings

#: CDN-y, z których wolno ładować skrypty. Pinowanie wersji i SRI są w szablonie ``base.html``.
SCRIPT_CDN_SOURCES = ("https://cdnjs.cloudflare.com", "https://cdn.jsdelivr.net")

NONCE_BYTES = 16

#: Prefiksy ścieżek panelu redakcyjnego/administracyjnego. Kolejność bez znaczenia.
#: ``/cms/`` musi się zgadzać z ``config/urls.py``.
ADMIN_PATH_PREFIXES = ("/cms/", "/admin/")


def _origin(url: str | None) -> str:
    """Sam schemat i host z URL-a z ustawień. Pusty string, gdy nic sensownego nie podano."""
    parts = urlsplit((url or "").strip())
    if parts.scheme in ("http", "https") and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return ""


def build_policy(nonce: str) -> str:
    """Buduje treść polityki dla jednego żądania (nonce jest jednorazowy)."""
    script_src = ["'self'", f"'nonce-{nonce}'", *SCRIPT_CDN_SOURCES]
    connect_src = ["'self'", *SCRIPT_CDN_SOURCES]
    storage_origin = _origin(getattr(settings, "S3_PUBLIC_ENDPOINT_URL", ""))
    if storage_origin:
        connect_src.append(storage_origin)
    directives = [
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        # Zobacz docstring modułu: wyjątek dotyczy wyłącznie stylów, nigdy skryptów.
        "style-src 'self' 'unsafe-inline'",
        f"script-src {' '.join(script_src)}",
        f"connect-src {' '.join(connect_src)}",
        # pdf.js uruchamia worker; przy CDN cross-origin robi to przez blob: (fallback biblioteki).
        "worker-src 'self' blob:",
    ]
    return "; ".join(directives)


def build_admin_policy() -> str:
    """Polityka panelu (``/cms/``, ``/admin/``). Świadomie z ``'unsafe-inline'`` dla skryptów.

    ``frame-ancestors 'self'``, a nie ``'none'``: podgląd strony w Wagtailu osadza własny adres
    w ``<iframe>`` tej samej domeny. Ramek z obcych domen nadal nie ma.
    """
    return "; ".join(
        [
            "default-src 'self'",
            "base-uri 'self'",
            "object-src 'none'",
            "frame-ancestors 'self'",
            "form-action 'self'",
            "img-src 'self' data: blob:",
            "font-src 'self' data:",
            "media-src 'self' data: blob:",
            "style-src 'self' 'unsafe-inline'",
            # Patrz docstring modułu: wyjątek dotyczy wyłącznie ścieżek panelu.
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
            "connect-src 'self'",
            "worker-src 'self' blob:",
            # Osadzenia (EmbedBlock) w podglądzie: bez tego edytor nie pokaże wstawionego filmu.
            "frame-src 'self' https:",
        ]
    )


def is_admin_path(path: str) -> bool:
    """Czy ścieżka należy do panelu. Porównanie po prefiksie, na znormalizowanej ścieżce."""
    return any(path.startswith(prefix) for prefix in ADMIN_PATH_PREFIXES)


class ContentSecurityPolicyMiddleware:
    """Nadaje żądaniu ``csp_nonce`` i dokleja nagłówek CSP do odpowiedzi."""

    header = "Content-Security-Policy"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        nonce = secrets.token_urlsafe(NONCE_BYTES)
        # Nonce musi istnieć zanim szablon zacznie się renderować – stąd przypisanie przed widokiem.
        request.csp_nonce = nonce
        response = self.get_response(request)
        if self.header not in response:
            response[self.header] = (
                build_admin_policy() if is_admin_path(request.path) else build_policy(nonce)
            )
        return response
