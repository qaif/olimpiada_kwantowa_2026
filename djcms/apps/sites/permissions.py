"""Uprawnienia redaktorów per konkurs: grupy ``redakcja:*``, ``GlobalPagePermission``, foldery (D5, S12).

**Lustro ``/cms/`` aplikacji głównej.** Kto redaguje który konkurs, rozstrzyga aplikacja główna
(``backend/apps/cms/djcms_sso.py``) i przysyła wynik w tokenie SSO; tutaj ten wynik zamienia się
w członkostwo w grupach, a grupy – w uprawnienia django CMS i filera:

- ``redakcja:<slug>`` – strony witryny konkursu: dodawanie, edycja, usuwanie, przenoszenie
  **i publikacja** (odpowiednik ``cms:<slug>`` z Wagtaila: kopia grup ``Editors`` + ``Moderators``),
- ``redakcja:<slug>:bez-publikacji`` – to samo bez publikacji (konto, które w ``/cms/`` edytuje,
  ale nie publikuje – np. własna grupa na wzór ``Editors``). Bez tej grupy takie konto dostałoby
  tu więcej, niż ma w aplikacji głównej, albo nic. Usuwanie i przenoszenie – jak w Wagtailu tylko
  stron bez opublikowanej wersji w poddrzewie (``apps.sites.live_guard``),
- ``redakcja:platforma`` – wszystkie witryny (``GlobalPagePermission`` bez listy witryn), dla kont
  bez ograniczeń w ``/cms/`` (superkoordynator, superużytkownik aplikacji głównej).

**Wyłącznie ``GlobalPagePermission`` z listą witryn, nigdy ``PagePermission``**: bufor uprawnień
django CMS 5.1.3 (``cms/cache/permissions.py::get_cache_key``) nie ma witryny w kluczu – uprawnienie
na stronie jednej witryny przeciekałoby do drzewa drugiej (DJ-02 § 5.1). Uprawnienia globalne
liczy ``get_global_actions_for_user(user, site)`` – z witryną.

**Poza grupami zostaje zarządzanie dostępem**: uprawnienia stron i folderów, konta i grupy CMS-a
(``EXCLUDED_MODELS``) oraz ``can_change_permissions`` i ``can_change_advanced_settings``. Konta
i grupy zmienia wyłącznie techniczny superużytkownik (``bootstrap_djcms_admin``).

**Filer** (``FILER_ENABLE_PERMISSIONS = True``): folder najwyższego poziomu konkursu
``Konkurs: <nazwa> (<slug>)`` (ten sam, do którego importuje ``apps.importer``) z ``FolderPermission``
dla obu grup konkursu (odczyt, zmiana, podfoldery – razem z potomkami); konkurs domyślny dostaje
też folder importu sprzed DJ-02 (``Import z Wagtaila`` w korzeniu). Folder ``Wspólne`` – do odczytu
dla każdego redaktora (zapis: platforma). Pliki są i tak **wszystkie publiczne** (``apps.blocks.files``):
uprawnienia folderów porządkują bibliotekę redakcji, a nie ukrywają plików przed czytelnikiem.

Zestaw uprawnień modelowych grup jest **zastępowany** przy każdym ``ensure_*`` (``permissions.set``):
stan grupy ma wynikać z kodu, a nie z kliknięć w panelu.
"""

from __future__ import annotations

from collections.abc import Iterable

from django.contrib.auth.models import Group, Permission
from django.db import transaction
from django.db.models import Q

GROUP_PREFIX = "redakcja:"
PLATFORM_GROUP = "redakcja:platforma"
DRAFT_SUFFIX = ":bez-publikacji"
SHARED_FOLDER_NAME = "Wspólne"

#: Aplikacje, których modele redaktor edytuje (strony, wtyczki, rozszerzenia, wersje, pliki, przekierowania).
EDITOR_APP_LABELS = (
    "cms",
    "djangocms_text",
    "djangocms_versioning",
    "filer",
    "dj_pages",
    "dj_blocks",
    "dj_live",
    "dj_seo",
)

#: Zarządzanie dostępem i modele wewnętrzne – poza każdą grupą redakcji.
EXCLUDED_MODELS = frozenset(
    {
        ("cms", "urlconfrevision"),
        ("cms", "globalpagepermission"),
        ("cms", "pagepermission"),
        ("cms", "pageuser"),
        ("cms", "pageusergroup"),
        ("filer", "folderpermission"),
        # Schowek filera jest per konto (``Clipboard.user``), ale panel pokazuje każdy schowek temu,
        # kto ma uprawnienie modelowe – także cudzy, z nazwami plików innych konkursów. Filer 3.6
        # schowka nie używa (wgrywanie: ``filer.add_file`` + prawo do folderu), więc redakcji
        # niczego nie zabiera.
        ("filer", "clipboard"),
        ("filer", "clipboarditem"),
        # Presety miniatur są wspólne dla wszystkich witryn – zmienia je platforma, nie redakcja konkursu.
        ("filer", "thumbnailoption"),
        ("dj_pages", "loginattempt"),
    }
)
#: Uprawnienia do zarządzania uprawnieniami stron – poza grupami, choć leżą na modelu ``Page``.
EXCLUDED_CODENAMES = frozenset({"change_page_permissions"})
#: Czego nie dostaje grupa ``…:bez-publikacji`` (flaga ``can_publish`` i uprawnienie Django).
PUBLISH_CODENAMES = frozenset({"publish_page"})


def group_name(slug: str, *, publish: bool = True) -> str:
    return f"{GROUP_PREFIX}{slug}" if publish else f"{GROUP_PREFIX}{slug}{DRAFT_SUFFIX}"


def editor_permissions(*, publish: bool = True) -> list[Permission]:
    rows = Permission.objects.filter(content_type__app_label__in=EDITOR_APP_LABELS).select_related(
        "content_type"
    )
    excluded_codenames = EXCLUDED_CODENAMES if publish else EXCLUDED_CODENAMES | PUBLISH_CODENAMES
    return [
        perm
        for perm in rows
        if (perm.content_type.app_label, perm.content_type.model) not in EXCLUDED_MODELS
        and perm.codename not in excluded_codenames
    ]


def _page_permission(group: Group, *, publish: bool, sites: Iterable) -> None:
    """Jeden ``GlobalPagePermission`` grupy z dokładnie tymi flagami i witrynami (idempotentnie)."""
    from cms.models import GlobalPagePermission

    values = {
        "can_add": True,
        "can_change": True,
        "can_delete": True,
        "can_move_page": True,
        "can_publish": publish,
        "can_change_advanced_settings": False,
        "can_change_permissions": False,
        "can_view": False,
    }
    rows = list(GlobalPagePermission.objects.filter(group=group, user__isnull=True).order_by("pk"))
    permission = rows[0] if rows else GlobalPagePermission(group=group)
    for extra in rows[1:]:
        extra.delete()
    for name, value in values.items():
        setattr(permission, name, value)
    permission.save()
    permission.sites.set(list(sites))


def _folder_permission(folder, group, *, read: bool, edit: bool) -> None:
    from filer.models import FolderPermission

    allow = FolderPermission.ALLOW
    FolderPermission.objects.update_or_create(
        folder=folder,
        group=group,
        user=None,
        everybody=False,
        defaults={
            "type": FolderPermission.ALL,
            "can_read": allow if read else None,
            "can_edit": allow if edit else None,
            "can_add_children": allow if edit else None,
        },
    )


def ensure_shared_folder():
    """Folder ``Wspólne`` w korzeniu – odczyt dla każdego redaktora (``everybody``), zapis: platforma."""
    from filer.models import Folder, FolderPermission

    folder = Folder.objects.filter(name=SHARED_FOLDER_NAME, parent__isnull=True).order_by("pk").first()
    if folder is None:
        folder = Folder.objects.create(name=SHARED_FOLDER_NAME)
    FolderPermission.objects.update_or_create(
        folder=folder,
        group=None,
        user=None,
        everybody=True,
        defaults={
            "type": FolderPermission.ALL,
            "can_read": FolderPermission.ALLOW,
            "can_edit": None,
            "can_add_children": None,
        },
    )
    return folder


@transaction.atomic
def ensure_platform_group() -> Group:
    """``redakcja:platforma``: wszystkie witryny (lista pusta = wszystkie) i wszystkie foldery."""
    from filer.models import FolderPermission

    group, _ = Group.objects.get_or_create(name=PLATFORM_GROUP)
    group.permissions.set(editor_permissions())
    _page_permission(group, publish=True, sites=())
    allow = FolderPermission.ALLOW
    FolderPermission.objects.update_or_create(
        folder=None,
        group=group,
        user=None,
        everybody=False,
        defaults={
            "type": FolderPermission.ALL,
            "can_read": allow,
            "can_edit": allow,
            "can_add_children": allow,
        },
    )
    ensure_shared_folder()
    return group


@transaction.atomic
def ensure_site_permissions(competition) -> tuple[Group, Group]:
    """Obie grupy konkursu, ich ``GlobalPagePermission`` na witrynie i uprawnienia do folderów."""
    from apps.importer.services import competition_folder, legacy_import_folder

    groups = []
    for publish in (True, False):
        group, _ = Group.objects.get_or_create(name=group_name(competition.slug, publish=publish))
        group.permissions.set(editor_permissions(publish=publish))
        _page_permission(group, publish=publish, sites=[competition.site])
        groups.append(group)
    folders = [competition_folder(competition)]
    if competition.is_default:
        legacy = legacy_import_folder()
        if legacy is not None:
            folders.append(legacy)
    for folder in folders:
        for group in groups:
            _folder_permission(folder, group, read=True, edit=True)
    return groups[0], groups[1]


def ensure_all() -> dict[str, int]:
    """Wszystko naraz – ``setup_djcms_groups`` przy wdrożeniu. Zwraca liczby do komunikatu."""
    from .models import CompetitionSite

    ensure_platform_group()
    sites = list(CompetitionSite.objects.select_related("site").order_by("slug"))
    for competition in sites:
        ensure_site_permissions(competition)
    return {"competitions": len(sites)}


# --- członkostwo z tokenu SSO ------------------------------------------------------------------------


def wanted_groups(*, platform: bool, competitions: dict[str, frozenset[str]]) -> list[Group]:
    """Grupy konta według tokenu. Konkurs nieznany rejestrowi albo wygaszony – pominięty."""
    from .models import CompetitionSite

    if platform:
        group = Group.objects.filter(name=PLATFORM_GROUP).first() or ensure_platform_group()
        return [group]
    result = []
    for competition in CompetitionSite.objects.filter(
        slug__in=list(competitions), is_active=True
    ).select_related("site"):
        publish = "publish" in competitions[competition.slug]
        name = group_name(competition.slug, publish=publish)
        existing = Group.objects.filter(name=name).first()
        if existing is None:
            ensure_site_permissions(competition)
        result.append(existing or Group.objects.get(name=name))
    return result


def apply_grants(user, *, platform: bool, competitions: dict[str, frozenset[str]]) -> None:
    """**Zastępuje** grupy i uprawnienia konta tymi z tokenu i czyści bufory uprawnień.

    Wszystkie grupy – nie tylko ``redakcja:*``: konto SSO nie ma mieć niczego, czego aplikacja
    główna mu nie dała (także grupy dopisanej ręcznie w panelu djcms).
    """
    user.groups.set(wanted_groups(platform=platform, competitions=competitions))
    user.user_permissions.clear()
    forget_permissions(user)


def forget_permissions(user) -> None:
    """Bufory uprawnień w tym procesie: django CMS (strony), filer (foldery), Django (na obiekcie)."""
    from cms.cache.permissions import clear_user_permission_cache
    from cms.utils.permissions import clear_permission_lru_caches
    from filer.cache import clear_folder_permission_cache

    clear_user_permission_cache(user)
    clear_permission_lru_caches(user)
    clear_folder_permission_cache(user)
    for attr in (
        "_perm_cache",
        "_user_perm_cache",
        "_group_perm_cache",
        "_cms_user_sites",
        EDITABLE_SITES_ATTR,
    ):
        if hasattr(user, attr):
            delattr(user, attr)


# --- zasięg redaktora ----------------------------------------------------------------------------

#: Atrybut konta z zapamiętanym zasięgiem na czas żądania.
EDITABLE_SITES_ATTR = "_dj_editable_site_ids"


def editable_site_ids(user) -> set[int] | None:
    """Witryny, które konto może redagować; ``None`` = wszystkie (superużytkownik, uprawnienie bez witryn).

    Zasięg wprost z ``GlobalPagePermission`` konta i jego grup – to samo źródło, z którego django CMS
    liczy uprawnienia do stron, więc zawężenie panelu nie może rozjechać się z uprawnieniami.
    """
    if user.is_superuser:
        return None
    if hasattr(user, EDITABLE_SITES_ATTR):
        cached: set[int] | None = getattr(user, EDITABLE_SITES_ATTR)
        return cached
    from cms.models import GlobalPagePermission

    found: set[int] = set()
    unrestricted = False
    permissions = (
        GlobalPagePermission.objects.filter(Q(user=user) | Q(group__user=user))
        .filter(can_change=True)
        .prefetch_related("sites")
        .distinct()
    )
    for permission in permissions:
        sites = [site.pk for site in permission.sites.all()]
        if not sites:
            unrestricted = True
            break
        found.update(sites)
    ids = None if unrestricted else found
    setattr(user, EDITABLE_SITES_ATTR, ids)
    return ids


def is_platform_editor(user) -> bool:
    """Konto platformy: superużytkownik albo uprawnienie bez listy witryn (grupa ``redakcja:platforma``).

    To samo, co znaczy ``platform: true`` w tokenie SSO (``apply_grants`` daje wtedy ``PLATFORM_GROUP``).
    Konto anonimowe albo brak konta – ``False``.
    """
    return bool(user is not None and user.is_authenticated and editable_site_ids(user) is None)


# --- witryna obiektu panelu --------------------------------------------------------------------------


def object_site_id(obj) -> int | None:
    """Witryna, do której należy obiekt panelu django CMS, albo ``None`` (obiekt bez witryny).

    Strona → ``site``; treść strony → jej strona; placeholder → jego źródło; wtyczka → jej
    placeholder; wersja (djangocms-versioning) → jej treść; rozszerzenie strony/treści → obiekt
    rozszerzany. Łańcuch najwyżej kilku kroków – każdy krok to jeden odczyt relacji.
    """
    from cms.models import CMSPlugin, Page, PageContent, Placeholder

    for _step in range(6):
        if obj is None:
            return None
        if isinstance(obj, Page):
            return obj.site_id
        if isinstance(obj, PageContent):
            obj = obj.page
        elif isinstance(obj, Placeholder):
            obj = obj.source
        elif isinstance(obj, CMSPlugin):
            obj = obj.placeholder
        elif hasattr(obj, "extended_object"):
            obj = obj.extended_object
        elif obj.__class__.__module__.startswith("djangocms_versioning") and hasattr(obj, "content"):
            obj = obj.content
        else:
            return None
    return None
