"""Zasięg redaktora w ``/cms/``: które konkursy, witryny, strony i kolekcje są „jego”.

Wagtail sam zawęża to, co umie zawęzić: drzewo stron liczy z ``GroupPagePermission``, obrazy
i dokumenty z ``GroupCollectionPermission``, ustawienia witryny z ``GroupSitePermission``. Tego,
czego zawęzić nie umie, jest kilka i każde kończy się tym samym wyciekiem — **tytułem cudzego
obiektu** na ekranie redaktora innego konkursu:

- komunikaty (snippet ``cms.Announcement``) — uprawnienie modelowe obejmuje wszystkie wiersze,
- dziennik zdarzeń (``wagtailcore.ModelLogEntry``) — ``viewable_by_user`` wpuszcza wpis każdego
  obiektu typu, do którego ma się **jakiekolwiek** uprawnienie, więc redaktor z prawem do obrazów
  swojej kolekcji czyta nazwy obrazów wszystkich kolekcji,
- wybór strony (``choose-page``) — pokazuje całe drzewo każdemu, kto ma ``access_admin``,
- raport „Użycie typów stron” — liczby i tytuł ostatnio edytowanej strony z całej instalacji.

Ten moduł odpowiada na jedno pytanie — :func:`cms_scope` — a haki, widoki i warstwa pośrednia
(``apps/cms/wagtail_hooks.py``, ``apps/cms/middleware.py``) tylko z niego czytają.

**Zasięg liczymy z uprawnień Wagtaila, a nie z ról konkursu.** „Twoje konkursy” to konkursy,
w których drzewie stron masz jakiekolwiek ``GroupPagePermission``. Źródło jest jedno: to, co
redaktor może edytować w drzewie, wyznacza też, czyje komunikaty i czyje wpisy dziennika widzi —
a nie osobna lista, która mogłaby się z drzewem rozjechać.

**``None`` znaczy „bez ograniczeń”** i dostają go trzy rodzaje kont: superużytkownik,
superkoordynator (``apps.accounts.super_coordinator``) oraz każdy, czyja grupa ma prawa na
**korzeniu drzewa stron** — w tym globalna grupa ``coordinator`` **przed** uruchomieniem
``scope_cms_access``. Ostatni przypadek jest warunkiem wstecznej zgodności: dopóki komenda nie
zabrała grupie praw do korzenia, koordynator widzi w ``/cms/`` dokładnie to, co widział przed
tym wydaniem, bez żadnego dodatkowego filtra.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import CharField, Q, Subquery
from django.db.models.functions import Cast

#: Atrybut konta, pod którym zapamiętujemy zasięg na czas żądania — tak samo jak Wagtail trzyma
#: tam swoje ``_page_permission_cache``. Klucz czyści ``apps.accounts.super_coordinator.forget``.
CACHE_ATTR = "_cms_scope"


@dataclass(frozen=True)
class CmsScope:
    """Zasięg redaktora z ograniczeniami. Zbiory, a nie querysety: liczone raz na żądanie."""

    #: Ścieżki treebearda stron, na których grupy tego konta mają ``GroupPagePermission``.
    page_paths: tuple[str, ...]
    #: Kolekcje z uprawnieniami — razem z potomkami, bo tak dziedziczy je Wagtail.
    collection_ids: frozenset[int]
    competition_ids: frozenset[int]
    site_ids: frozenset[int]

    def covers_path(self, path: str) -> bool:
        """Czy strona o tej ścieżce leży w poddrzewie, do którego redaktor ma prawa."""
        return any(path.startswith(root) for root in self.page_paths)

    def leads_to(self, path: str) -> bool:
        """Czy strona jest **przodkiem** poddrzewa redaktora — trzeba przez nią przejść w wyborze."""
        return any(root.startswith(path) for root in self.page_paths)

    def visible_path(self, path: str) -> bool:
        return self.covers_path(path) or self.leads_to(path)


def _empty() -> CmsScope:
    return CmsScope(
        page_paths=(), collection_ids=frozenset(), competition_ids=frozenset(), site_ids=frozenset()
    )


def is_unrestricted(user) -> bool:
    """Czy to konto widzi ``/cms/`` bez ograniczeń (patrz docstring modułu)."""
    return cms_scope(user) is None


def cms_scope(user) -> CmsScope | None:
    """Zasięg konta w ``/cms/`` albo ``None`` = bez ograniczeń. Zapamiętany na obiekcie konta."""
    if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
        return _empty()
    if user.is_superuser:
        return None
    if hasattr(user, CACHE_ATTR):
        return getattr(user, CACHE_ATTR)
    scope = _compute(user)
    setattr(user, CACHE_ATTR, scope)
    return scope


def _compute(user) -> CmsScope | None:
    from wagtail.models import Collection, GroupCollectionPermission, GroupPagePermission, Page, Site

    from apps.accounts.super_coordinator import is_super_coordinator
    from apps.tenancy.models import Competition

    if is_super_coordinator(user):
        return None

    page_paths = tuple(
        sorted(set(GroupPagePermission.objects.filter(group__user=user).values_list("page__path", flat=True)))
    )
    # Prawa na korzeniu drzewa obejmują wszystkie witryny naraz — to jest dokładnie stan grupy
    # ``coordinator`` sprzed ``scope_cms_access`` i nie ma czego dalej zawężać.
    if any(len(path) == Page.steplen for path in page_paths):
        return None

    collection_paths = set(
        GroupCollectionPermission.objects.filter(group__user=user).values_list("collection__path", flat=True)
    )
    collection_ids: frozenset[int] = frozenset()
    if collection_paths:
        condition = Q()
        for path in collection_paths:
            condition |= Q(path__startswith=path)
        collection_ids = frozenset(Collection.objects.filter(condition).values_list("pk", flat=True))

    scope = CmsScope(
        page_paths=page_paths,
        collection_ids=collection_ids,
        competition_ids=frozenset(),
        site_ids=frozenset(),
    )
    site_ids = frozenset(
        site_id
        for site_id, root_path in Site.objects.values_list("pk", "root_page__path")
        if root_path and scope.visible_path(root_path) and not _is_tree_root(root_path)
    )
    competition_ids = frozenset(Competition.objects.filter(site_id__in=site_ids).values_list("pk", flat=True))
    return CmsScope(
        page_paths=page_paths,
        collection_ids=collection_ids,
        competition_ids=competition_ids,
        site_ids=site_ids,
    )


def _is_tree_root(path: str) -> bool:
    from wagtail.models import Page

    return len(path) == Page.steplen


# --- filtry dla haków ----------------------------------------------------------------------------


def filter_pages(pages, user):
    """Strony widoczne w wyborze strony: poddrzewo redaktora i droga do niego od korzenia."""
    scope = cms_scope(user)
    if scope is None:
        return pages
    if not scope.page_paths:
        return pages.none()
    from wagtail.models import Page

    condition = Q()
    ancestors: set[str] = set()
    for root in scope.page_paths:
        condition |= Q(path__startswith=root)
        ancestors.update(root[:end] for end in range(Page.steplen, len(root), Page.steplen))
    if ancestors:
        condition |= Q(path__in=ancestors)
    return pages.filter(condition)


def page_visible(page, user) -> bool:
    scope = cms_scope(user)
    return scope is None or scope.visible_path(page.path)


def filter_by_competition(queryset, user, field: str = "competition_id"):
    """Wiersze konkursów redaktora. Wiersz bez konkursu widzi wyłącznie konto bez ograniczeń."""
    scope = cms_scope(user)
    if scope is None:
        return queryset
    return queryset.filter(**{f"{field}__in": scope.competition_ids})


def competition_in_scope(competition_id, user) -> bool:
    scope = cms_scope(user)
    return scope is None or (competition_id is not None and competition_id in scope.competition_ids)


def filter_log_entries(queryset, user):
    """Wpisy ``ModelLogEntry`` wyłącznie o obiektach z zasięgu redaktora.

    Biała lista typów, a nie czarna: redaktor z ograniczeniami widzi wpisy o obrazach i dokumentach
    ze swoich kolekcji, o komunikatach swoich konkursów i o ustawieniach swoich witryn. Wpis
    o czymkolwiek innym (witryna, grupa, kolekcja, konto) jest wpisem o obiekcie **platformy**
    i w dzienniku redaktora jednego konkursu nie ma czego szukać.
    """
    scope = cms_scope(user)
    if scope is None:
        return queryset
    from django.contrib.contenttypes.models import ContentType
    from wagtail.documents import get_document_model
    from wagtail.images import get_image_model

    from apps.cms.models import Announcement, SiteSettings

    def as_text(rows):
        return Subquery(rows.annotate(_pk_text=Cast("pk", output_field=CharField())).values("_pk_text"))

    Image = get_image_model()
    Document = get_document_model()
    types = ContentType.objects.get_for_models(Image, Document, Announcement, SiteSettings)
    allowed = (
        Q(
            content_type=types[Image],
            object_id__in=as_text(Image.objects.filter(collection_id__in=scope.collection_ids)),
        )
        | Q(
            content_type=types[Document],
            object_id__in=as_text(Document.objects.filter(collection_id__in=scope.collection_ids)),
        )
        | Q(
            content_type=types[Announcement],
            object_id__in=as_text(Announcement.objects.filter(competition_id__in=scope.competition_ids)),
        )
        | Q(
            content_type=types[SiteSettings],
            object_id__in=as_text(SiteSettings.objects.filter(site_id__in=scope.site_ids)),
        )
    )
    return queryset.filter(allowed)


def install_log_entry_scope() -> None:
    """Podpina :func:`filter_log_entries` pod ``ModelLogEntryManager.viewable_by_user``.

    Dlaczego podmiana metody, a nie własny widok: z ``viewable_by_user`` czyta raport „Historia
    serwisu” **i** trzy listy jego filtrów (autorzy, typy, czynności — ``audit_logging.py``), a adresy
    raportu montuje Wagtail przed hakami ``register_admin_urls``, więc własny widok pod tym samym
    adresem wymagałby zmiany ``config/urls.py``. Jedna podmiana zawęża wszystkie cztery miejsca
    naraz i każde przyszłe, które zapyta o ten sam zbiór. Test
    ``test_the_log_entry_queryset_is_scoped`` pilnuje, że podmiana nadal działa po aktualizacji
    Wagtaila (zmiana nazwy metody zakończy się czerwonym testem, a nie cichym wyciekiem).

    Wywołanie jest idempotentne: znacznik na funkcji chroni przed podwójnym opakowaniem przy
    ponownym ``ready()`` (testy, autoreload).
    """
    from wagtail.models.audit_log import ModelLogEntryManager

    # Metoda jest zdefiniowana w ``BaseLogEntryManager``; przypisanie na ``ModelLogEntryManager``
    # zmienia wyłącznie dziennik modeli — dziennik stron (``PageLogEntryManager``) ma własną
    # implementację, liczoną z drzewa, i zostaje nietknięty.
    original = ModelLogEntryManager.viewable_by_user
    if getattr(original, "_competition_scoped", False):
        return

    def viewable_by_user(self, user):
        return filter_log_entries(original(self, user), user)

    viewable_by_user._competition_scoped = True
    viewable_by_user.__wrapped__ = original
    viewable_by_user.__doc__ = original.__doc__
    ModelLogEntryManager.viewable_by_user = viewable_by_user


def install_page_filter_scope() -> None:
    """Zawęża listy wyboru w filtrach listy stron (eksplorator, przepływy): witryny, właściciele, edytujący.

    ``PageFilterSet`` Wagtaila buduje te listy z **całej** instalacji — pod stroną główną konkursu
    A redaktor widziałby w filtrze „Witryna” nazwę witryny konkursu B, a w „Właściciel”
    i „Edytował” — nazwiska redaktorów B. Zawężamy do witryn z zasięgu i do osób, które są
    właścicielami albo autorami edycji stron z poddrzewa redaktora. Filtry są atrybutami klasy
    współdzielonymi przez ``PageFilterSet`` i ``GenericPageFilterSet``, więc jedna podmiana
    obejmuje obie; znacznik chroni przed podwójnym opakowaniem.
    """
    from wagtail.admin.views.pages.listing import PageFilterSet
    from wagtail.models import Page, PageLogEntry

    def pages_in_scope(user_scope):
        condition = Q(pk__in=[])
        for root in user_scope.page_paths:
            condition |= Q(path__startswith=root)
        return Page.objects.filter(condition)

    def sites(queryset, user_scope):
        return queryset.filter(pk__in=user_scope.site_ids)

    def owners(queryset, user_scope):
        return queryset.filter(pk__in=pages_in_scope(user_scope).order_by().values("owner_id"))

    def editors(queryset, user_scope):
        edits = PageLogEntry.objects.filter(action="wagtail.edit", page__in=pages_in_scope(user_scope))
        return queryset.filter(pk__in=edits.order_by().values("user_id"))

    for name, narrow in (("site", sites), ("owner", owners), ("edited_by", editors)):
        declared = PageFilterSet.declared_filters.get(name)
        if declared is None or getattr(declared, "_competition_scoped", False):
            continue
        original = declared.queryset

        def scoped(request, original=original, narrow=narrow):
            queryset = original(request) if callable(original) else original.all()
            user_scope = cms_scope(getattr(request, "user", None))
            if user_scope is None:
                return queryset
            return narrow(queryset, user_scope)

        declared.queryset = scoped
        declared._competition_scoped = True
