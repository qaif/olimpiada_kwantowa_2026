"""Menu części informacyjnej dla ``templates/base.html``.

Nawigacja nie jest zabezpieczeniem (tak samo jak ``apps.web.context_processors.roles``) – to tylko
lista publicznych stron Wagtaila oznaczonych „pokaż w menu”. Menu składa się z drzewa **witryny
z żądania** (``Site.find_for_request``) i tak było od początku – ta część była poprawna jeszcze
przed wielokonkursowością (``docs/UNIWERSALNY-ETAP-1.md`` § 3.7, „piąty, mniej oczywisty”).

Poprawki wymagała lista zapasowa. ``FALLBACK_MENU`` to cztery adresy, które w bazie założyła
migracja ``apps.cms.0002`` – czyli drzewo **Konkursu #1**, a nie „menu każdego serwisu”. Dlatego
sięgamy po nią wyłącznie wtedy, gdy żądanie trafiło w witrynę **domyślną**: to jest ta jedna
witryna, o której wiadomo, że te adresy w niej istnieją. Konkurs, którego drzewo stron dopiero
powstaje, dostaje menu **puste** – nagłówek bez pozycji jest wtedy uczciwy, a nagłówek z cudzymi
adresami prowadziłby jego czytelników na cztery strony, których pod tą domeną nie ma.

Szablon bazowy nigdy nie może wywrócić się przez CMS: błąd bazy i brak drzewa kończą się pustym
menu albo listą zapasową, nigdy wyjątkiem.

Jedna pozycja menu ma listę rozwijaną: sekcja dokumentów (``DocumentIndexPage``). Rozwijamy
**wyłącznie** ten typ, a nie „każdą stronę menu, która ma dzieci”: newsroom też ma dzieci i pod
regułą ogólną wysypałby do nagłówka wszystkie aktualności, a archiwum – wszystkie edycje.
Dzieci czytamy jednym zapytaniem dla całego menu, więc dołożenie kolejnego dokumentu nie dokłada
zapytania do każdej strony serwisu.
"""

from __future__ import annotations

import logging

from django.db import DatabaseError
from django.db.models import Q

logger = logging.getLogger(__name__)

#: Pozycje, które stoją w **przyklejonym pasku** obok logotypu, a nie w dolnym menu serwisu:
#: to, czego uczestnik szuka najczęściej i w trakcie pracy z długim dokumentem (zadania, terminy,
#: warsztaty). Dobór jest po slugu strony, nie po tytule, bo tytuł redakcja może zmienić.
PRIMARY_MENU_SLUGS = ("zadania", "harmonogram", "warsztaty", "kontakt")

#: Zapasowe menu = dokładnie te ścieżki, które tworzy migracja drzewa stron **witryny domyślnej**.
#: Stała zostaje nietknięta (pilnuje jej ``test_menu_matches_seeded_tree``); zmieniło się to, komu
#: wolno ją pokazać – patrz docstring modułu.
FALLBACK_MENU = (
    {"title": "Aktualności", "url": "/aktualnosci/"},
    {"title": "Zadania", "url": "/zadania/"},
    {"title": "Archiwum", "url": "/archiwum/"},
    {"title": "Wyniki", "url": "/wyniki/"},
)


def _expandable_children(pages: list, request) -> dict[int, list[dict]]:
    """Opublikowane dzieci stron-indeksów dokumentów, w jednym zapytaniu na całe menu.

    Klucz to identyfikator rodzica. Strony bez rozwijanej listy w wyniku nie występują, więc
    ``dict.get`` w pętli menu daje pustą listę i szablon rysuje zwykły odnośnik.
    """
    from django.contrib.contenttypes.models import ContentType
    from wagtail.models import Page

    from .models import DocumentIndexPage

    index_type = ContentType.objects.get_for_model(DocumentIndexPage)
    parents = [page for page in pages if page.content_type_id == index_type.pk]
    if not parents:
        return {}

    # Dzieci = potomkowie o głębokości rodzica + 1. Warunek na ``path`` jest indeksowany
    # (treebeard trzyma ścieżkę materializowaną), więc zapytanie zostaje jedno niezależnie
    # od liczby rozwijanych pozycji.
    query = Q()
    for parent in parents:
        query |= Q(path__startswith=parent.path, depth=parent.depth + 1)

    found: dict[int, list[dict]] = {parent.pk: [] for parent in parents}
    for child in Page.objects.live().filter(query).order_by("path"):
        parent = next((item for item in parents if child.path.startswith(item.path)), None)
        if parent is None:  # pragma: no cover - filtr wyżej nie przepuszcza obcych ścieżek
            continue
        url = child.get_url(request=request)
        found[parent.pk].append({"title": child.title, "url": url, "active": request.path == url})
    return found


def cms_menu(request) -> dict:
    """Menu serwisu złożone z drzewa witryny, w którą trafiło żądanie.

    ``fallback_allowed`` rozstrzyga o liście zapasowej: pokazujemy ją wyłącznie dla witryny domyślnej,
    bo tylko o jej drzewie wiadomo, że ma te cztery adresy (patrz docstring modułu). Rozstrzygamy
    to **przed** pętlą i na obiekcie, który i tak mamy w ręku – ``is_default_site`` jest kolumną
    tego samego wiersza, więc nie kosztuje ani jednego zapytania więcej.
    """
    from wagtail.models import Page, Site

    fallback_allowed = False
    try:
        site = Site.find_for_request(request)
        if site is None:
            raise Site.DoesNotExist
        fallback_allowed = site.is_default_site
        pages = list(Page.objects.live().in_menu().child_of(site.root_page).order_by("path"))
        children = _expandable_children(pages, request)
        items = []
        for page in pages:
            url = page.get_url(request=request)
            kids = children.get(page.pk, [])
            items.append(
                {
                    "title": page.title,
                    "url": url,
                    "children": kids,
                    # Pozycja rodzica jest podświetlona także wtedy, gdy czytelnik stoi na jej
                    # dziecku – inaczej na stronie regulaminu nagłówek nie wskazywałby niczego.
                    "active": request.path == url or any(kid["active"] for kid in kids),
                    "primary": page.slug in PRIMARY_MENU_SLUGS,
                }
            )
    except (DatabaseError, Site.DoesNotExist, AttributeError):  # pragma: no cover - baza bez drzewa
        # Witryny nie znamy, więc nie wiemy też, czy to ta domyślna – a lista zapasowa opisuje
        # wyłącznie jej drzewo. Puste menu jest tu jedyną odpowiedzią, która nie może być cudza.
        logger.warning("Menu CMS niedostępne – nagłówek zostaje bez pozycji.")
        items = []
    fallback = (
        [
            {
                **item,
                "children": [],
                "active": request.path == item["url"],
                "primary": item["url"].strip("/") in PRIMARY_MENU_SLUGS,
            }
            for item in FALLBACK_MENU
        ]
        if fallback_allowed
        else []
    )
    menu = items or fallback
    # Osobna lista dla przyklejonego paska zamiast filtrowania w szablonie: pasek i menu serwisu
    # czytają to samo źródło, a pasek pokazuje swoje pozycje dopiero po przyklejeniu (skrypt
    # static/js/sticky-bar.js) – dolne menu zostaje w pełnym składzie.
    return {
        "cms_menu": menu,
        "cms_menu_primary": [item for item in menu if item["primary"]],
    }
