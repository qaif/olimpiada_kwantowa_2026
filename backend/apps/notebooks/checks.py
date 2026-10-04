"""Sprawdzenie systemowe: piaskownica „inline” nie ma prawa działać w produkcji (QC-01 § 5).

Tryb inline wykonuje kod uczniów w podprocesie **workera** – z jego siecią (Redis, baza) i jego
zmiennymi środowiskowymi w zasięgu procesu-rodzica. Hak audytowy i limity zostają, ale granicy
kontenera (``network_mode: none``, brak sekretów) nie ma. Błąd, a nie ostrzeżenie: pomyłka w
``.env`` produkcji ma zatrzymać start, a nie wylądować w logu, którego nikt nie czyta.
"""

from __future__ import annotations

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
