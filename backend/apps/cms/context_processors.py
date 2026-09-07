"""Menu części informacyjnej dla ``templates/base.html``.

Nawigacja nie jest zabezpieczeniem (tak samo jak ``apps.web.context_processors.roles``) – to tylko
lista publicznych stron Wagtaila oznaczonych „pokaż w menu”. Jeśli drzewo stron jeszcze nie
istnieje (świeża baza przed migracją danych) albo zapytanie się nie powiedzie, wracamy do stałej
listy adresów utworzonych przez migrację ``apps.cms.0002`` – szablon bazowy nigdy nie może
wywrócić się przez CMS.

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

#: Zapasowe menu = dokładnie te ścieżki, które tworzy migracja drzewa stron.
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
    from wagtail.models import Page, Site

    try:
        site = Site.find_for_request(request)
        if site is None:
            raise Site.DoesNotExist
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
                }
            )
    except (DatabaseError, Site.DoesNotExist, AttributeError):  # pragma: no cover - baza bez drzewa
        logger.warning("Menu CMS niedostępne – używam listy zapasowej.")
        items = []
    fallback = [{**item, "children": [], "active": request.path == item["url"]} for item in FALLBACK_MENU]
    return {"cms_menu": items or fallback}
