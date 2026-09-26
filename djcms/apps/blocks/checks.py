"""System checki wtyczek redakcyjnych i biblioteki plików."""

from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, register


@register()
def check_filer_is_public_only(app_configs=None, **kwargs):
    """``dj_blocks.E001``: filer musi działać bez uprawnień i z plikami publicznymi (``files.py``).

    Błąd, a nie ostrzeżenie: włączenie uprawnień odsłoniłoby redaktorom pole „prywatny”, którego
    na ``dj.`` nikt nie egzekwuje (``/djcms/media/*`` serwuje Caddy bez pytania Django o zgodę).
    """
    errors = []
    if getattr(settings, "FILER_ENABLE_PERMISSIONS", False):
        errors.append(
            Error(
                "FILER_ENABLE_PERMISSIONS musi być wyłączone – pliki dj. są wyłącznie publiczne.",
                hint="Usuń ustawienie albo ustaw False (docs/tasks/DJ-01.md § 7, reguła 12).",
                id="dj_blocks.E001",
            )
        )
    if not getattr(settings, "FILER_IS_PUBLIC_DEFAULT", True):
        errors.append(
            Error(
                "FILER_IS_PUBLIC_DEFAULT musi być True – pliki dj. są wyłącznie publiczne.",
                id="dj_blocks.E001",
            )
        )
    return errors
