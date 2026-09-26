"""Automatyczne przekierowania przy zmianie adresu opublikowanej strony (DJ-02 § 8, ``source=auto``).

Odpowiednik ``WAGTAILREDIRECTS_AUTO_CREATE``: zmiana sluga, nadpisanego adresu albo przeniesienie
opublikowanej strony zostawia pod starym adresem przekierowanie 301 na nowy – dla niej **i dla
wszystkich jej potomków** (ich adresy zmieniają się razem z nią).

Skąd wiemy o zmianie: ``cms.PageUrl`` jest w django CMS 5.1.3 tablicą tras opublikowanej treści
(``path`` = adres, ``None`` = strona nieosiągalna), ale wszystkie jego zmiany idą przez
``QuerySet.update``/``bulk_update`` – sygnały ``pre_save``/``post_save`` nie przychodzą. Dwie
metody zmieniają ścieżki: ``Page.update_urls_from_content`` (publikacja/wycofanie wersji –
djangocms-versioning, zapis formularza strony) i ``Page.move_page`` (przeniesienie w drzewie).
Owijamy obie: zdjęcie ścieżek poddrzewa przed, porównanie po. Owinięcie jest idempotentne
(znacznik) i niczego nie zmienia w wyniku oryginału.

Reguły zapisu:

- stary adres miał stronę (``path`` nie ``None``), nowy też – przekierowanie ``stary → nowy``;
  wycofanie publikacji (nowy ``None``) nie tworzy niczego (brak celu),
- strona główna (``path == ""``) – pomijana (``/`` nie jest „starym adresem”),
- wpis redakcji (``manual``) i importu pod tym samym starym adresem wygrywa – automat go nie rusza,
- wpis ``auto`` spod **nowego** adresu jest kasowany (strona wróciła pod dawny adres – przekierowanie
  byłoby martwe, a przy kolejnej zmianie tworzyłoby pętlę).
"""

from __future__ import annotations

import functools
import logging

from django.db import transaction

from .models import Redirect, RedirectSource, normalise_path

logger = logging.getLogger(__name__)

PATCH_MARKER = "_dj_seo_auto_redirects"


def _page_url(path: str) -> str:
    path = path.strip("/")
    return f"/{path}/" if path else "/"


def snapshot(page) -> dict[tuple[int, str], str | None]:
    """``{(page_id, język): path}`` strony i jej potomków (``PageUrl``)."""
    from cms.models import Page, PageUrl

    tree = Page.get_tree(page).values("pk")
    return {
        (page_id, language): path
        for page_id, language, path in PageUrl.objects.filter(page__in=tree).values_list(
            "page_id", "language", "path"
        )
    }


def record_changes(site_id: int, before: dict, after: dict) -> list[Redirect]:
    """Przekierowania ``auto`` dla ścieżek, które się zmieniły (reguły w docstringu modułu)."""
    created = []
    for key, old in before.items():
        new = after.get(key)
        if old is None or new is None or old == new or old.strip("/") == "":
            continue
        old_path = normalise_path(_page_url(old))
        new_url = _page_url(new)
        Redirect.objects.filter(
            site_id=site_id, old_path=normalise_path(new_url), source=RedirectSource.AUTO
        ).delete()
        existing = Redirect.objects.filter(site_id=site_id, old_path=old_path).first()
        if existing is not None and existing.source != RedirectSource.AUTO:
            continue
        if existing is not None:
            existing.new_path = new_url
            existing.is_permanent = True
            existing.save(update_fields=["new_path", "is_permanent"])
            created.append(existing)
        else:
            created.append(
                Redirect.objects.create(
                    site_id=site_id,
                    old_path=old_path,
                    new_path=new_url,
                    is_permanent=True,
                    source=RedirectSource.AUTO,
                )
            )
    # Przekierowanie wskazujące stary adres (łańcuch A → B, teraz B → C) – przepinamy na nowy.
    for key, old in before.items():
        new = after.get(key)
        if old is None or new is None or old == new or old.strip("/") == "":
            continue
        Redirect.objects.filter(site_id=site_id, new_path=_page_url(old), source=RedirectSource.AUTO).update(
            new_path=_page_url(new)
        )
    return created


def _wrap(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            with transaction.atomic():
                before = snapshot(self)
        except Exception:  # noqa: BLE001 - automat nie może zablokować publikacji ani przeniesienia
            logger.exception(
                "Przekierowania automatyczne: zdjęcie adresów strony #%s nie powiodło się", self.pk
            )
            return method(self, *args, **kwargs)
        result = method(self, *args, **kwargs)
        try:
            # Punkt zapisu: błąd automatu nie może zatruć transakcji publikacji (Postgres).
            with transaction.atomic():
                record_changes(self.site_id, before, snapshot(self))
        except Exception:  # noqa: BLE001 - jw.; brak przekierowania to mniejsze zło niż cofnięta publikacja
            logger.exception("Przekierowania automatyczne strony #%s nie zapisały się", self.pk)
        return result

    setattr(wrapper, PATCH_MARKER, True)
    return wrapper


def install() -> None:
    """Owija ``Page.update_urls_from_content`` i ``Page.move_page`` (``AppConfig.ready``, idempotentnie)."""
    from cms.models import Page

    for name in ("update_urls_from_content", "move_page"):
        current = getattr(Page, name)
        if getattr(current, PATCH_MARKER, False):
            continue
        setattr(Page, name, _wrap(current))
