"""Redakcja per konkurs: grupa ``cms:<slug>``, kolekcja konkursu i komenda ``scope_cms_access``.

Cały ten moduł pilnuje jednej reguły w dwóch kierunkach (``docs/UNIWERSALNY-ETAP-2.md`` § 1.1.5):

- **w stronę Konkursu #1**: przed komendą nic się nie zmienia (grupa ``coordinator`` ma dokładnie
  to, co wpisała jej migracja ``cms.0003``), a po komendzie koordynator może w ``/cms/`` dokładnie
  to samo — macierz możliwości przed i po jest równa, co sprawdza test **i sama komenda**,
- **w stronę konkursu drugiego**: grupa ``cms:<slug>`` daje te same czynności, ale wyłącznie
  w poddrzewie jego witryny i w jego kolekcji — nigdy na korzeniu drzewa.

Przegląd wycieków między dwoma konkursami (strony, media, okna wyboru, raporty, dziennik) jest
w ``test_cms_permissions_per_competition.py``.
"""

from __future__ import annotations

import io

import pytest
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from PIL import Image as PILImage
from wagtail.documents import get_document_model
from wagtail.images import get_image_model
from wagtail.models import (
    Collection,
    CollectionViewRestriction,
    GroupCollectionPermission,
    GroupPagePermission,
    Page,
)

from apps.accounts.models import GROUP_COORDINATOR, CompetitionRole, User
from apps.accounts.services import grant_role, has_role
from apps.accounts.tests.factories import UserFactory
from apps.cms.attachments import ensure_document
from apps.cms.models import ContentPage
from apps.cms.permissions import (
    GROUP_PREFIX,
    SOURCE_GROUPS,
    cms_abilities,
    cms_group_name,
    collection_name,
    ensure_cms_group,
    ensure_collection,
    upload_collection,
)
from apps.cms.tests.factories import AnnouncementFactory

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
#
# Po wydaniu „uprawnienia CMS per konkurs” komenda działa na całą instalację: zabiera globalnej
# grupie ``coordinator`` uprawnienia ``/cms/`` i przenosi redakcję do grup ``cms:<slug>``. Warunek
# ciągłości Olimpiady Kwantowej nie jest już odmową, tylko **dowodem**: macierz możliwości przed
# i po przebiegu ma być identyczna, a komenda sama się wycofuje, gdy nie jest.


def _image(title: str, collection):
    buffer = io.BytesIO()
    PILImage.new("RGB", (8, 8), (1, 2, 3)).save(buffer, format="PNG")
    return get_image_model().objects.create(
        title=title,
        collection=collection,
        file=SimpleUploadedFile(f"{title}.png", buffer.getvalue(), content_type="image/png"),
    )


def _document(title: str, collection):
    return get_document_model().objects.create(
        title=title,
        collection=collection,
        file=SimpleUploadedFile(f"{title}.pdf", b"%PDF-1.4 x", content_type="application/pdf"),
    )


@pytest.fixture
def kwantowa_only(competition, settings, tmp_path):
    """Instalacja jak dzisiejsza produkcja: jeden konkurs, role z grup, media w korzeniu kolekcji."""
    settings.MEDIA_ROOT = str(tmp_path)
    root = Collection.get_first_root_node()
    coordinator = UserFactory(email="koordynator-kwantowa@example.test", groups=[GROUP_COORDINATOR])
    home = Page.objects.get(pk=competition.site.root_page_id)
    home.add_child(instance=ContentPage(title="Regulamin testowy", slug="regulamin-testowy"))
    _image("logo-kwantowa", root)
    _document("regulamin-kwantowa", root)
    AnnouncementFactory(competition=competition, text="Komunikat kwantowy")
    return coordinator


def _fresh(user):
    """Świeży obiekt konta — Wagtail zapamiętuje uprawnienia na obiekcie (patrz ``cms_abilities``)."""
    return User.objects.get(pk=user.pk)


def test_competition_one_coordinator_keeps_identical_abilities(kwantowa_only, competition):
    """Główny warunek wydania: koordynator Olimpiady Kwantowej po komendzie może **dokładnie** to samo.

    Macierz: każda strona poniżej korzenia × 12 czynności, każdy obraz i dokument × zmiana, usunięcie,
    wybór, wgrywanie, komunikaty, ustawienia witryn i wejście do panelu.
    """
    before = cms_abilities(_fresh(kwantowa_only))

    call_command("scope_cms_access", stdout=io.StringIO())

    after = cms_abilities(_fresh(kwantowa_only))
    assert after == before
    assert ("admin", "access") in before
    assert any(len(row) == 3 and row[0] == "page" and row[2] == "publish" for row in before)
    assert any(len(row) == 3 and row[0] == "image" and row[2] == "change" for row in before)
    # …ale przez inną grupę: globalna nie daje już niczego w ``/cms/``.
    assert cms_group_name(competition) in set(kwantowa_only.groups.values_list("name", flat=True))
    assert not GroupPagePermission.objects.filter(group__name=GROUP_COORDINATOR).exists()
    assert not GroupCollectionPermission.objects.filter(group__name=GROUP_COORDINATOR).exists()


def test_competition_one_super_coordinator_path_loses_nothing(kwantowa_only):
    """Kolejność z ``docs/OPERACJE.md``: najpierw superkoordynator, potem zawężenie.

    Nic nie ubywa; przybywa wyłącznie to, co rola superkoordynatora daje z definicji — komunikaty
    i ustawienia serwisu (``SUPER_COORDINATOR_MODEL_PERMISSIONS``).
    """
    before = cms_abilities(_fresh(kwantowa_only))

    call_command("superkoordynator", "--all-current-coordinators", stdout=io.StringIO())
    call_command("scope_cms_access", stdout=io.StringIO())

    after = cms_abilities(_fresh(kwantowa_only))
    assert before <= after
    assert {row[0] for row in after - before} <= {"announcement", "site_settings"}


def test_the_command_keeps_the_role_group_and_non_cms_permissions(kwantowa_only):
    """Grupa ``coordinator`` zostaje (jest rolą) razem z uprawnieniami spoza ``/cms/``."""
    group = Group.objects.get(name=GROUP_COORDINATOR)
    foreign = Permission.objects.get(content_type__app_label="auth", codename="view_group")
    group.permissions.add(foreign)

    call_command("scope_cms_access", stdout=io.StringIO())

    assert kwantowa_only.groups.filter(name=GROUP_COORDINATOR).exists()
    assert set(group.permissions.values_list("codename", flat=True)) == {"view_group"}
    assert has_role(_fresh(kwantowa_only), None, CompetitionRole.COORDINATOR)


def test_the_command_moves_root_media_into_the_single_competition(kwantowa_only, competition):
    root = Collection.get_first_root_node()

    call_command("scope_cms_access", stdout=io.StringIO())

    target = ensure_collection(competition)
    assert not get_image_model().objects.filter(collection=root).exists()
    assert not get_document_model().objects.filter(collection=root).exists()
    assert get_image_model().objects.filter(collection=target).exists()


def test_the_command_copies_the_root_view_restriction(kwantowa_only, competition):
    """Dokument „tylko dla zalogowanych” nie może po przeniesieniu stać się publiczny."""
    root = Collection.get_first_root_node()
    CollectionViewRestriction.objects.create(
        collection=root, restriction_type=CollectionViewRestriction.LOGIN
    )

    call_command("scope_cms_access", stdout=io.StringIO())

    assert ensure_collection(competition).get_view_restrictions().exists()


def test_dry_run_changes_nothing(kwantowa_only, competition):
    before = _snapshot(Group.objects.get(name=GROUP_COORDINATOR))
    out = io.StringIO()

    call_command("scope_cms_access", "--dry-run", stdout=out)

    assert _snapshot(Group.objects.get(name=GROUP_COORDINATOR)) == before
    assert not Group.objects.filter(name=cms_group_name(competition)).exists()
    assert get_image_model().objects.filter(collection=Collection.get_first_root_node()).exists()
    text = out.getvalue()
    assert "Próba na sucho" in text
    assert "koordynator-kwantowa@example.test" in text
    assert "zabieram" in text


def test_the_command_is_idempotent(kwantowa_only):
    call_command("scope_cms_access", stdout=io.StringIO())
    groups = set(kwantowa_only.groups.values_list("name", flat=True))
    out = io.StringIO()

    call_command("scope_cms_access", stdout=out)

    assert set(kwantowa_only.groups.values_list("name", flat=True)) == groups
    assert "bez zmian" in out.getvalue()


def test_the_command_rolls_back_when_a_coordinator_would_lose_something(kwantowa_only, competition):
    """Siatka bezpieczeństwa: strona **poza** witryną konkursu (na produkcji jej nie ma — ale gdyby
    była, koordynator by ją stracił). Komenda ma odmówić i nie zapisać niczego."""
    Page.get_first_root_node().add_child(instance=Page(title="Sierota", slug="sierota"))
    before = _snapshot(Group.objects.get(name=GROUP_COORDINATOR))

    with pytest.raises(CommandError, match="straciłby"):
        call_command("scope_cms_access", stdout=io.StringIO(), stderr=io.StringIO())

    assert _snapshot(Group.objects.get(name=GROUP_COORDINATOR)) == before
    assert not Group.objects.filter(name=cms_group_name(competition)).exists()


def test_before_the_command_nothing_changes(kwantowa_only, competition):
    """Wdrożenie bez komendy: grupa globalna ma prawa jak zawsze, żadna grupa konkursu nie powstaje,
    nowe pliki lądują w korzeniu, a zasięg ``/cms/`` jest bez ograniczeń."""
    from apps.cms.scope import cms_scope

    assert GroupPagePermission.objects.filter(group__name=GROUP_COORDINATOR).exists()
    assert not Group.objects.filter(name=cms_group_name(competition)).exists()
    assert upload_collection(competition).pk == Collection.get_first_root_node().pk
    assert cms_scope(_fresh(kwantowa_only)) is None


def test_after_the_command_new_uploads_land_in_the_competition_collection(kwantowa_only, competition):
    call_command("scope_cms_access", stdout=io.StringIO())

    assert upload_collection(competition).pk == ensure_collection(competition).pk


def test_a_coordinator_granted_after_the_command_gets_the_cms_group(kwantowa_only, competition):
    """Sygnały: rola nadana po komendzie daje ``/cms/`` bez ponownego przebiegu, a odebrana — zabiera."""
    call_command("scope_cms_access", stdout=io.StringIO())
    newcomer = UserFactory(email="nowy-koordynator@example.test")

    grant_role(newcomer, CompetitionRole.COORDINATOR, competition=competition)
    assert newcomer.groups.filter(name=cms_group_name(competition)).exists()

    newcomer.groups.remove(Group.objects.get(name=GROUP_COORDINATOR))
    assert not newcomer.groups.filter(name=cms_group_name(competition)).exists()


def test_memberships_follow_the_flag_switch(competition, other_competition):
    """Przełączenie ``memberships_enforced`` zmienia źródło roli — a razem z nim skład grupy."""
    call_command("scope_cms_access", "--root-media-to", competition.slug, stdout=io.StringIO())
    global_only = UserFactory(email="tylko-grupa@example.test", groups=[GROUP_COORDINATOR])
    assert global_only.groups.filter(name=cms_group_name(other_competition)).exists()

    other_competition.feature_flags = {"memberships_enforced": True}
    other_competition.save()

    assert not global_only.groups.filter(name=cms_group_name(other_competition)).exists()
    assert global_only.groups.filter(name=cms_group_name(competition)).exists()


def test_several_competitions_narrow_but_never_widen(competition, other_competition):
    """Przy dwóch konkursach różnica jest celem — ale wyłącznie w stronę „mniej”."""
    for row in (competition, other_competition):
        row.feature_flags = {"memberships_enforced": True}
        row.save()
    user = UserFactory(email="koordynator-obcy@example.test")
    grant_role(user, CompetitionRole.COORDINATOR, competition=other_competition)
    before = cms_abilities(_fresh(user))

    out = io.StringIO()
    call_command("scope_cms_access", stdout=out)
    after = cms_abilities(_fresh(user))

    assert after < before
    assert "zawężam" in out.getvalue()
    home_a = Page.objects.get(pk=competition.site.root_page_id)
    assert not home_a.permissions_for_user(_fresh(user)).can_edit()
    root_b = Page.objects.get(pk=other_competition.site.root_page_id)
    assert root_b.permissions_for_user(_fresh(user)).can_edit()
