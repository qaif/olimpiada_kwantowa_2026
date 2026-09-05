"""Menu części informacyjnej dla ``templates/base.html``.

Nawigacja nie jest zabezpieczeniem (tak samo jak ``apps.web.context_processors.roles``) – to tylko
lista publicznych stron Wagtaila oznaczonych „pokaż w menu”. Jeśli drzewo stron jeszcze nie
istnieje (świeża baza przed migracją danych) albo zapytanie się nie powiedzie, wracamy do stałej
listy adresów utworzonych przez migrację ``apps.cms.0002`` – szablon bazowy nigdy nie może
wywrócić się przez CMS.
"""

from __future__ import annotations

import logging

from django.db import DatabaseError

logger = logging.getLogger(__name__)

#: Zapasowe menu = dokładnie te ścieżki, które tworzy migracja drzewa stron.
FALLBACK_MENU = (
    {"title": "Aktualności", "url": "/aktualnosci/"},
    {"title": "Zadania", "url": "/zadania/"},
    {"title": "Archiwum", "url": "/archiwum/"},
    {"title": "Wyniki", "url": "/wyniki/"},
)


def cms_menu(request) -> dict:
    from wagtail.models import Page, Site

    try:
        site = Site.find_for_request(request)
        if site is None:
            raise Site.DoesNotExist
        pages = Page.objects.live().in_menu().child_of(site.root_page).order_by("path")
        items = [{"title": page.title, "url": page.get_url(request=request)} for page in pages]
    except (DatabaseError, Site.DoesNotExist, AttributeError):  # pragma: no cover - baza bez drzewa
        logger.warning("Menu CMS niedostępne – używam listy zapasowej.")
        items = []
    return {"cms_menu": items or list(FALLBACK_MENU)}
