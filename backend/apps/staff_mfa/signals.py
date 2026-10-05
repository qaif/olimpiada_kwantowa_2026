"""Nowa wersja polityki 2FA przy zmianie ról i przełączników (przegląd SEC-01, M3a).

Znacznik zwolnienia w sesji niesie wersję polityki (``policy.version``). Bez tych sygnałów konto,
któremu właśnie nadano rolę koordynatora albo przydział w logistyce, chodziłoby ze starym
„nie musisz” aż do wygaśnięcia znacznika (``EXEMPT_TTL_SECONDS``). Podbicie wersji jest jednym
zapisem do cache'a; reagujemy wyłącznie na role, które polityka może objąć – rejestracja uczestnika
(``Membership`` z rolą ``participant``) wersji nie zmienia, inaczej szczyt rejestracji unieważniałby
znaczniki wszystkich zalogowanych co sekundę.

Przy wyłączonym ``TWO_FACTOR_ENABLED`` sygnały nie robią nic (zachowanie sprzed SEC-01).
"""

from __future__ import annotations

from django.contrib.auth.models import Group
from django.db.models.signals import m2m_changed, post_delete, post_save

from . import policy

#: Role, których zmiana może zmienić odpowiedź polityki.
WATCHED_ROLES = frozenset({*policy.STAFF_GROUPS, "supervisor", "superkoordynator"})


def _enabled() -> bool:
    from apps.accounts.twofactor import is_enabled

    return is_enabled()


def _bump(**_kwargs) -> None:
    if _enabled():
        policy.bump_version()


def _membership_changed(sender, instance, **_kwargs) -> None:
    if _enabled() and getattr(instance, "role", None) in WATCHED_ROLES:
        policy.bump_version()


def _groups_changed(sender, instance, action, pk_set, **_kwargs) -> None:
    if action not in ("post_add", "post_remove", "post_clear") or not _enabled():
        return
    if action == "post_clear" or Group.objects.filter(pk__in=pk_set or (), name__in=WATCHED_ROLES).exists():
        policy.bump_version()


def connect() -> None:
    from apps.accounts.delegations import DelegationLeader
    from apps.accounts.models import Membership, User
    from apps.delegation_logistics.models import LogisticsAccess
    from apps.tenancy.models import Competition

    m2m_changed.connect(_groups_changed, sender=User.groups.through, dispatch_uid="staff_mfa_groups")
    for model, handler in (
        (Membership, _membership_changed),
        (LogisticsAccess, _bump),
        (DelegationLeader, _bump),
        (Competition, _bump),
    ):
        uid = f"staff_mfa_{model._meta.label_lower}"
        post_save.connect(handler, sender=model, dispatch_uid=f"{uid}_save")
        post_delete.connect(handler, sender=model, dispatch_uid=f"{uid}_delete")
