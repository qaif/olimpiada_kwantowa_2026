"""System checki wtyczek redakcyjnych i biblioteki plików."""

from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, register


@register()
def check_filer_is_public_only(app_configs=None, **kwargs):
    """``dj_blocks.E001``: pliki filera wyłącznie publiczne, bez przełącznika „prywatny” (``files.py``).

    Błąd, a nie ostrzeżenie: pole „prywatny” obiecywałoby redaktorom ochronę, której nikt nie
    egzekwuje (``/djcms/media/*`` serwuje Caddy bez pytania Django o zgodę). Uprawnienia folderów
    (``FILER_ENABLE_PERMISSIONS``) są włączone od DJ-02g, więc filer sam pola nie chowa – robi to
    ``files.hide_private_toggle``, a ta kontrola pilnuje, żeby tak zostało.
    """
    from .files import private_toggle_visible

    errors = []
    if not getattr(settings, "FILER_IS_PUBLIC_DEFAULT", True):
        errors.append(
            Error(
                "FILER_IS_PUBLIC_DEFAULT musi być True – pliki djcms są wyłącznie publiczne.",
                hint="docs/tasks/DJ-01.md § 7, reguła 12.",
                id="dj_blocks.E001",
            )
        )
    for where in private_toggle_visible():
        errors.append(
            Error(
                f"Panel filera pokazuje przełącznik prywatności ({where}) – pliki djcms są publiczne.",
                hint="apps.blocks.files.hide_private_toggle (woła BlocksConfig.ready).",
                id="dj_blocks.E001",
            )
        )
    return errors


@register()
def check_filer_folder_permissions(app_configs=None, **kwargs):
    """``dj_blocks.E002``: uprawnienia folderów filera włączone – folder konkursu należy do jego redakcji.

    Bez nich redaktor konkursu A widziałby, zmieniał i kasował pliki z folderu konkursu B
    (DJ-02 D5, S12). Grupy i ``FolderPermission`` zakłada ``apps.sites.permissions``.
    """
    if getattr(settings, "FILER_ENABLE_PERMISSIONS", False):
        return []
    return [
        Error(
            "FILER_ENABLE_PERMISSIONS musi być włączone – foldery filera są per konkurs.",
            hint="docs/tasks/DJ-02.md § 1.2 D5.",
            id="dj_blocks.E002",
        )
    ]
