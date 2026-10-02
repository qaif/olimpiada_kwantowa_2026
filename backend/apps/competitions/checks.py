"""Kontrole konfiguracji zawodów (``manage.py check``).

- ``competitions.W001`` – sekret przepustek Jitsi (``JITSI_JWT_APP_SECRET``) za krótki albo równy
  innemu sekretowi (v0.39.0, ``apps.competitions.jitsi_jwt``).
"""

from __future__ import annotations

import hmac

from django.conf import settings
from django.core import checks

from .jitsi_jwt import SECRET_MIN_LENGTH, app_secret

#: Sekrety, z którymi klucz przepustek nie może być równy. Ta sama lista, co przy ``cms.W013``,
#: plus klucz SSO – każdy z nich chroni inną granicę.
OTHER_SECRETS = ("SECRET_KEY", "DJCMS_INTERNAL_TOKEN", "DJCMS_SSO_KEY")


def secret_problems() -> list[str]:
    """Powody ostrzeżenia ``competitions.W001``. Pusty sekret to nie problem – funkcja jest wyłączona."""
    secret = app_secret()
    if not secret:
        return []
    problems = []
    if len(secret) < SECRET_MIN_LENGTH:
        problems.append(
            f"JITSI_JWT_APP_SECRET ma {len(secret)} znaków – przepustki do Jitsi wymagają co najmniej "
            f"{SECRET_MIN_LENGTH} i do tego czasu platforma ich nie wystawia (rozmowy idą zwykłym "
            "linkiem, którego zamknięte Jitsi nie przyjmie)."
        )
    for name in OTHER_SECRETS:
        value = getattr(settings, name, "") or ""
        if value and hmac.compare_digest(value.encode(), secret.encode()):
            problems.append(f"JITSI_JWT_APP_SECRET jest równy {name} – każdy sekret ma być osobny.")
    return problems


@checks.register(checks.Tags.security)
def check_jitsi_jwt_secret(app_configs=None, **kwargs) -> list[checks.CheckMessage]:
    """``competitions.W001``: sekret przepustek Jitsi za krótki albo równy innemu sekretowi.

    Krótki sekret działa jak pusty – platforma nie wystawia tokenów – a przy zamkniętym Jitsi to
    znaczy, że nikt nie wejdzie na rozmowę. Sekret równy ``SECRET_KEY`` łączyłby dwie granice:
    wyciek klucza Django (np. z kopii ``.env``) dawałby od razu możliwość wystawiania przepustek
    moderatora do dowolnego pokoju – i odwrotnie.
    """
    return [
        checks.Warning(
            problem,
            hint="Sekret generuje scripts/deploy_jitsi.sh (64 znaki [A-Za-z0-9]); docs/OPERACJE.md § 25.",
            id="competitions.W001",
        )
        for problem in secret_problems()
    ]
