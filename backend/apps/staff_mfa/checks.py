"""Ostrzeżenia przy starcie (``manage.py check``/``migrate`` we wdrożeniu) – przegląd SEC-01, M1 i L7.

Oba dotyczą wyłącznie ``TWO_FACTOR_ENABLED=1``:

- ``staff_mfa.W001`` – pusta lista ról platformy: superkoordynator i konta ``/admin/`` (widzą wszystkie
  konkursy) nie muszą mieć 2FA. Najczęstsza przyczyna: ``TWO_FACTOR_REQUIRED_ROLES=`` przepisane
  ze starego ``.env.example``,
- ``staff_mfa.W002`` – brak aktywnego superkoordynatora: reset 2FA kont personelu przechodzi wtedy
  na koordynatorów (wąsko: tylko personel ich konkursu, nigdy ``admin``/superkoordynator), a poza
  tym zostaje komenda ``manage.py reset_2fa``.

Sprawdzenie bazy jest ostrożne: brak tabel (świeża baza przed ``migrate``) albo brak połączenia
nie może zatrzymać polecenia – wtedy ostrzeżenia W002 po prostu nie ma.
"""

from __future__ import annotations

from django.conf import settings
from django.core.checks import Tags, Warning, register


@register(Tags.security)
def two_factor_policy_checks(app_configs=None, **kwargs):
    if not getattr(settings, "TWO_FACTOR_ENABLED", False):
        return []
    found = []
    from . import policy

    if not policy.platform_roles():
        found.append(
            Warning(
                "TWO_FACTOR_ENABLED=1, a TWO_FACTOR_REQUIRED_ROLES jest puste – superkoordynator "
                "i konta /admin/ nie muszą mieć 2FA.",
                hint="Ustaw TWO_FACTOR_REQUIRED_ROLES=superkoordynator,admin (docs/OPERACJE.md § 41.1).",
                id="staff_mfa.W001",
            )
        )
    try:
        has_super = policy.any_active_super_coordinator()
    except Exception:  # noqa: BLE001 - baza przed migracją albo niedostępna
        has_super = True
    if not has_super:
        found.append(
            Warning(
                "TWO_FACTOR_ENABLED=1, a na platformie nie ma aktywnego superkoordynatora – reset 2FA "
                "kont personelu zostaje koordynatorom (wąsko) i komendzie reset_2fa.",
                hint="Nadaj rolę: manage.py superkoordynator (docs/OPERACJE.md § 41.5).",
                id="staff_mfa.W002",
            )
        )
    return found
