"""Sprawdzenie systemowe: piaskownica „inline” nie ma prawa działać w produkcji (QC-01 § 5).

Tryb inline wykonuje kod uczniów w podprocesie **workera** – z jego siecią (Redis, baza) i jego
zmiennymi środowiskowymi w zasięgu procesu-rodzica. Hak audytowy i limity zostają, ale granicy
kontenera (``network_mode: none``, brak sekretów) nie ma. Błąd, a nie ostrzeżenie: pomyłka w
``.env`` produkcji ma zatrzymać start, a nie wylądować w logu, którego nikt nie czyta.
"""

from __future__ import annotations

import re

from django.conf import settings
from django.core.checks import Error, register


@register()
def inline_runner_not_in_production(app_configs, **kwargs):
    if getattr(settings, "NOTEBOOK_RUNNER_INLINE", False) and not settings.DEBUG:
        test_settings = (
            settings.SETTINGS_MODULE.endswith(".test") if hasattr(settings, "SETTINGS_MODULE") else False
        )
        if not test_settings:
            return [
                Error(
                    "NOTEBOOK_RUNNER_INLINE=1 przy DEBUG=0: kod uczniów wykonywałby się w workerze "
                    "bez izolacji.",
                    hint="Usuń NOTEBOOK_RUNNER_INLINE z .env i uruchom kontener notebook-runner "
                    "(profil 'notebooks').",
                    id="notebooks.E001",
                )
            ]
    return []


#: Nazwa hosta z opcjonalnym portem (dev: ``lab.localhost:8000``); bez schematu i ścieżki.
LAB_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+(:[0-9]{1,5})?$")


@register()
def lab_host_is_separate(app_configs, **kwargs):
    """``NOTEBOOK_LAB_HOST`` (QC-02 § 1): poprawna nazwa i **inna** niż każdy host serwisu.

    Host serwisu podany jako host laboratorium oddałby kod uczniów originowi z sesją – dokładnie to,
    przed czym to ustawienie chroni – a ``NotebookLabHostMiddleware`` zamknąłby na nim całą aplikację.
    """
    host = getattr(settings, "NOTEBOOK_LAB_HOST", "")
    if not host:
        return []
    problem = None
    if not LAB_HOST_RE.match(host):
        problem = "to nie jest nazwa hosta (bez schematu, ścieżki i ukośnika; port tylko w dev)"
    else:
        name = host.split(":")[0]
        extra = getattr(settings, "EXTRA_DOMAINS", [])
        platform = {settings.SITE_DOMAIN.lower(), *(domain.lower() for domain in extra)}
        if name in platform or name.removeprefix("www.") in platform:
            problem = "to jest host serwisu (SITE_DOMAIN albo EXTRA_DOMAINS)"
    if problem is None:
        return []
    return [
        Error(
            f"NOTEBOOK_LAB_HOST={host!r}: {problem}.",
            hint="Podaj osobny host laboratorium, np. lab.<SITE_DOMAIN> albo osobną domenę "
            "(docs/tasks/QC-02.md § 1), albo usuń zmienną z .env.",
            id="notebooks.E002",
        )
    ]
