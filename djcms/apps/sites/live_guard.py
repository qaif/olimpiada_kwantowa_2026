"""Bez prawa publikacji nie zmienia się tego, co widzi czytelnik: usuwanie, przenoszenie, strona główna (D5).

Grupa ``redakcja:<slug>:bez-publikacji`` ma w ``GlobalPagePermission`` usuwanie i przenoszenie
(inaczej nie posprzątałaby własnych szkiców), ale django CMS 5.1.3 nie wiąże tych akcji z publikacją:
``user_can_delete_page``/``user_can_move_page`` sprawdzają wyłącznie flagi ``can_delete``
i ``can_move_page``. Redaktor bez publikacji mógłby więc zdjąć albo przenieść **opublikowaną**
stronę – adres znika albo zmienia się od razu, bez nowej wersji do zatwierdzenia.

Reguła jak w Wagtailu (``PagePermissionTester.can_delete``/``can_move`` aplikacji głównej): bez
prawa publikacji strony wolno ją usunąć, przenieść albo usunąć jej tłumaczenie **tylko wtedy**, gdy
ani ona, ani żadna jej podstrona nie ma opublikowanej wersji. Prawo publikacji – bez zmian.

Zmiana adresu przez formularz ustawień strony (slug, „nadpisz adres”) reguły nie potrzebuje:
djangocms-versioning 2.7.1 zmienia wyłącznie wersję roboczą (``check_modify``; opublikowana treść
jest tylko do odczytu – także dla publikującego), a adres strony idzie za treścią dopiero po
publikacji (``ChangePageForm.save`` → ``update_urls_from_content`` tylko dla ``is_public()``).
Ustawienie strony głównej (``PageAdmin.set_home``) wymaga w django CMS tylko prawa zmiany obu stron,
a przepisuje adresy obu drzew od razu (``Page.set_as_homepage``) – tu: prawo publikacji nowej
i obecnej strony głównej (:func:`user_can_change_home`).

**Łatka na funkcje** ``cms.utils.page_permissions`` (a nie na widoki): te same funkcje liczą przyciski
drzewa stron, paska narzędzi i widoki panelu, więc przycisk nie obiecuje akcji, której widok
odmówi. ``cms.cms_toolbars`` importuje ``user_can_delete_page`` po nazwie – podmieniamy i tam.
Strona główna nie ma własnej funkcji uprawnień – łatka na widok ``PageAdmin.set_home`` (przed
zbudowaniem adresów panelu, czyli w ``AppConfig.ready``). Sygnatury oryginałów są sprawdzane:
zmiana API django CMS ma wywrócić start, a nie cicho wyłączyć regułę.
"""

from __future__ import annotations

import inspect
from functools import wraps

from django.core.exceptions import ImproperlyConfigured, PermissionDenied

PATCH_MARKER = "_dj_live_guard"

#: Funkcje ``cms.utils.page_permissions`` objęte regułą.
GUARDED = ("user_can_delete_page", "user_can_move_page", "user_can_delete_page_translation")
#: Moduły, które importują którąś z nich po nazwie (``from … import user_can_delete_page``).
IMPORTED_BY_NAME = ("cms.cms_toolbars",)


def has_live_content(page) -> bool:
    """Strona albo któraś z jej podstron ma opublikowaną wersję (jakiegokolwiek języka).

    ``PageContent.objects`` z djangocms-versioning zwraca wyłącznie treści opublikowane
    (``admin_manager`` – wszystkie). Poddrzewo z treebearda: strona i jej potomkowie.
    """
    from cms.models import Page, PageContent

    return PageContent.objects.filter(page__in=Page.get_tree(page)).exists()


def user_can_touch_live_tree(user, page) -> bool:
    """Czy konto może zmienić opublikowaną część drzewa pod ``page`` (usunąć, przenieść)."""
    from cms.utils import page_permissions

    if user.is_superuser or page_permissions.user_can_publish_page(user, page):
        return True
    return not has_live_content(page)


def user_can_change_home(user, page) -> bool:
    """Czy konto może uczynić ``page`` stroną główną jej witryny (prawo publikacji obu stron)."""
    from cms.models import Page
    from cms.utils import page_permissions

    if user.is_superuser:
        return True
    home = Page.objects.filter(is_home=True, site_id=page.site_id).exclude(pk=page.pk).first()
    return all(page_permissions.user_can_publish_page(user, target) for target in (page, home) if target)


def _guard(original):
    @wraps(original)
    def guarded(user, page, *args, **kwargs):
        return bool(original(user, page, *args, **kwargs)) and user_can_touch_live_tree(user, page)

    setattr(guarded, PATCH_MARKER, True)
    guarded.original = original  # type: ignore[attr-defined]
    return guarded


def _guard_set_home(original):
    @wraps(original)
    def set_home(self, request, object_id):
        page = self.get_object(request, object_id=object_id)
        if page is not None and not user_can_change_home(request.user, page):
            raise PermissionDenied("Stronę główną zmienia wyłącznie redaktor z prawem publikacji.")
        return original(self, request, object_id)

    setattr(set_home, PATCH_MARKER, True)
    return set_home


def _check_signature(func, expected: tuple[str, ...]) -> None:
    params = tuple(inspect.signature(func).parameters)
    if params[: len(expected)] != expected:
        raise ImproperlyConfigured(
            f"{func.__module__}.{func.__qualname__} ma inną sygnaturę niż {expected} "
            f"(jest {params}) – łatka apps.sites.live_guard wymaga przeglądu."
        )


def is_installed() -> bool:
    from cms.admin.pageadmin import PageAdmin
    from cms.utils import page_permissions

    functions = [getattr(page_permissions, name) for name in GUARDED] + [PageAdmin.set_home]
    return all(getattr(func, PATCH_MARKER, False) for func in functions)


def install() -> None:
    """Idempotentnie podmienia funkcje z ``GUARDED`` i ``PageAdmin.set_home`` (``AppConfig.ready``)."""
    import importlib

    from cms.admin.pageadmin import PageAdmin
    from cms.utils import page_permissions

    for name in GUARDED:
        current = getattr(page_permissions, name)
        if getattr(current, PATCH_MARKER, False):
            continue
        _check_signature(current, ("user", "page"))
        setattr(page_permissions, name, _guard(current))
    if not getattr(PageAdmin.set_home, PATCH_MARKER, False):
        _check_signature(PageAdmin.set_home, ("self", "request", "object_id"))
        PageAdmin.set_home = _guard_set_home(PageAdmin.set_home)  # type: ignore[method-assign]
    for module_name in IMPORTED_BY_NAME:
        module = importlib.import_module(module_name)
        for name in GUARDED:
            if hasattr(module, name):
                setattr(module, name, getattr(page_permissions, name))
