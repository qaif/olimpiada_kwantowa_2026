"""Członkostwo w grupie ``cms:<slug>`` idzie za rolą koordynatora — bez pamiętania o komendzie.

Rola koordynatora zmienia się dziś czterema drogami i żadna z nich nie należy do ``/cms/``:
``grant_role`` (rejestracja, ``create_competition``, ``bootstrap_coordinator``), ``/admin/``
(grupy konta, wiersze ``Membership``), panel koordynatora i przełącznik ``memberships_enforced``,
który zmienia samo **źródło** roli. Wszystkie kończą się zapisem jednego z trzech modeli, więc
nasłuchujemy modeli, a nie dróg:

- ``accounts.Membership`` z rolą koordynatora — zapis i skasowanie,
- przynależność konta do globalnej grupy ``coordinator`` (``m2m_changed`` w obie strony relacji),
- ``tenancy.Competition`` — wyłącznie wtedy, gdy zmieniła się flaga ``memberships_enforced``
  albo ``scoped_cms_permissions`` (porównanie ze stanem z chwili wczytania wiersza).

Każdy odbiornik woła ``apps.cms.permissions.sync_user_cms_groups`` (albo wersję hurtową dla
konkursu), a te działają wyłącznie w konkursach zawężonych. Przed ``scope_cms_access`` na
produkcji żaden konkurs zawężony nie jest, więc odbiorniki kończą się na jednym pytaniu i niczego
nie zapisują — wdrożenie tego wydania samo z siebie nie zmienia nikomu uprawnień.

Zapis grupy ``cms:<slug>`` przez sam odbiornik wywołuje ``m2m_changed`` jeszcze raz, ale z grupą
konkursu w ``pk_set`` — a na nią odbiornik nie reaguje, więc pętli nie ma.
"""

from __future__ import annotations

from django.contrib.auth.models import Group
from django.db.models.signals import m2m_changed, post_delete, post_init, post_save
from django.dispatch import receiver

from apps.accounts.models import GROUP_COORDINATOR, CompetitionRole, Membership, User
from apps.tenancy.models import Competition

#: Flagi konkursu, których zmiana przestawia, kto jest redaktorem jego ``/cms/``.
WATCHED_FLAGS = ("memberships_enforced", "scoped_cms_permissions")
_LOADED_FLAGS = "_cms_flags_at_load"


def _sync(user) -> None:
    from .permissions import sync_user_cms_groups

    sync_user_cms_groups(user)


@receiver(post_save, sender=Membership, dispatch_uid="cms_membership_saved")
@receiver(post_delete, sender=Membership, dispatch_uid="cms_membership_deleted")
def membership_changed(sender, instance, **kwargs):
    if instance.role != CompetitionRole.COORDINATOR:
        return
    user = User.objects.filter(pk=instance.user_id).first()
    if user is not None:
        _sync(user)


@receiver(m2m_changed, sender=User.groups.through, dispatch_uid="cms_coordinator_group_changed")
def coordinator_group_changed(sender, instance, action, reverse, pk_set, **kwargs):
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    if reverse:
        # ``group.user_set.add(...)`` — ``instance`` jest grupą, ``pk_set`` kontami.
        if instance.name != GROUP_COORDINATOR:
            return
        users = User.objects.filter(pk__in=pk_set or ())
    else:
        # ``user.groups.add(...)`` — reagujemy wyłącznie na zmianę grupy ``coordinator``;
        # ``post_clear`` nie mówi, co zniknęło, więc wtedy synchronizujemy zawsze.
        if (
            action != "post_clear"
            and not Group.objects.filter(pk__in=pk_set or (), name=GROUP_COORDINATOR).exists()
        ):
            return
        users = [instance]
    for user in users:
        _sync(user)


def _flags(instance) -> tuple:
    # ``__dict__``, a nie atrybut: pole odroczone (``only()``/``defer()``) wczytywałoby się tu
    # osobnym zapytaniem przy **każdym** utworzeniu obiektu konkursu.
    flags = instance.__dict__.get("feature_flags")
    if flags is None and "feature_flags" not in instance.__dict__:
        return ()
    return tuple((flags or {}).get(name) for name in WATCHED_FLAGS)


@receiver(post_init, sender=Competition, dispatch_uid="cms_competition_flags_loaded")
def remember_flags(sender, instance, **kwargs):
    setattr(instance, _LOADED_FLAGS, _flags(instance))


@receiver(post_save, sender=Competition, dispatch_uid="cms_competition_flags_saved")
def competition_flags_changed(sender, instance, created, **kwargs):
    current = _flags(instance)
    before = getattr(instance, _LOADED_FLAGS, ())
    setattr(instance, _LOADED_FLAGS, current)
    if created or current == before:
        return
    from .permissions import scoped_cms_permissions, sync_competition_cms_group

    if scoped_cms_permissions(instance):
        sync_competition_cms_group(instance)
