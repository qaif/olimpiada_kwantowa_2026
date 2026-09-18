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

**Trzy decyzje warte uzasadnienia.**

- **Uprawnienia są kopiowane z ``Editors`` i ``Moderators``, a nie wypisywane nazwami kodowymi.**
  Dokładnie tak, jak robi ``cms.0003`` — i z tego samego powodu: nazwy kodowe zmieniały się między
  wersjami Wagtaila (``permission`` zamiast ``permission_type`` w ``GroupPagePermission``), a lista
  przepisana ręcznie rozjechałaby się z tamtą migracją przy pierwszej aktualizacji. Kopia zamiast
  listy znaczy też, że obie grupy — globalna i konkursowa — mają **ten sam** zestaw czynności
  i różnią się wyłącznie zasięgiem.
- **Grupa ``cms:<slug>`` dokłada, nigdy nie odbiera.** Koordynator Konkursu #1 zostaje w grupie
  ``coordinator`` i jego zestaw uprawnień nie zmienia się o ani jeden wiersz (§ 0.5 punkt 20,
  test w ``apps/cms/tests/test_cms_scope.py``). Odebranie globalnej grupy koordynatorom **obcych**
  konkursów jest osobną, jawną komendą ``manage.py scope_cms_access``.
- **Prefiks ``cms:`` jest zarezerwowany.** Grupa o takiej nazwie należy do systemu, a nie do
  administratora: :func:`ensure_cms_group` odtwarza jej uprawnienia przy każdym wywołaniu, więc
  ręczna zmiana zestawu w ``/cms/`` zniknie przy najbliższym przebiegu. Dwukropek w nazwie grupy
  jest tu celowy — nie da się go wpisać przez pomyłkę w nazwie grupy własnej administratora.

**Czego ten moduł nie robi:** nie przenosi istniejących obrazów ani dokumentów między kolekcjami
(biblioteka Konkursu #1 zostaje tam, gdzie jest — przeniesienie byłoby zmianą widoczną w ``/cms/``),
nie dotyka migracji ``cms.0003``, nie odbiera nikomu niczego i nie zmienia polityki widoczności
kolekcji (``apps/cms/views.py``).
"""

from __future__ import annotations

from django.contrib.auth.models import Group
from wagtail.models import Collection, GroupCollectionPermission, GroupPagePermission

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

    ``None`` (nie wiadomo, o który konkurs chodzi) znaczy „jak dziś”: korzeń drzewa i korzeń
    kolekcji. Odwrót jest miękki celowo — mowa o **wgrywaniu pliku**, a nie o konfiguracji
    konkursu, więc brak rozstrzygnięcia ma dać zachowanie sprzed etapu 2, a nie wyjątek w środku
    komendy seedującej (§ 1.0 (b)).
    """
    return competition is not None and competition.has_feature(FEATURE)


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
