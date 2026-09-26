"""Kontrole konfiguracji części informacyjnej (``manage.py check``).

Dziś jedna: token wewnętrznego API wersji porównawczej na django CMS (``apps.cms.djcms_api``).
"""

from __future__ import annotations

from django.conf import settings
from django.core import checks

#: Najkrótszy token, przy którym API w ogóle odpowiada. Trzydzieści dwa znaki to 192 bity przy
#: alfabecie base64 – tyle, ile generuje ``scripts/deploy.sh`` z zapasem. Krótszy sekret nie jest
#: „słabszym zabezpieczeniem”, tylko wyłączeniem API (``apps.cms.djcms_api.auth.api_enabled``).
DJCMS_TOKEN_MIN_LENGTH = 32


@checks.register(checks.Tags.security)
def check_djcms_token(app_configs=None, **kwargs) -> list[checks.CheckMessage]:
    """``cms.W010``: token ustawiony, ale za krótki – API jest przez to wyłączone.

    Ostrzeżenie, a nie błąd: pusty token jest stanem poprawnym (instalacja bez wersji ``dj.``),
    a krótki działa dokładnie tak samo jak pusty – nic nie wycieka, API odpowiada 404. Tyle że
    operator, który token wpisał, chciał API **włączyć**, więc bez tego komunikatu szukałby
    przyczyny pustych sekcji na ``dj.`` w zupełnie innym miejscu.
    """
    token = getattr(settings, "DJCMS_INTERNAL_TOKEN", "") or ""
    if token and len(token) < DJCMS_TOKEN_MIN_LENGTH:
        return [
            checks.Warning(
                f"DJCMS_INTERNAL_TOKEN ma {len(token)} znaków – API dla wersji django CMS "
                f"wymaga co najmniej {DJCMS_TOKEN_MIN_LENGTH} i do tego czasu odpowiada 404.",
                hint='Wygeneruj token: python -c "import secrets; print(secrets.token_urlsafe(36))".',
                id="cms.W010",
            )
        ]
    return []
