"""Superkoordynator — koordynator **wszystkich** konkursów instalacji, bez konta superużytkownika.

Polecenie organizatora: „obecny koordynator ma nim zostać”. Po zawężeniu ``/cms/`` do konkursu
(``apps.cms.permissions``, ``manage.py scope_cms_access``) koordynator widzi strony, media
i panel **swojego** konkursu. Organizator platformy potrzebuje dalej widoku na całość — i do
tej pory dostawał go przez ``is_superuser``, czyli razem z ``/admin/``, kontami serwisowymi
i każdą tabelą w bazie. Superkoordynator jest tą samą szerokością **wyłącznie w dwóch miejscach**:

- **panel koordynatora** — ``has_role(user, <dowolny konkurs>, "coordinator")`` odpowiada ``True``
  (``apps.accounts.services``), więc ``/coordinator/`` otwiera się pod adresem każdego konkursu,
  a dane są dalej zawężone konkursem **żądania** — superkoordynator ogląda konkurs B pod adresem
  konkursu B, a nie „wszystko naraz”,
- **``/cms/``** — grupa ``superkoordynator`` ma prawa Wagtaila na korzeniu drzewa stron i na
  korzeniu kolekcji mediów, do tego komunikaty i ustawienia serwisu każdej witryny
  (``apps.cms.permissions.ensure_super_coordinator_group``).

**Czego ta rola nie daje:** ``/admin/`` Django zostaje wyłącznie dla superużytkownika
(``is_staff``/``is_superuser`` się nie zmieniają), a w ``/cms/`` — zarządzanie kontami, grupami,
witrynami i kolekcjami. To są czynności operatora serwera, a nie organizatora zawodów.

**Dlaczego grupa Django, a nie pole na koncie.** Tak są zapisane wszystkie role platformy
(``GROUP_*`` w ``apps.accounts.models``), a Wagtail liczy uprawnienia wyłącznie z grup — rola
zapisana polem wymagałaby drugiego źródła prawdy dla ``/cms/``. Nazwa grupy nie jest wśród
``RBAC_GROUPS``: tamte opisują rolę **w konkursie** i mają odpowiednik w ``Membership``, a tej
roli nie da się przypisać do jednego konkursu z definicji.

**Nadanie i odebranie** idzie wyłącznie przez :func:`grant` i :func:`revoke` (komenda
``manage.py superkoordynator``, akcje w ``/admin/``), bo tylko tamtędy powstaje wpis audytu
``accounts.super_coordinator.granted`` / ``.revoked``. Dopisanie grupy ręcznie w formularzu konta
działa (uprawnienia są w grupie), ale zostawia dziennik bez śladu — i o tym mówi podręcznik.
"""

from __future__ import annotations

from django.contrib.auth.models import Group
from django.db import transaction
from django.db.models import Q

from apps.core.models import audit

from .models import GROUP_COORDINATOR, GROUP_SUPER_COORDINATOR, CompetitionRole, User

#: Atrybut na obiekcie konta, pod którym trzymamy odpowiedź na czas jednego żądania.
#: ``request.user`` żyje dokładnie tyle, co żądanie, a o tę rolę pytają w jednym żądaniu bramka
#: widoku, nawigacja i zakres ``/cms/`` — bez pamięci byłyby to trzy identyczne zapytania.
CACHE_ATTR = "_is_super_coordinator"

AUDIT_GRANTED = "accounts.super_coordinator.granted"
AUDIT_REVOKED = "accounts.super_coordinator.revoked"


def is_super_coordinator(user) -> bool:
    """Czy to konto jest superkoordynatorem. Konto nieaktywne nie jest nikim — jak w ``has_role``."""
    if not user or not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    cached = getattr(user, CACHE_ATTR, None)
    if cached is None:
        prefetched = getattr(user, "_prefetched_objects_cache", {}).get("groups")
        if prefetched is not None:
            cached = any(group.name == GROUP_SUPER_COORDINATOR for group in prefetched)
        else:
            cached = user.groups.filter(name=GROUP_SUPER_COORDINATOR).exists()
        setattr(user, CACHE_ATTR, cached)
    return cached


def forget(user) -> None:
    """Zapomina zapamiętaną odpowiedź — po nadaniu albo odebraniu roli na tym samym obiekcie."""
    if user is not None and hasattr(user, CACHE_ATTR):
        delattr(user, CACHE_ATTR)
    # Zakres ``/cms/`` jest liczony z tej samej przynależności i zapamiętany tak samo.
    if user is not None and hasattr(user, "_cms_scope"):
        delattr(user, "_cms_scope")


def super_coordinator_group() -> Group:
    """Grupa ``superkoordynator`` z kompletem uprawnień ``/cms/``, zakładana przy pierwszym użyciu.

    Uprawnienia odtwarza ``apps.cms.permissions.ensure_super_coordinator_group`` — przy **każdym**
    nadaniu, więc instalacja, w której ktoś ręcznie okroił grupę w ``/cms/``, wraca do stanu
    opisanego w podręczniku przy najbliższym nadaniu roli. Import lokalny: konta nie zależą od
    części informacyjnej przy składaniu aplikacji.
    """
    from apps.cms.permissions import ensure_super_coordinator_group

    return ensure_super_coordinator_group()


@transaction.atomic
def grant(user: User, *, actor=None, via: str = "command", request=None) -> bool:
    """Nadaje rolę. ``True``, gdy coś się zmieniło; powtórzenie jest bez skutku i bez wpisu audytu."""
    group = super_coordinator_group()
    if user.groups.filter(pk=group.pk).exists():
        return False
    user.groups.add(group)
    forget(user)
    audit(actor, AUDIT_GRANTED, user, {"email": user.email, "via": via}, request=request)
    return True


@transaction.atomic
def revoke(user: User, *, actor=None, via: str = "command", request=None) -> bool:
    """Odbiera rolę. Rola koordynatora konkursu (grupa ``coordinator``, członkostwa) zostaje."""
    group = Group.objects.filter(name=GROUP_SUPER_COORDINATOR).first()
    if group is None or not user.groups.filter(pk=group.pk).exists():
        return False
    user.groups.remove(group)
    forget(user)
    audit(actor, AUDIT_REVOKED, user, {"email": user.email, "via": via}, request=request)
    return True


def current_coordinators():
    """Konta, które **dziś** mają rolę koordynatora — w którymkolwiek konkursie, którąkolwiek drogą.

    Dwie drogi, bo tyle ich jest w ``has_role``: globalna grupa ``coordinator`` (konkurs
    z wyłączonym ``memberships_enforced``, czyli dzisiejsza produkcja) i wiersz ``Membership``
    z rolą koordynatora (konkurs z włączonym). Suma, a nie jedna z nich: komenda
    ``superkoordynator --all-current-coordinators`` ma nie pominąć nikogo, kto by po zawężeniu
    ``/cms/`` coś stracił.
    """
    return (
        User.objects.filter(
            Q(groups__name=GROUP_COORDINATOR) | Q(memberships__role=CompetitionRole.COORDINATOR)
        )
        .distinct()
        .order_by("email")
    )
