"""Grupa redakcyjna i kolekcja mediów dla konkursów, które mają już włączone zawężone ``/cms/``.

Backfill do ``apps.cms.permissions`` (T17, ``docs/UNIWERSALNY-ETAP-2.md`` § 1.1.5). Od tego wydania
grupa ``cms:<slug>`` i kolekcja konkursu powstają przy zakładaniu konkursu i przy pierwszym
wgraniu pliku; ta migracja robi to samo dla konkursów **już stojących w bazie**, żeby instalacja
odtworzona ze zrzutu nie czekała z uprawnieniami na pierwszy zapis.

**Dotyczy wyłącznie konkursów z włączoną flagą ``scoped_cms_permissions``** i to jest cała treść
tej decyzji. Konkurs #1 ma tę flagę wyłączoną, więc migracja nie zakłada mu ani grupy, ani
kolekcji — a gdyby zakładała, redaktor Olimpiady Kwantowej zobaczyłby w ``/cms/`` nową pozycję na
liście kolekcji, czyli zmianę widoczną bez polecenia organizatora (§ 0.1, § 0.5 punkt 20).
Na dzisiejszej produkcji przebieg jest więc pusty i taki ma być.

Uwagi implementacyjne, wzięte z ``cms.0002_initial_tree``:

- migracja **nie importuje** modeli aplikacji ani ``wagtail.models`` — korzysta wyłącznie z modeli
  historycznych. Cena: ścieżkę treebearda nowej kolekcji (``path``/``depth``/``numchild``) liczymy
  ręcznie, bo historyczny ``Collection`` nie ma metod ``MP_Node``,
- przebieg jest idempotentny (``get_or_create``) i **wyłącznie addytywny** — żadnego wiersza nie
  kasuje, więc powtórzenie niczego nie odbiera,
- ``elidable=False``: to jest backfill danych, a nie krok, który wolno pominąć przy spłaszczaniu
  historii migracji.
"""

from django.db import migrations

#: Flaga z katalogu ``apps.tenancy.models.FEATURE_DEFAULTS``. Powtórzona literałem, bo migracja
#: opisuje stan z chwili swojego powstania i nie ma prawa czytać dzisiejszego kodu.
FEATURE = "scoped_cms_permissions"

#: Grupy wzorcowe Wagtaila — ta sama para, co w ``cms.0003_coordinator_permissions``.
SOURCE_GROUPS = ("Editors", "Moderators")

#: Prefiks nazwy grupy redakcyjnej konkursu (``apps.cms.permissions.GROUP_PREFIX``).
GROUP_PREFIX = "cms:"

#: Rozmiar segmentu ścieżki treebearda (``Collection.steplen``) i jego alfabet.
STEPLEN = 4
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _step(index: int) -> str:
    """``index`` (liczony od 1) jako segment ścieżki treebearda, np. 1 → ``0001``."""
    digits = ""
    value = index
    while value:
        value, remainder = divmod(value, len(ALPHABET))
        digits = ALPHABET[remainder] + digits
    return digits.rjust(STEPLEN, "0")


def _child_count(Collection, parent) -> int:
    """Ilu bezpośrednich potomków ma węzeł — liczone z drzewa, nie z ``parent.numchild``.

    Licznik bywa nieaktualny na bazie, po której ktoś chodził ręcznie, a od tej liczby zależy
    i ścieżka nowego węzła, i ``numchild`` zapisywany na końcu.
    """
    return Collection.objects.filter(depth=parent.depth + 1, path__startswith=parent.path).count()


def _ensure_collection(Collection, root, name: str):
    existing = (
        Collection.objects.filter(depth=root.depth + 1, path__startswith=root.path, name=name)
        .order_by("path")
        .first()
    )
    if existing is not None:
        return existing
    index = _child_count(Collection, root) + 1
    collection = Collection.objects.create(
        name=name,
        path=root.path + _step(index),
        depth=root.depth + 1,
        numchild=0,
    )
    Collection.objects.filter(pk=root.pk).update(numchild=_child_count(Collection, root))
    return collection


def grant(apps, schema_editor):
    Competition = apps.get_model("tenancy", "Competition")
    Group = apps.get_model("auth", "Group")
    Collection = apps.get_model("wagtailcore", "Collection")
    GroupPagePermission = apps.get_model("wagtailcore", "GroupPagePermission")
    GroupCollectionPermission = apps.get_model("wagtailcore", "GroupCollectionPermission")

    sources = list(Group.objects.filter(name__in=SOURCE_GROUPS))
    root = Collection.objects.filter(depth=1).order_by("path").first()
    if not sources or root is None:  # pragma: no cover - obie rzeczy zakłada wagtailcore
        return

    page_permission_ids = set(
        GroupPagePermission.objects.filter(group__in=sources).values_list("permission_id", flat=True)
    )
    collection_permission_ids = set(
        GroupCollectionPermission.objects.filter(group__in=sources).values_list(
            "permission_id", flat=True
        )
    )

    for competition in Competition.objects.select_related("site").all():
        if not (competition.feature_flags or {}).get(FEATURE, False):
            continue
        group, _ = Group.objects.get_or_create(name=f"{GROUP_PREFIX}{competition.slug}")
        for source in sources:
            group.permissions.add(*source.permissions.all())
        collection = _ensure_collection(Collection, root, competition.name)
        for permission_id in page_permission_ids:
            GroupPagePermission.objects.get_or_create(
                group=group,
                page_id=competition.site.root_page_id,
                permission_id=permission_id,
            )
        for permission_id in collection_permission_ids:
            GroupCollectionPermission.objects.get_or_create(
                group=group,
                collection_id=collection.pk,
                permission_id=permission_id,
            )


def revoke(apps, schema_editor):
    """Odwrót jest pusty — i to jest decyzja, a nie przeoczenie.

    Skasowanie grup ``cms:<slug>`` przy cofaniu migracji odebrałoby dostęp do ``/cms/`` ludziom,
    których komenda ``scope_cms_access`` zdążyła przenieść z grupy globalnej: cofnięcie wydania
    zostawiłoby ich bez żadnej grupy. Reguła etapu jest jedna — **nikomu nie odbieramy uprawnień
    automatycznie** (§ 1.1.5). Grupa i kolekcja, których przy wyłączonej fladze nikt nie czyta,
    nic nie kosztują; ich usunięcie jest decyzją administratora w ``/cms/``.
    """


class Migration(migrations.Migration):
    dependencies = [
        ("cms", "0022_backfill_announcement_competition"),
        ("tenancy", "0001_initial"),
    ]

    operations = [migrations.RunPython(grant, revoke, elidable=False)]
