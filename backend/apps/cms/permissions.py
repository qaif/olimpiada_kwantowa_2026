"""Kto redaguje który konkurs w ``/cms/``: grupa Django na konkurs i kolekcja mediów na konkurs.

Stan zastany (``docs/UNIWERSALNY-ETAP-2.md`` § 1.1.5) jest jeden i nazwany wprost: migracja
``cms.0003_coordinator_permissions`` kopiuje do **globalnej** grupy ``coordinator`` komplet
uprawnień wbudowanych grup Wagtaila ``Editors`` i ``Moderators`` — razem z ``GroupPagePermission``
na **korzeniu drzewa stron** i ``GroupCollectionPermission`` na **korzeniu kolekcji**. Koordynator
drugiego konkursu, dopisany do tej grupy przez ``create_competition --coordinator-email``, dostaje
więc prawo edycji i publikacji każdej strony każdego konkursu oraz wgląd we wszystkie obrazy
i dokumenty. Ten moduł jest odpowiedzią na to i składa się z trzech rzeczy:

- :func:`ensure_cms_group` — grupa ``cms:<slug>`` z tym samym zestawem uprawnień, tylko **zawężony**
  do poddrzewa witryny konkursu i do jego kolekcji,
- :func:`ensure_collection` — kolekcja mediów konkursu jako dziecko korzenia,
- :func:`upload_collection` — jedno wejście dla ``attachments.py`` i ``images.py``: do której
  kolekcji trafia nowo wgrany plik.

**Stan docelowy (wydanie „uprawnienia CMS per konkurs”, ``manage.py scope_cms_access``).**
Globalna grupa ``coordinator`` zostaje **znacznikiem roli** (od niej zależy ``has_role`` przy
wyłączonym ``memberships_enforced``), ale traci wszystkie uprawnienia ``/cms/``. Redaktorem
konkursu jest się wyłącznie przez grupę ``cms:<slug>``, a do niej wpisuje i z niej wypisuje
:func:`sync_user_cms_groups` — przy każdej zmianie roli koordynatora (sygnały w
``apps/cms/signals.py``). Szerokość „wszystkie konkursy” ma od tej pory jedno imię:
superkoordynator (:func:`ensure_super_coordinator_group`, ``apps.accounts.super_coordinator``).

**Cztery decyzje warte uzasadnienia.**

- **Uprawnienia są kopiowane z ``Editors`` i ``Moderators``, a nie wypisywane nazwami kodowymi.**
  Dokładnie tak, jak robi ``cms.0003`` — i z tego samego powodu: nazwy kodowe zmieniały się między
  wersjami Wagtaila (``permission`` zamiast ``permission_type`` w ``GroupPagePermission``), a lista
  przepisana ręcznie rozjechałaby się z tamtą migracją przy pierwszej aktualizacji. Kopia zamiast
  listy znaczy też, że grupa globalna sprzed komendy, grupa konkursu i grupa superkoordynatora
  mają **ten sam** zestaw czynności na stronach i mediach i różnią się wyłącznie zasięgiem.
- **Nic nie zmienia się samo przy wdrożeniu.** Dopóki ``scope_cms_access`` nie zabrało grupie
  ``coordinator`` praw do korzenia, instalacja działa jak przed wydaniem: grupy ``cms:<slug>``
  wyłącznie **dokładają**, a zasięg ``apps.cms.scope`` traktuje posiadacza praw do korzenia jako
  konto bez ograniczeń. Stan „po komendzie” rozpoznaje :func:`global_coordinator_scoped` —
  z samych danych, bez osobnego przełącznika, który mógłby się z nimi rozjechać.
- **Prefiks ``cms:`` jest zarezerwowany.** Grupa o takiej nazwie należy do systemu, a nie do
  administratora: :func:`ensure_cms_group` odtwarza jej uprawnienia przy każdym wywołaniu,
  a :func:`sync_user_cms_groups` wyznacza jej członków z roli koordynatora. Redaktor bez roli
  koordynatora dostaje **własną** grupę założoną w ``/cms/`` → Grupy, a nie wpis do ``cms:<slug>``,
  który przy najbliższej zmianie roli zniknie.
- **Komunikaty i ustawienia serwisu nie wchodzą do grupy konkursu.** Globalna grupa nigdy ich nie
  miała, a polecenie tego wydania brzmi „koordynator zachowuje dokładnie te same możliwości”.
  Zawężenie dla nich jest gotowe (``apps.cms.scope``, polityka komunikatów w
  ``apps/cms/wagtail_hooks.py``, ``GroupSitePermission`` dla ustawień), więc nadanie ich jednemu
  konkursowi jest wpisem w ``/cms/`` → Grupy → ``cms:<slug>``, a nie zmianą kodu.

**Czego ten moduł nie robi:** nie dotyka migracji ``cms.0003`` (historia migracji produkcji) i nie
zmienia polityki widoczności kolekcji (``apps/cms/views.py``). Pliki z korzenia kolekcji przenosi
wyłącznie komenda ``scope_cms_access`` (:func:`adopt_root_media`) — i tylko wtedy, gdy wiadomo,
czyje są.
"""

from __future__ import annotations

from django.contrib.auth.models import Group, Permission
from django.db.models import Q
from wagtail.models import Collection, GroupCollectionPermission, GroupPagePermission, Page

from apps.accounts.models import GROUP_COORDINATOR, GROUP_SUPER_COORDINATOR, CompetitionRole
from apps.cms.tenancy import resolve_competition

#: Prefiks nazwy grupy redakcyjnej konkursu. Zarezerwowany — patrz docstring modułu.
GROUP_PREFIX = "cms:"

#: Grupy wzorcowe zakładane przez ``wagtailcore.0002_initial_data``. Ta sama para, co w ``cms.0003``.
SOURCE_GROUPS = ("Editors", "Moderators")

#: Flaga konkursu z katalogu ``apps.tenancy.models.FEATURE_DEFAULTS`` (§ 0.6). Czytana **wyłącznie**
#: przez :func:`scoped_cms_permissions` — jedno miejsce odczytu, zgodnie z § 1.0 (c).
FEATURE = "scoped_cms_permissions"


def cms_group_name(competition) -> str:
    """Nazwa grupy redakcyjnej konkursu, np. ``cms:kwantowa``."""
    return f"{GROUP_PREFIX}{competition.slug}"


def scoped_cms_permissions(competition) -> bool:
    """Czy ten konkurs liczy uprawnienia ``/cms/`` po swojemu. Jedyny odczyt flagi w module.

    Dwie drogi do „tak”: flaga ``scoped_cms_permissions`` konkursu (tryb etapu 2 — grupa konkursu
    **obok** globalnej) albo instalacja po ``scope_cms_access`` (:func:`global_coordinator_scoped`),
    w której każdy konkurs jest zawężony, bo grupa globalna nie daje już niczego. Bez drugiej drogi
    konkurs założony po komendzie wgrywałby pliki do korzenia kolekcji, do którego jego koordynator
    nie ma prawa, i nie dostawałby koordynatora w swojej grupie.

    ``None`` (nie wiadomo, o który konkurs chodzi) znaczy „jak dziś”: korzeń drzewa i korzeń
    kolekcji. Odwrót jest miękki celowo — mowa o **wgrywaniu pliku**, a nie o konfiguracji
    konkursu, więc brak rozstrzygnięcia ma dać zachowanie sprzed etapu 2, a nie wyjątek w środku
    komendy seedującej (§ 1.0 (b)).
    """
    if competition is None:
        return False
    return competition.has_feature(FEATURE) or global_coordinator_scoped()


def global_coordinator_scoped() -> bool:
    """Czy ``scope_cms_access`` już zabrało globalnej grupie ``coordinator`` prawa w ``/cms/``.

    Stan liczony z danych, a nie zapisany obok: znacznikiem jest brak ``GroupPagePermission``
    grupy ``coordinator``. ``cms.0003`` wpisuje je każdej instalacji, więc „brak” znaczy wyłącznie
    „komenda przeszła” — albo operator zdjął je ręcznie w ``/cms/``, co jest tą samą decyzją.
    Przywrócenie praw w ``/cms/`` → Grupy przywraca też zachowanie sprzed komendy: jedno źródło
    prawdy, żadnej flagi, która mogłaby twierdzić co innego niż grupa.
    """
    return not GroupPagePermission.objects.filter(group__name=GROUP_COORDINATOR).exists()


def collection_name(competition) -> str:
    """Nazwa kolekcji mediów konkursu — to, co redaktor widzi na liście kolekcji w ``/cms/``.

    Nazwa, a nie identyfikator: kolekcja Wagtaila nie ma sluga, a lista kolekcji jest ekranem dla
    człowieka. Tożsamością kolekcji jest więc **nazwa wśród dzieci korzenia** i tak szuka jej
    :func:`ensure_collection`.
    """
    return competition.name


def ensure_collection(competition) -> Collection:
    """Kolekcja mediów konkursu jako dziecko korzenia; zakładana przy pierwszym wywołaniu.

    Wiersze **już wgrane zostają tam, gdzie są**: ta funkcja niczego nie przenosi. Przeniesienie
    biblioteki Konkursu #1 do nowej kolekcji byłoby zmianą widoczną w ``/cms/`` przy pierwszym
    otwarciu listy obrazów, a § 0.1 mówi o takich zmianach jednoznacznie.
    """
    root = Collection.get_first_root_node()
    name = collection_name(competition)
    existing = root.get_children().filter(name=name).order_by("path").first()
    if existing is not None:
        return existing
    return root.add_child(name=name)


def _source_groups() -> list[Group]:
    return list(Group.objects.filter(name__in=SOURCE_GROUPS))


def ensure_cms_group(competition) -> Group:
    """Grupa ``cms:<slug>`` z uprawnieniami zawężonymi do witryny i kolekcji tego konkursu.

    Trzy rzeczy, każda z innego powodu:

    1. ``Group.permissions`` (w tym ``wagtailadmin.access_admin``) — **kopiowane** z ``Editors``
       i ``Moderators``, dokładnie tak, jak robi ``cms.0003``. Bez ``access_admin`` ``/cms/``
       odpowiada przekierowaniem nawet komuś, kto ma prawa do stron,
    2. ``GroupPagePermission`` na ``competition.site.root_page`` — **nie** na korzeniu drzewa.
       To jest cała różnica wobec grupy globalnej: te same czynności, ale w poddrzewie jednego
       konkursu,
    3. ``GroupCollectionPermission`` na kolekcji konkursu (:func:`ensure_collection`).

    Przebieg jest idempotentny (``get_or_create`` / ``add``) i **wyłącznie addytywny**: żadnego
    wiersza nie kasuje, więc dwukrotne wywołanie nie odbiera nikomu dostępu. Instalacja bez grup
    wzorcowych (baza sprzed ``wagtailcore.0002_initial_data``) dostaje samą grupę, bez uprawnień —
    kopiowanie z pustki dałoby grupę bez ``access_admin``, czyli koordynatora bez panelu, i lepiej,
    żeby było to widać od razu.
    """
    group, _ = Group.objects.get_or_create(name=cms_group_name(competition))
    sources = _source_groups()
    if not sources:  # pragma: no cover - wagtailcore.0002_initial_data zawsze je tworzy
        return group

    root_page_id = competition.site.root_page_id
    collection = ensure_collection(competition)
    for source in sources:
        group.permissions.add(*source.permissions.all())
        for permission_id in GroupPagePermission.objects.filter(group=source).values_list(
            "permission_id", flat=True
        ):
            GroupPagePermission.objects.get_or_create(
                group=group, page_id=root_page_id, permission_id=permission_id
            )
        for permission_id in GroupCollectionPermission.objects.filter(group=source).values_list(
            "permission_id", flat=True
        ):
            GroupCollectionPermission.objects.get_or_create(
                group=group, collection=collection, permission_id=permission_id
            )
    return group


def upload_collection(competition=None) -> Collection:
    """Kolekcja, do której trafia **nowo** wgrany plik: konkursu albo korzeń.

    Jedno wejście dla ``apps.cms.attachments`` i ``apps.cms.images``, bo reguła „gdzie ląduje plik”
    ma być jedna — dwie kopie znaczyłyby bibliotekę, w której dokumenty konkursu leżą gdzie indziej
    niż jego obrazy. Korzeń jest odpowiedzią w dwóch przypadkach: flaga wyłączona (Konkurs #1, stan
    dzisiejszy) oraz konkurs nierozstrzygnięty (komenda uruchomiona bez kontekstu w instalacji bez
    konkursów).
    """
    resolved = resolve_competition(competition)
    if not scoped_cms_permissions(resolved):
        return Collection.get_first_root_node()
    return ensure_collection(resolved)


# --- superkoordynator -----------------------------------------------------------------------------

#: Uprawnienia modelowe, które superkoordynator ma **ponad** grupy wzorcowe: komunikaty (snippet),
#: ustawienia serwisu każdej witryny i wybór obrazu/dokumentu w edytorze. Trójki
#: ``(app_label, model, codename)``, bo ``Permission`` jest jednoznaczne dopiero z typem treści.
SUPER_COORDINATOR_MODEL_PERMISSIONS = (
    ("cms", "announcement", "add_announcement"),
    ("cms", "announcement", "change_announcement"),
    ("cms", "announcement", "delete_announcement"),
    ("cms", "sitesettings", "change_sitesettings"),
    ("wagtailimages", "image", "choose_image"),
    ("wagtaildocs", "document", "choose_document"),
)

#: Wybór obrazu i dokumentu Wagtail sprawdza **na kolekcji** (``choose`` w
#: ``CollectionOwnershipPermissionPolicy``), więc same uprawnienia modelowe nie wystarczą.
SUPER_COORDINATOR_COLLECTION_PERMISSIONS = (
    ("wagtailimages", "image", "choose_image"),
    ("wagtaildocs", "document", "choose_document"),
)


def _permissions(triples) -> list[Permission]:
    condition = Q(pk__in=[])
    for app_label, model, codename in triples:
        condition |= Q(content_type__app_label=app_label, content_type__model=model, codename=codename)
    return list(Permission.objects.filter(condition))


def ensure_super_coordinator_group() -> Group:
    """Grupa ``superkoordynator``: czynności grupy globalnej sprzed zawężenia, na całej instalacji.

    Strony i media — dokładnie to, co ``cms.0003`` wpisało grupie ``coordinator`` (kopie
    ``Editors`` + ``Moderators`` na korzeniu drzewa i korzeniu kolekcji), więc koordynator, który
    przed ``scope_cms_access`` dostanie tę rolę, **nie traci niczego**. Ponad to komunikaty,
    ustawienia serwisu i wybór mediów (:data:`SUPER_COORDINATOR_MODEL_PERMISSIONS`) — organizator
    platformy ma mieć w ``/cms/`` wszystko, co dotyczy treści konkursów, bez ``is_superuser``.
    Zarządzania kontami, grupami, witrynami i kolekcjami grupa **nie** dostaje.

    Idempotentna i wyłącznie addytywna, jak :func:`ensure_cms_group`.
    """
    group, _ = Group.objects.get_or_create(name=GROUP_SUPER_COORDINATOR)
    tree_root = Page.get_first_root_node()
    collection_root = Collection.get_first_root_node()
    for source in _source_groups():
        group.permissions.add(*source.permissions.all())
        for permission_id in GroupPagePermission.objects.filter(group=source).values_list(
            "permission_id", flat=True
        ):
            GroupPagePermission.objects.get_or_create(
                group=group, page=tree_root, permission_id=permission_id
            )
        for permission_id in GroupCollectionPermission.objects.filter(group=source).values_list(
            "permission_id", flat=True
        ):
            GroupCollectionPermission.objects.get_or_create(
                group=group, collection=collection_root, permission_id=permission_id
            )
    group.permissions.add(*_permissions(SUPER_COORDINATOR_MODEL_PERMISSIONS))
    for permission in _permissions(SUPER_COORDINATOR_COLLECTION_PERMISSIONS):
        GroupCollectionPermission.objects.get_or_create(
            group=group, collection=collection_root, permission=permission
        )
    return group


# --- członkostwo w grupie konkursu ----------------------------------------------------------------


def coordinators_of(competition):
    """Konta z rolą koordynatora **tego** konkursu — ta sama reguła, co ``has_role``, bez superroli.

    Przy włączonym ``memberships_enforced`` rozstrzyga wiersz ``Membership``, przy wyłączonym —
    globalna grupa ``coordinator`` (czyli dziś na produkcji: każdy jej członek jest koordynatorem
    Olimpiady Kwantowej). Superkoordynator jest tu **pominięty** celowo: ma prawa do korzenia
    przez własną grupę i wpis do ``cms:<slug>`` niczego by mu nie dał, a zaśmiecałby listę
    redaktorów konkursu w ``/cms/`` → Grupy.
    """
    from apps.accounts.models import User
    from apps.accounts.services import memberships_enforced

    if memberships_enforced(competition):
        rows = User.objects.filter(
            memberships__competition=competition, memberships__role=CompetitionRole.COORDINATOR
        )
    else:
        rows = User.objects.filter(groups__name=GROUP_COORDINATOR)
    return rows.distinct()


def sync_user_cms_groups(user) -> tuple[list[str], list[str]]:
    """Wpisuje konto do grup ``cms:<slug>`` konkursów, które koordynuje, i wypisuje z pozostałych.

    Dotyczy wyłącznie konkursów zawężonych (:func:`scoped_cms_permissions`): w konkursie, który
    jeszcze działa „po staremu”, dostęp daje grupa globalna i członkostwo w ``cms:<slug>`` nie
    zmieniałoby niczego poza listą w ``/cms/``. Zwraca nazwy grup dopisanych i wypisanych — do
    komunikatu komendy i do testów.

    **Czyta konkursy wąsko** (``values_list`` identyfikatora, sluga i flag), a pełny wiersz tylko
    wtedy, gdy trzeba założyć grupę. Funkcję woła sygnał zmiany grup konta, a ten odpala także
    kod uruchamiany na bazie w stanie **starszej** migracji (testy migracji danych, kopia bazy
    w trakcie ``migrate``) — pełny ``SELECT`` po modelu z dzisiejszymi kolumnami wywracałby się
    tam na kolumnie, której jeszcze nie ma. Przed ``scope_cms_access`` (grupa globalna z prawami
    do korzenia) i bez flagi etapu 2 funkcja kończy się na dwóch zapytaniach i niczego nie pisze.
    """
    from apps.accounts.models import Membership
    from apps.accounts.super_coordinator import forget
    from apps.tenancy.models import FEATURE_DEFAULTS, Competition

    def flag(flags, name):
        return bool((flags or {}).get(name, FEATURE_DEFAULTS[name]))

    rows = list(Competition.objects.order_by("pk").values_list("pk", "slug", "feature_flags"))
    everything = global_coordinator_scoped() if rows else False
    rows = [row for row in rows if everything or flag(row[2], FEATURE)]
    if not rows:
        return [], []

    added: list[str] = []
    removed: list[str] = []
    current = set(user.groups.filter(name__startswith=GROUP_PREFIX).values_list("name", flat=True))
    in_global_group = user.groups.filter(name=GROUP_COORDINATOR).exists()
    for pk, slug, flags in rows:
        name = f"{GROUP_PREFIX}{slug}"
        if flag(flags, "memberships_enforced"):
            is_coordinator = Membership.objects.filter(
                user=user, competition_id=pk, role=CompetitionRole.COORDINATOR
            ).exists()
        else:
            is_coordinator = in_global_group
        should = user.is_active and is_coordinator
        if should and name not in current:
            # ``only``: te same kolumny, których potrzebuje ``ensure_cms_group`` — patrz docstring.
            competition = Competition.objects.only("pk", "slug", "name", "site", "feature_flags").get(pk=pk)
            user.groups.add(ensure_cms_group(competition))
            added.append(name)
        elif not should and name in current:
            user.groups.remove(Group.objects.get(name=name))
            removed.append(name)
    if added or removed:
        forget(user)
    return added, removed


def sync_competition_cms_group(competition) -> tuple[list[str], list[str]]:
    """Wersja hurtowa :func:`sync_user_cms_groups` dla jednego konkursu — do komendy.

    Zwraca adresy e-mail dopisanych i wypisanych. Konta nieaktywne nie są dopisywane (nie mają
    dziś żadnej roli), ale **nie są** też wypisywane tylko dlatego, że są nieaktywne: blokada konta
    ma być odwracalna jednym kliknięciem, bez odtwarzania uprawnień.
    """
    from apps.accounts.models import User

    group = ensure_cms_group(competition)
    wanted = set(coordinators_of(competition).filter(is_active=True).values_list("pk", flat=True))
    members = set(group.user_set.values_list("pk", flat=True))
    coordinators = set(coordinators_of(competition).values_list("pk", flat=True))
    to_add = wanted - members
    to_remove = members - coordinators
    if to_add:
        group.user_set.add(*to_add)
    if to_remove:
        group.user_set.remove(*to_remove)
    emails = dict(User.objects.filter(pk__in=to_add | to_remove).values_list("pk", "email"))
    return sorted(emails[pk] for pk in to_add), sorted(emails[pk] for pk in to_remove)


# --- zawężenie grupy globalnej --------------------------------------------------------------------


def is_cms_permission(permission: Permission) -> bool:
    """Czy uprawnienie modelowe dotyczy ``/cms/`` — czyli Wagtaila albo modeli ``apps.cms``.

    Po tym kryterium ``scope_cms_access`` zdejmuje uprawnienia z grupy ``coordinator``; każde inne
    (gdyby operator dopisał grupie coś spoza ``/cms/``) zostaje, bo polecenie mówi o redakcji
    treści, a nie o roli.
    """
    app_label = permission.content_type.app_label
    return app_label.startswith("wagtail") or app_label == "cms"


def global_coordinator_cms_rows() -> dict[str, list]:
    """Co grupa ``coordinator`` ma dziś w ``/cms/`` — to, co zabierze :func:`strip_global_coordinator`."""
    group = Group.objects.filter(name=GROUP_COORDINATOR).first()
    if group is None:
        return {"permissions": [], "pages": [], "collections": []}
    return {
        "permissions": [
            permission
            for permission in group.permissions.select_related("content_type").order_by("codename")
            if is_cms_permission(permission)
        ],
        "pages": list(GroupPagePermission.objects.filter(group=group).select_related("page", "permission")),
        "collections": list(
            GroupCollectionPermission.objects.filter(group=group).select_related("collection", "permission")
        ),
    }


def strip_global_coordinator() -> dict[str, int]:
    """Zabiera globalnej grupie ``coordinator`` wszystkie uprawnienia ``/cms/``. Sama grupa zostaje.

    Grupa zostaje, bo jest **rolą**: ``has_role`` przy wyłączonym ``memberships_enforced`` i kilka
    bramek zbiorów danych pytają o nią wprost. Znika wyłącznie to, co dotyczy redakcji treści —
    wiersze praw do stron i kolekcji oraz uprawnienia modelowe Wagtaila i ``apps.cms``.
    """
    rows = global_coordinator_cms_rows()
    group = Group.objects.filter(name=GROUP_COORDINATOR).first()
    if group is None:
        return {"permissions": 0, "pages": 0, "collections": 0}
    group.permissions.remove(*rows["permissions"])
    GroupPagePermission.objects.filter(pk__in=[row.pk for row in rows["pages"]]).delete()
    GroupCollectionPermission.objects.filter(pk__in=[row.pk for row in rows["collections"]]).delete()
    return {key: len(value) for key, value in rows.items()}


def adopt_root_media(competition) -> dict[str, int]:
    """Przenosi obrazy i dokumenty leżące w **korzeniu** kolekcji do kolekcji konkursu.

    Po zawężeniu korzeń kolekcji nie należy do nikogo poza superkoordynatorem — plik, który
    w nim zostanie, zniknie z biblioteki koordynatora. W instalacji z jednym konkursem odpowiedź
    „czyj jest ten plik” jest jedna i komenda przenosi wszystko sama; przy kilku konkursach
    wskazuje go operator (``--root-media-to``). Adres obrazu i dokumentu nie zależy od kolekcji,
    więc przeniesienie nie zmienia niczego na stronach.

    Ograniczenia widoczności korzenia (``CollectionViewRestriction``) przechodzą na kolekcję
    konkursu, zanim pliki się przeniosą — inaczej dokument „tylko dla zalogowanych” stałby się po
    przeniesieniu publiczny.
    """
    from wagtail.documents import get_document_model
    from wagtail.images import get_image_model
    from wagtail.models import CollectionViewRestriction

    root = Collection.get_first_root_node()
    target = ensure_collection(competition)
    for restriction in CollectionViewRestriction.objects.filter(collection=root):
        copy, created = CollectionViewRestriction.objects.get_or_create(
            collection=target,
            restriction_type=restriction.restriction_type,
            defaults={"password": restriction.password},
        )
        if created:
            copy.groups.set(restriction.groups.all())
    images = get_image_model().objects.filter(collection=root).update(collection=target)
    documents = get_document_model().objects.filter(collection=root).update(collection=target)
    return {"images": images, "documents": documents}


# --- macierz możliwości ---------------------------------------------------------------------------

#: Czynności na stronie i metoda ``PagePermissionTester``, która na nie odpowiada.
PAGE_ACTIONS = (
    ("add", "can_add_subpage"),
    ("change", "can_edit"),
    ("publish", "can_publish"),
    ("lock", "can_lock"),
    ("unlock", "can_unlock"),
    ("delete", "can_delete"),
    ("unpublish", "can_unpublish"),
    ("move", "can_move"),
    ("copy", "can_copy"),
    ("reorder", "can_reorder_children"),
    ("publish_subpage", "can_publish_subpage"),
    ("view_restrictions", "can_set_view_restrictions"),
)
MEDIA_ACTIONS = ("change", "delete", "choose")


def cms_abilities(user) -> frozenset[tuple]:
    """Komplet tego, co to konto może zrobić w ``/cms/`` z **istniejącą treścią** — jako zbiór krotek.

    Do porównania „przed i po” w ``scope_cms_access`` i w testach. Zbiór obejmuje każdą stronę
    poniżej korzenia drzewa, każdy obraz i dokument, każdy komunikat i ustawienia każdej witryny,
    razem z wejściem do panelu i możliwością wgrania pliku. **Nie** obejmuje dwóch rzeczy, które
    zawężenie zmienia z założenia: korzenia drzewa (dodanie nowej witryny obok istniejących jest
    czynnością operatora platformy) i tego, **do której** kolekcji trafia nowy plik.

    Konto trzeba podać świeżo pobrane z bazy — Wagtail zapamiętuje uprawnienia na obiekcie konta
    i porównanie na tym samym obiekcie przed i po zmianie grup porównałoby pamięć, a nie bazę.
    """
    from wagtail.contrib.settings.models import BaseSiteSetting  # noqa: F401 - rejestr polityk
    from wagtail.documents import get_document_model
    from wagtail.images import get_image_model
    from wagtail.models import Site
    from wagtail.permissions import policy_registry

    from apps.cms.models import Announcement, SiteSettings

    rows: set[tuple] = set()
    if user.has_perm("wagtailadmin.access_admin"):
        rows.add(("admin", "access"))
    for page in Page.objects.filter(depth__gt=1).order_by("path"):
        tester = page.permissions_for_user(user)
        for action, method in PAGE_ACTIONS:
            if getattr(tester, method)():
                rows.add(("page", page.pk, action))
    for kind, model in (("image", get_image_model()), ("document", get_document_model())):
        policy = policy_registry.get_by_type(model)
        if policy.user_has_permission(user, "add"):
            rows.add((kind, "upload"))
        for item in model.objects.select_related("collection").order_by("pk"):
            for action in MEDIA_ACTIONS:
                if policy.user_has_permission_for_instance(user, action, item):
                    rows.add((kind, item.pk, action))
    announcements = policy_registry.get_by_type(Announcement)
    for announcement in Announcement.objects.order_by("pk"):
        for action in ("change", "delete"):
            if announcements.user_has_permission_for_instance(user, action, announcement):
                rows.add(("announcement", announcement.pk, action))
    settings_policy = policy_registry.get_by_type(SiteSettings)
    for site in Site.objects.order_by("pk"):
        if settings_policy.user_has_permission_for_instance(user, "change", site):
            rows.add(("site_settings", site.pk, "change"))
    return frozenset(rows)
