"""System checki uprawnień redaktorów i SSO (DJ-02 D5, D6, S11, S12).

- ``dj_sites.E001`` – ``CMS_PERMISSION`` wyłączone: każdy personel redagowałby każdą witrynę,
- ``dj_sites.W001`` – ``DJCMS_SSO_KEY`` za krótki albo równy innemu sekretowi djcms.
"""

from __future__ import annotations

import hmac

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

from .sso import KEY_MIN_LENGTH


@register(Tags.security)
def check_cms_permissions_enabled(app_configs=None, **kwargs):
    """``dj_sites.E001``: bez ``CMS_PERMISSION`` grupy ``redakcja:<slug>`` nie zawężają niczego."""
    if getattr(settings, "CMS_PERMISSION", False):
        return []
    return [
        Error(
            "CMS_PERMISSION musi być włączone – redaktor konkursu A redagowałby strony konkursu B.",
            hint="docs/tasks/DJ-02.md § 1.2 D5 (grupy redakcja:<slug> z GlobalPagePermission).",
            id="dj_sites.E001",
        )
    ]


@register(Tags.security)
def check_sso_key(app_configs=None, **kwargs):
    """``dj_sites.W001``: pusty klucz = SSO wyłączone (poprawne); krótki albo wspólny – ostrzeżenie."""
    key = getattr(settings, "DJCMS_SSO_KEY", "") or ""
    if not key:
        return []
    warnings = []
    if len(key) < KEY_MIN_LENGTH:
        warnings.append(
            Warning(
                f"DJCMS_SSO_KEY ma {len(key)} znaków – logowanie redaktorów z /cms/ wymaga co najmniej "
                f"{KEY_MIN_LENGTH} i do tego czasu jest wyłączone.",
                id="dj_sites.W001",
            )
        )
    for name in ("DJCMS_INTERNAL_TOKEN", "SECRET_KEY"):
        other = getattr(settings, name, "") or ""
        if other and hmac.compare_digest(other.encode(), key.encode()):
            warnings.append(
                Warning(f"DJCMS_SSO_KEY jest równy {name} – każdy sekret ma być osobny.", id="dj_sites.W001")
            )
    return warnings
