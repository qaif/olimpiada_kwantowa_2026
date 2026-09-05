"""Drzewo stron części informacyjnej i domyślna witryna Wagtaila.

Struktura (T-09): ``Root`` → ``HomePage`` (``/``) → ``Aktualności`` (``/aktualnosci/``),
``Zadania`` (``/zadania/``), ``Archiwum`` (``/archiwum/``), ``Wyniki`` (``/wyniki/``).

Uwagi implementacyjne:

- migracja **nie importuje** modeli aplikacji ani ``wagtail.models`` – korzysta wyłącznie z modeli
  historycznych (``apps.get_model``). Cena: ścieżki treebearda (``path``/``depth``/``numchild``)
  liczymy ręcznie, bo historyczny ``Page`` nie ma metod ``MP_Node``. Zysk: migracja opisuje stan
  z chwili jej powstania i nie wywróci się, gdy modele później się zmienią,
- migracja jest **idempotentna względem stanu początkowego Wagtaila**: strona powitalna „Welcome to
  your new Wagtail site!” z ``wagtailcore.0002_initial_data`` jest usuwana, a domyślna ``Site``
  przestawiana na naszą stronę główną (nie tworzymy drugiej – ``is_default_site`` byłby wtedy
  niejednoznaczny),
- hostname bierze się z ``SITE_DOMAIN``; to tylko wartość początkowa. Docelowo domenę zmienia
  redaktor w ``/cms/`` (Ustawienia → Witryny), więc migracja nie nadpisuje istniejącego wpisu
  przy ponownym uruchomieniu na bazie, gdzie strony już są.
"""

import uuid

from django.conf import settings
from django.db import migrations

#: Rozmiar segmentu ścieżki treebearda (``Page.steplen``). Zmiana w Wagtailu wymagałaby migracji
#: całego drzewa, więc traktujemy ją jako stałą kontraktu.
STEPLEN = 4
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

#: Strony pierwszego poziomu: (model, slug, tytuł, pola własne). Kolejność wyznacza menu.
#: Pola własne podajemy jawnie – historyczny model nie zna ``default`` z ``blocks.py``.
SECTIONS = (
    ("NewsIndexPage", "aktualnosci", "Aktualności", {"intro": ""}),
    ("ProblemsPage", "zadania", "Zadania", {"intro": "", "closed_notice": "", "body": "[]"}),
    ("ArchiveIndexPage", "archiwum", "Archiwum", {"intro": ""}),
    ("ResultsPage", "wyniki", "Wyniki", {"intro": ""}),
)

HOME_SLUG = "home"
HOME_TITLE = "Platforma Olimpiady"
HOME_HERO = "Olimpiada"


def _step(index: int) -> str:
    """``index`` (liczony od 1) jako segment ścieżki treebearda, np. 1 → ``0001``."""
    digits = ""
    value = index
    while value:
        value, remainder = divmod(value, len(ALPHABET))
        digits = ALPHABET[remainder] + digits
    return digits.rjust(STEPLEN, "0")


def _child_count(Page, parent) -> int:
    """Ilu bezpośrednich potomków ma węzeł. Historyczny ``Page`` nie ma metod ``MP_Node``.

    Liczymy z drzewa, a nie z ``parent.numchild``: licznik bywa nieaktualny na bazie, po której
    ktoś chodził ręcznie, a od tej liczby zależy zarówno ścieżka nowego węzła, jak i ``numchild``
    zapisywany na końcu. Warunek na ``path`` musi zostać razem z ``depth`` – samo ``depth``
    złapałoby dzieci innego korzenia, gdyby takie kiedyś powstały.
    """
    return Page.objects.filter(depth=parent.depth + 1, path__startswith=parent.path).count()


def _content_type(ContentType, model: str):
    content_type, _ = ContentType.objects.get_or_create(app_label="cms", model=model.lower())
    return content_type


def _page_defaults(*, title, slug, url_path, path, depth, content_type, locale, sort_order=None):
    return {
        "title": title,
        "draft_title": title,
        "slug": slug,
        "content_type": content_type,
        "live": True,
        "has_unpublished_changes": False,
        "url_path": url_path,
        "seo_title": "",
        "show_in_menus": sort_order is not None,
        "search_description": "",
        "expired": False,
        "locked": False,
        "path": path,
        "depth": depth,
        "numchild": 0,
        "locale": locale,
        "translation_key": uuid.uuid4(),
    }


def create_tree(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Locale = apps.get_model("wagtailcore", "Locale")
    Page = apps.get_model("wagtailcore", "Page")
    Site = apps.get_model("wagtailcore", "Site")
    HomePage = apps.get_model("cms", "HomePage")

    if HomePage.objects.exists():
        # Drzewo już istnieje (np. migracja odtwarzana na żywej bazie) – nie dotykamy treści.
        return

    locale = Locale.objects.order_by("pk").first()
    if locale is None:
        locale = Locale.objects.create(language_code=settings.LANGUAGE_CODE)

    root = Page.objects.filter(depth=1).order_by("path").first()
    if root is None:  # pragma: no cover - wagtailcore.0002_initial_data zawsze go tworzy
        raise RuntimeError("Brak korzenia drzewa stron Wagtaila.")

    # Strona powitalna Wagtaila jest przykładem, nie treścią – zwalniamy po niej miejsce w drzewie.
    Page.objects.filter(depth=2, slug=HOME_SLUG, content_type__app_label="wagtailcore").delete()

    # Pierwszy wolny segment liczymy ze stanu drzewa, a nie literałem ``_step(1)``: gdyby pod
    # korzeniem stała już jakaś strona (inna witryna, strona utworzona przed tą migracją),
    # ścieżka ``0001`` byłaby zajęta, a treebeard dostałby dwa węzły o tej samej ścieżce.
    next_step = _child_count(Page, root) + 1
    home_path = root.path + _step(next_step)
    home = HomePage.objects.create(
        hero_title=HOME_HERO,
        hero_text="",
        show_timeline=True,
        **_page_defaults(
            title=HOME_TITLE,
            slug=HOME_SLUG,
            url_path=f"{root.url_path}{HOME_SLUG}/",
            path=home_path,
            depth=2,
            content_type=_content_type(ContentType, "homepage"),
            locale=locale,
        ),
    )

    for index, (model_name, slug, title, extra) in enumerate(SECTIONS, start=1):
        model = apps.get_model("cms", model_name)
        model.objects.create(
            **extra,
            **_page_defaults(
                title=title,
                slug=slug,
                url_path=f"{home.url_path}{slug}/",
                path=home_path + _step(index),
                depth=3,
                content_type=_content_type(ContentType, model_name),
                locale=locale,
                sort_order=index,
            ),
        )

    # ``numchild`` liczony z drzewa, nie literałem: przy korzeniu z rodzeństwem ``1`` byłoby
    # wprost nieprawdą, a treebeard trzyma ten licznik jako fakt (używa go m.in. ``add_child``).
    Page.objects.filter(pk=home.pk).update(numchild=_child_count(Page, home))
    Page.objects.filter(pk=root.pk).update(numchild=_child_count(Page, root))

    # Domyślna witryna. Uwaga: skasowanie strony powitalnej kaskaduje na ``Site`` z
    # ``wagtailcore.0002_initial_data`` (``root_page`` ma ``on_delete=CASCADE``), więc zwykle
    # trafiamy tu z pustą tabelą. Gałąź ``else`` zostaje na wypadek bazy, gdzie ktoś ustawił
    # własną witrynę wcześniej – nie chcemy wtedy drugiego wpisu z ``is_default_site``.
    site = Site.objects.filter(is_default_site=True).first()
    hostname = getattr(settings, "SITE_DOMAIN", "") or "localhost"
    site_name = getattr(settings, "WAGTAIL_SITE_NAME", "") or HOME_TITLE
    if site is None:
        Site.objects.create(
            hostname=hostname,
            port=80,
            site_name=site_name,
            root_page_id=home.pk,
            is_default_site=True,
        )
    else:
        site.hostname = hostname
        site.port = 80
        site.site_name = site_name
        site.root_page_id = home.pk
        site.save()


def remove_tree(apps, schema_editor):
    """Wycofanie: usuwamy tylko strony utworzone tutaj. Witrynę zostawiamy – wskaże korzeń."""
    Page = apps.get_model("wagtailcore", "Page")
    Site = apps.get_model("wagtailcore", "Site")
    root = Page.objects.filter(depth=1).order_by("path").first()
    if root is None:  # pragma: no cover
        return
    Site.objects.filter(is_default_site=True).update(root_page_id=root.pk)
    Page.objects.filter(content_type__app_label="cms").delete()
    Page.objects.filter(pk=root.pk).update(numchild=_child_count(Page, root))


class Migration(migrations.Migration):
    dependencies = [
        ("cms", "0001_initial"),
        ("wagtailcore", "0094_alter_page_locale"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(create_tree, remove_tree)]
