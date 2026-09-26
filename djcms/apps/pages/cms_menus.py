"""Menu serwisu ``dj.`` – modyfikator menu django CMS czytający ``MenuExtension`` (§ 6.3 docs/tasks/DJ-01.md).

Menu Wagtaila (``backend/apps/cms/context_processors.py``) ma trzy reguły zaszyte w kodzie:
pozycje w przyklejonym pasku (``PRIMARY_MENU_SLUGS``), lista rozwijana wyłącznie dla spisu
dokumentów (``DocumentIndexPage``) i dokument wyniesiony z tej listy do menu
(``PROMOTED_DOCUMENT_SLUGS``). Na ``dj.`` te same reguły są polami strony (``MenuExtension``),
a resztę – widoczność („w nawigacji”), tytuł w menu, kolejność – daje django CMS z drzewa stron.

Modyfikator działa w dwóch fazach wywołania ``show_menu``:

- **przed cięciem** (``post_cut=False``) dopisuje do węzłów flagi z ``MenuExtension``
  (``attr["dj_primary"|"dj_expand"|"dj_promote"]``) – jednym zapytaniem na całe menu,
  zapamiętanym na żądaniu, bo rama woła ``show_menu`` dwa razy (pasek i menu serwisu),
- **po cięciu** (``post_cut=True``, lista węzłów korzenia) układa pozycje jak w Wagtailu: strona
  główna pierwsza (domek), zaraz za nią dokumenty wyniesione z list rozwijanych, potem reszta
  w kolejności drzewa. Liczy też ``attr["dj_active"]`` – pozycja listy rozwijanej jest aktywna,
  gdy czytelnik stoi na jednej z jej podstron (ten sam warunek co ``active`` w backendzie).

Wyniesiona pozycja stoi **zaraz po stronie głównej**, a nie w miejscu rodzica: w Wagtailu to jest
„Komitety”, pierwsza pozycja po domku (``MENU_ORDER``), a strona-dziecko nie ma własnego miejsca
w kolejności korzenia drzewa, więc „przed/po rodzicu” nie dałoby tego samego menu.

Modyfikator nie usuwa dzieci ze stron bez „listy rozwijanej” – o tym, czy dzieci są rysowane,
decyduje szablon ``dj/menu/nav.html`` (``dj_expand``). Inne użycia ``show_menu`` (np. spis
podstron we wtyczce) dostają więc drzewo nietknięte, poza wyniesionymi pozycjami.

To jest nawigacja, nie autoryzacja: widoczność stron rozstrzyga django CMS (opublikowane,
„w nawigacji”), a ten moduł zmienia wyłącznie układ.
"""

from __future__ import annotations

from menus.base import Modifier
from menus.menu_pool import menu_pool

#: Atrybut żądania z flagami ``MenuExtension`` pobranymi w tej odsłonie (id strony → flagi).
REQUEST_MEMO_ATTR = "_dj_menu_extensions"

FLAGS = ("primary", "expand", "promote")


def _extension_flags(request, page_ids: set[int]) -> dict[int, dict[str, bool]]:
    from .models import MenuExtension

    memo = getattr(request, REQUEST_MEMO_ATTR, None) if request is not None else None
    if memo is not None and page_ids <= memo["ids"]:
        return memo["flags"]
    flags = {
        row["extended_object_id"]: {flag: row[flag] for flag in FLAGS}
        for row in MenuExtension.objects.filter(extended_object_id__in=page_ids).values(
            "extended_object_id", *FLAGS
        )
    }
    if request is not None:
        setattr(request, REQUEST_MEMO_ATTR, {"ids": set(page_ids), "flags": flags})
    return flags


def _annotate(request, nodes) -> None:
    page_ids = {node.id for node in nodes if node.attr.get("is_page")}
    flags = _extension_flags(request, page_ids) if page_ids else {}
    for node in nodes:
        node_flags = flags.get(node.id, {}) if node.attr.get("is_page") else {}
        for flag in FLAGS:
            node.attr[f"dj_{flag}"] = bool(node_flags.get(flag, False))


def _arrange(roots: list) -> list:
    home, promoted, rest = [], [], []
    for node in roots:
        if node.attr.get("is_home"):
            home.append(node)
            continue
        if node.attr.get("dj_expand"):
            kept = []
            for child in node.children:
                if child.attr.get("dj_promote"):
                    child.parent = None
                    promoted.append(child)
                else:
                    kept.append(child)
            node.children = kept
        rest.append(node)
    arranged = home + promoted + rest
    for node in arranged:
        node.attr["dj_active"] = bool(
            node.selected or (node.attr.get("dj_expand") and any(child.selected for child in node.children))
        )
    return arranged


class DjMenuModifier(Modifier):
    def modify(self, request, nodes, namespace, root_id, post_cut, breadcrumb):
        if breadcrumb:
            return nodes
        if not post_cut:
            _annotate(request, nodes)
            return nodes
        return _arrange(nodes)


menu_pool.register_modifier(DjMenuModifier)
