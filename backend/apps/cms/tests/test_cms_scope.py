"""Redakcja per konkurs: grupa ``cms:<slug>``, kolekcja konkursu i komenda ``scope_cms_access``.

Cały ten moduł pilnuje jednej reguły w dwóch kierunkach (``docs/UNIWERSALNY-ETAP-2.md`` § 1.1.5):

- **w stronę Konkursu #1**: nic się nie zmienia. Grupa ``coordinator`` ma po tej zmianie dokładnie
  te uprawnienia, które wpisała jej migracja ``cms.0003``, kolekcje mediów są te, co były, a nowe
  pliki dalej lądują w korzeniu. Test § 5.5 porównuje trzy zbiory **w obie strony**,
- **w stronę konkursu drugiego**: grupa ``cms:<slug>`` daje te same czynności, ale wyłącznie
  w poddrzewie jego witryny i w jego kolekcji — nigdy na korzeniu drzewa.

Komenda ``scope_cms_access`` jest tu jedynym miejscem, które komuś coś **odbiera**, i dlatego ma
najwięcej testów odmowy: Konkurs #1, wyłączona flaga zakresu, wyłączone członkostwa i koordynator
związany z innym konkursem.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError
from wagtail.models import Collection, GroupCollectionPermission, GroupPagePermission, Page

from apps.accounts.models import GROUP_COORDINATOR, CompetitionRole
from apps.accounts.services import grant_role
from apps.accounts.tests.factories import UserFactory
from apps.cms.attachments import ensure_document
from apps.cms.permissions import (
    GROUP_PREFIX,
    SOURCE_GROUPS,
    cms_group_name,
    collection_name,
    ensure_cms_group,
    ensure_collection,
    upload_collection,
)

pytestmark = pytest.mark.django_db


# --- pomocnicze -----------------------------------------------------------------------------------


def _snapshot(group: Group) -> dict[str, set]:
    """Trzy zbiory opisujące komplet uprawnień grupy — dokładnie te z § 5.5."""
    return {
        "permissions": set(group.permissions.values_list("pk", flat=True)),
        "pages": {
            (row.page_id, row.permission_id) for row in GroupPagePermission.objects.filter(group=group)
        },
        "collections": {
            (row.collection_id, row.permission_id)
            for row in GroupCollectionPermission.objects.filter(group=group)
        },
    }


def _scoped(competition, *, memberships: bool = True):
    """Konkurs z włączonym zawężeniem ``/cms/`` (i domyślnie z egzekwowanymi członkostwami)."""
    competition.feature_flags = {
        "scoped_cms_permissions": True,
        "memberships_enforced": memberships,
    }
    competition.save(update_fields=["feature_flags"])
    return competition


# --- 1. ciągłość Konkursu #1 ----------------------------------------------------------------------


def test_competition_one_coordinator_permissions_unchanged(competition, other_competition):
    """Zestaw uprawnień grupy ``coordinator`` po wprowadzeniu grup per konkurs — **równy**.

    Porównujemy trzy zbiory: ``Group.permissions``, ``GroupPagePermission`` (``page_id``,
    ``permission_id``) i ``GroupCollectionPermission`` (``collection_id``, ``permission_id``).
    Migracja ``cms.0003`` jest nietknięta, a grupa ``cms:<slug>`` wyłącznie **dokłada** — różnica
    ma być pusta w obie strony (§ 5.5).
    """
    coordinator = Group.objects.get(name=GROUP_COORDINATOR)
    before = _snapshot(coordinator)

    ensure_cms_group(_scoped(other_competition))

    after = _snapshot(coordinator)
    assert after == before
    # Sam test byłby pusty, gdyby ``cms.0003`` nie zdążyła nic wpisać.
    assert before["permissions"] and before["pages"] and before["collections"]


def test_the_coordinator_group_still_matches_the_wagtail_source_groups(competition, other_competition):
    """Ta sama prawda, powiedziana drugim źródłem: grupa globalna = ``Editors`` + ``Moderators``.

    Porównanie ze snapshotem sprzed wywołania złapie zmianę wprowadzoną **przez** ten kod;
    porównanie z grupami wzorcowymi złapie zmianę wprowadzoną przez cokolwiek innego w wydaniu.
    """
    ensure_cms_group(_scoped(other_competition))
    coordinator = Group.objects.get(name=GROUP_COORDINATOR)
    sources = Group.objects.filter(name__in=SOURCE_GROUPS)

    expected_pages = {
        (row.page_id, row.permission_id) for row in GroupPagePermission.objects.filter(group__in=sources)
    }
    actual_pages = {
        (row.page_id, row.permission_id) for row in GroupPagePermission.objects.filter(group=coordinator)
    }

    assert actual_pages == expected_pages
    assert expected_pages


def test_the_migration_leaves_competition_one_without_a_cms_group(competition):
    """``cms.0023`` dotyczy wyłącznie konkursów z włączoną flagą — a Konkurs #1 jej nie ma.

    Nowa pozycja na liście kolekcji w ``/cms/`` byłaby zmianą widoczną dla redaktora Olimpiady
    Kwantowej (§ 0.5 punkt 20), więc migracja ma go **pominąć**, a nie „założyć na zapas”.
    """
    assert not competition.has_feature("scoped_cms_permissions")
    assert not Group.objects.filter(name=cms_group_name(competition)).exists()

    root = Collection.get_first_root_node()
    assert not root.get_children().filter(name=collection_name(competition)).exists()


# --- 2. grupa konkursu ----------------------------------------------------------------------------


def test_the_cms_group_grants_pages_of_its_own_site_only(other_competition):
    """Cała różnica wobec grupy globalnej: prawa stoją na korzeniu **witryny**, nie drzewa."""
    group = ensure_cms_group(_scoped(other_competition))
    tree_root = Page.objects.filter(depth=1).order_by("path").first()
    site_root_id = other_competition.site.root_page_id

    pages = {row.page_id for row in GroupPagePermission.objects.filter(group=group)}

    assert pages == {site_root_id}
    assert tree_root.pk not in pages


def test_the_cms_group_carries_the_same_actions_as_the_global_one(other_competition):
    """Te same czynności, inny zasięg: zbiory ``permission_id`` mają być identyczne."""
    group = ensure_cms_group(_scoped(other_competition))
    coordinator = Group.objects.get(name=GROUP_COORDINATOR)

    assert set(group.permissions.values_list("pk", flat=True)) == set(
        coordinator.permissions.values_list("pk", flat=True)
    )
    assert group.permissions.filter(codename="access_admin").exists()
    assert {row.permission_id for row in GroupPagePermission.objects.filter(group=group)} == {
        row.permission_id for row in GroupPagePermission.objects.filter(group=coordinator)
    }


def test_the_cms_group_grants_the_collection_of_its_competition(other_competition):
    """Prawa do mediów stoją na kolekcji konkursu, a nie na korzeniu kolekcji."""
    group = ensure_cms_group(_scoped(other_competition))
    root = Collection.get_first_root_node()

    collections = {row.collection_id for row in GroupCollectionPermission.objects.filter(group=group)}

    assert collections == {ensure_collection(other_competition).pk}
    assert root.pk not in collections


def test_ensure_cms_group_is_idempotent(other_competition):
    """Drugi przebieg nie mnoży wierszy i niczego nie odbiera — jak ``cms.0003``."""
    competition = _scoped(other_competition)
    first = _snapshot(ensure_cms_group(competition))

    second = _snapshot(ensure_cms_group(competition))

    assert second == first
    assert Group.objects.filter(name=cms_group_name(competition)).count() == 1
    assert Collection.objects.filter(name=collection_name(competition)).count() == 1


def test_the_group_name_is_reserved_by_its_prefix(other_competition):
    assert cms_group_name(other_competition) == f"{GROUP_PREFIX}{other_competition.slug}"


# --- 3. kolekcja i tor wgrywania plików -----------------------------------------------------------


def test_ensure_collection_is_a_child_of_the_root_and_is_reused(other_competition):
    root = Collection.get_first_root_node()

    created = ensure_collection(other_competition)
    again = ensure_collection(other_competition)

    assert created.pk == again.pk
    assert created.get_parent().pk == root.pk
    assert created.name == other_competition.name


def test_upload_collection_is_the_root_for_competition_one(competition):
    """Flaga wyłączona = stan dzisiejszy: nowy plik ląduje w korzeniu kolekcji."""
    assert upload_collection(competition).pk == Collection.get_first_root_node().pk


def test_upload_collection_is_the_competition_collection_with_the_flag(other_competition):
    competition = _scoped(other_competition)

    assert upload_collection(competition).pk == ensure_collection(competition).pk


def test_a_new_document_lands_in_the_collection_of_its_competition(other_competition, tmp_path):
    """``ensure_document`` wgrywa do kolekcji konkursu — to jest cała zmiana w ``attachments.py``."""
    competition = _scoped(other_competition)
    source = tmp_path / "regulamin-obcy.pdf"
    source.write_bytes(b"%PDF-1.4 regulamin obcego konkursu")

    document, action = ensure_document("Regulamin obcego konkursu", source, competition=competition)

    assert action == "created"
    assert document.collection_id == ensure_collection(competition).pk


def test_a_new_document_of_competition_one_lands_in_the_root(competition, tmp_path):
    source = tmp_path / "regulamin.pdf"
    source.write_bytes(b"%PDF-1.4 regulamin")

    document, action = ensure_document("Regulamin testowy", source, competition=competition)

    assert action == "created"
    assert document.collection_id == Collection.get_first_root_node().pk


def test_an_existing_document_keeps_its_collection(competition, other_competition, tmp_path):
    """Wiersze już wgrane zostają tam, gdzie są — podmiana treści nie przenosi pliku."""
    source = tmp_path / "regulamin.pdf"
    source.write_bytes(b"%PDF-1.4 pierwsza wersja")
    document, _ = ensure_document("Regulamin przenoszony", source, competition=competition)
    root_id = document.collection_id

    source.write_bytes(b"%PDF-1.4 druga wersja")
    again, action = ensure_document("Regulamin przenoszony", source, competition=_scoped(other_competition))

    assert action == "updated"
    assert again.pk == document.pk
    assert again.collection_id == root_id


# --- 4. komenda scope_cms_access ------------------------------------------------------------------


def _coordinator_of(competition, email: str):
    user = UserFactory(email=email)
    grant_role(user, CompetitionRole.COORDINATOR, competition=competition)
    return user


def test_scope_cms_access_refuses_competition_one(competition):
    """Twardy warunek w kodzie, nie w README: Konkurs #1 nie daje się zawęzić."""
    with pytest.raises(CommandError, match="Konkursu #1"):
        call_command("scope_cms_access", "--competition", "kwantowa")


def test_scope_cms_access_refuses_a_competition_without_the_flag(other_competition):
    with pytest.raises(CommandError, match="scoped_cms_permissions"):
        call_command("scope_cms_access", "--competition", other_competition.slug)


def test_scope_cms_access_refuses_without_memberships_enforced(other_competition):
    """Bez ``memberships_enforced`` odebranie grupy odbiera **rolę**, a nie zasięg w ``/cms/``."""
    _scoped(other_competition, memberships=False)

    with pytest.raises(CommandError, match="memberships_enforced"):
        call_command("scope_cms_access", "--competition", other_competition.slug)


def test_scope_cms_access_refuses_an_unknown_competition():
    with pytest.raises(CommandError, match="Nie ma konkursu"):
        call_command("scope_cms_access", "--competition", "nie-ma-takiego")


def test_scope_cms_access_moves_the_coordinator_to_the_group_of_the_competition(other_competition):
    competition = _scoped(other_competition)
    user = _coordinator_of(competition, "koordynator-obcy@example.test")
    assert user.groups.filter(name=GROUP_COORDINATOR).exists()

    call_command("scope_cms_access", "--competition", competition.slug)

    names = set(user.groups.values_list("name", flat=True))
    assert cms_group_name(competition) in names
    assert GROUP_COORDINATOR not in names


def test_scope_cms_access_dry_run_changes_nothing(other_competition, capsys):
    competition = _scoped(other_competition)
    user = _coordinator_of(competition, "koordynator-suchy@example.test")

    call_command("scope_cms_access", "--competition", competition.slug, "--dry-run")

    assert set(user.groups.values_list("name", flat=True)) == {GROUP_COORDINATOR}
    assert not Group.objects.filter(name=cms_group_name(competition)).exists()
    assert "koordynator-suchy@example.test" in capsys.readouterr().out


def test_scope_cms_access_skips_a_coordinator_of_another_competition(competition, other_competition):
    """Osoba koordynująca też Konkurs #1 zostaje nietknięta — odebranie grupy dotknęłoby i jego."""
    scoped = _scoped(other_competition)
    user = _coordinator_of(scoped, "koordynator-dwoch@example.test")
    grant_role(user, CompetitionRole.COORDINATOR, competition=competition)

    call_command("scope_cms_access", "--competition", scoped.slug)

    assert set(user.groups.values_list("name", flat=True)) == {GROUP_COORDINATOR}


def test_scope_cms_access_does_not_touch_competition_one_coordinators(competition, other_competition):
    """Po przebiegu dla konkursu obcego uprawnienia grupy ``coordinator`` są nadal te same."""
    scoped = _scoped(other_competition)
    _coordinator_of(scoped, "koordynator-inny@example.test")
    before = _snapshot(Group.objects.get(name=GROUP_COORDINATOR))

    call_command("scope_cms_access", "--competition", scoped.slug)

    assert _snapshot(Group.objects.get(name=GROUP_COORDINATOR)) == before
