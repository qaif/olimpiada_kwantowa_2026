"""Kontekst szablonów **paczki** motywu: wyłącznie dane z listy dozwolonej, bez obiektów modeli.

Dlaczego (przegląd 4.10.2026, H1): szablon Django woła bezargumentowe metody obiektów w kontekście.
Pełny kontekst strony (``request``, ``user``, ``page``, ``competition``…) dawał szablonowi paczki
``{{ page.unpublish }}`` (wycofanie strony przy renderze), ``{{ user.set_unusable_password }}``
i ``{% for p in competition.participants.all %}{{ p.user.email }}`` (dane osobowe na stronie
publicznej, do tego w pełnostronicowym cache gościa). Lint nazw tego nie zamknie – metod modeli
z efektami ubocznymi jest za dużo i przybywa ich z każdą wersją Wagtaila.

Zasada: szablon paczki dostaje **kopię** danych – napisy, liczby, daty, listy i słowniki – oraz
trzy klasy pośrednie bez metod z efektem ubocznym:

- :class:`PageProxy` – tytuł, adres, pola SEO i kilka pól treści (``{% pageurl %}`` w silniku
  motywu przyjmuje go zamiast strony – ``apps.themes.safe_tags``),
- :class:`ImageProxy` – tylko ``get_rendition`` (dla ``{% image %}``) zwracające
  :class:`RenditionProxy` (adres, wymiary, ``alt``, ``img_tag``),
- nic więcej: obiekt spoza listy (model, queryset, funkcja) zamienia się w ``None``.

Fragmenty **aplikacji** dołączane przez slot (``{% include "web/_support_link.html" %}``,
``classic/<slot>.html``) renderują się z pełnym kontekstem strony – to kod aplikacji, nie paczki
(``apps.themes.rendering.AppTemplate``). Pełny kontekst jedzie w kluczu ``_full_context``, do
którego szablon nie ma dostępu (Django odrzuca zmienne zaczynające się od podkreślenia już przy
kompilacji).
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable
from decimal import Decimal
from itertools import islice

from django.utils.functional import LazyObject, Promise, empty
from django.utils.safestring import SafeString

#: Pola strony przepisywane do :class:`PageProxy` (o ile strona je ma).
PAGE_FIELDS = (
    "title",
    "seo_title",
    "search_description",
    "slug",
    "date",
    "lead",
    "intro",
    "hero_title",
    "hero_text",
    "hero_show_news",
)

#: Modele, z których szablon paczki dostaje wybrane pola (``app_label.model_name`` → pola).
MODEL_FIELDS = {
    "competitions.edition": ("year_label",),
    "competitions.stage": ("display_name", "opens_at", "deadline_at"),
}

#: Pola ``cms.SiteSettings`` dostępne jako ``settings.cms.SiteSettings`` (dane jawne, ze stopki).
SITE_FIELDS = (
    "site_name",
    "tagline",
    "registration_note",
    "ga_measurement_id",
    "organizer_name",
    "organizer_address",
    "organizer_registry",
    "contact_email",
    "contact_phone",
    "contact_url",
)

#: Pola konkursu dostępne jako ``competition`` (marka i kontakt publiczny).
COMPETITION_FIELDS = (
    "name",
    "short_name",
    "tagline",
    "slug",
    "accent_colour",
    "contact_email",
    "organizer_name",
)

#: Klucze kontekstu strony przekazywane slotom (po przepisaniu przez :func:`safe`).
PASSTHROUGH = (
    "site_root",
    "LANGUAGE_CODE",
    "LANGUAGE_BIDI",
    "interface_language",
    "interface_direction",
    "interface_contrast",
    "app_version",
    "site_edition_label",
    "cms_menu",
    "cms_menu_primary",
    # Taśma sponsorów (THEME-02): słownik ``{"seconds", "entries": [{name, url, src, width, height}]}``
    # z procesora ``apps.cms.sponsor_slider`` – same napisy i liczby, żeby motyw mógł postawić
    # ``{% include "cms/_sponsor_slider.html" %}`` we własnym miejscu tylko wtedy, gdy są wpisy.
    "sponsor_slider",
    # strona główna (``HomePage.get_context``) – slot ``home_hero``
    "page",
    "hero_slides_visible",
    "hero_show_intro",
    "latest_news",
    "news_index",
    "edition",
    "current_stage",
    "now",
    # karta aktualności – slot ``news_card``
    "item",
)
ROLE_FLAGS = (
    "is_participant",
    "is_reviewer",
    "is_appeals_committee",
    "is_supervisor",
    "is_team_leader",
    "is_coordinator",
)
MAX_ITEMS = 100
MAX_DEPTH = 6


class RenditionProxy:
    """Wynik ``{% image … as x %}`` w szablonie paczki: adres i wymiary, bez obiektu modelu."""

    __slots__ = ("url", "width", "height", "alt", "full_url", "_html")

    def __init__(self, rendition=None):
        self.url = getattr(rendition, "url", "") or ""
        self.full_url = getattr(rendition, "full_url", "") or self.url
        self.width = getattr(rendition, "width", 0) or 0
        self.height = getattr(rendition, "height", 0) or 0
        self.alt = getattr(rendition, "alt", "") or ""
        self._html = rendition

    def img_tag(self, extra_attributes=None):
        if self._html is None:
            return SafeString("")
        return self._html.img_tag(extra_attributes or {})

    def __str__(self):
        return self.img_tag()


class ImageProxy:
    """Obraz Wagtaila dla ``{% image %}`` – sam ``get_rendition``, bez dostępu do modelu."""

    __slots__ = ("_image", "title", "width", "height")

    def __init__(self, image):
        self._image = image
        self.title = getattr(image, "title", "") or ""
        self.width = getattr(image, "width", 0) or 0
        self.height = getattr(image, "height", 0) or 0

    def get_rendition(self, spec):
        from wagtail.images.shortcuts import get_rendition_or_not_found

        try:
            return RenditionProxy(get_rendition_or_not_found(self._image, spec))
        except Exception:  # noqa: BLE001 - brak pliku/zła specyfikacja: pusty obraz, nie 500
            return RenditionProxy(None)

    def get_renditions(self, *specs):
        from wagtail.images.shortcuts import get_renditions_or_not_found

        try:
            found = get_renditions_or_not_found(self._image, specs)
        except Exception:  # noqa: BLE001 - j.w.
            return {spec: RenditionProxy(None) for spec in specs}
        return {key: RenditionProxy(rendition) for key, rendition in found.items()}

    def is_svg(self):
        return bool(getattr(self._image, "is_svg", lambda: False)())

    def __bool__(self):
        return True


class PageProxy:
    """Strona CMS widziana przez szablon paczki: pola z ``PAGE_FIELDS`` i adres."""

    __slots__ = ("url", *PAGE_FIELDS)

    def __init__(self, page, request):
        for name in PAGE_FIELDS:
            value = getattr(page, name, None)
            setattr(self, name, None if callable(value) else safe(value, request, depth=MAX_DEPTH - 1))
        try:
            self.url = page.get_url(request=request) or ""
        except Exception:  # noqa: BLE001 - strona bez witryny: bez adresu
            self.url = ""

    def __str__(self):
        return str(self.title or "")


def _model_label(value) -> str | None:
    meta = getattr(value, "_meta", None)
    return f"{meta.app_label}.{meta.model_name}" if meta is not None else None


def safe(value, request=None, *, depth: int = 0):
    """Kopia ``value`` zrozumiała dla szablonu paczki – albo ``None``, gdy typu nie ma na liście."""
    from wagtail.images.models import AbstractImage
    from wagtail.models import Page
    from wagtail.rich_text import RichText

    if depth > MAX_DEPTH:
        return None
    if value is None or isinstance(value, (bool, int, float, Decimal, str, datetime.date, datetime.time)):
        return value
    if isinstance(value, Promise):
        return str(value)
    if isinstance(value, LazyObject):
        if value._wrapped is empty:
            value._setup()
        value = value._wrapped
    if isinstance(value, (PageProxy, ImageProxy, RenditionProxy)):
        return value
    if isinstance(value, Page):
        return PageProxy(value, request)
    if isinstance(value, AbstractImage):
        return ImageProxy(value)
    if isinstance(value, RichText):
        return value.source
    if hasattr(value, "block_type") and hasattr(value, "value"):
        # Element StreamField (plansza slidera): typ bloku i jego wartość.
        return {"block_type": str(value.block_type), "value": safe(value.value, request, depth=depth + 1)}
    if (
        isinstance(value, dict)
        or hasattr(value, "items")
        and hasattr(value, "keys")
        and not _model_label(value)
    ):
        return {
            str(key): safe(item, request, depth=depth + 1)
            for key, item in value.items()
            if isinstance(key, str) and not key.startswith("_")
        }
    label = _model_label(value)
    if label is not None:
        fields = MODEL_FIELDS.get(label)
        if fields is None:
            return None
        out = {}
        for name in fields:
            attribute = getattr(value, name, None)
            out[name] = None if callable(attribute) else safe(attribute, request, depth=depth + 1)
        return out
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        return [safe(item, request, depth=depth + 1) for item in islice(value, MAX_ITEMS)]
    return None


def _site_settings(full: dict) -> dict:
    settings_proxy = full.get("settings")
    site = None
    try:
        site = settings_proxy["cms"]["SiteSettings"] if settings_proxy is not None else None
    except Exception:  # noqa: BLE001 - brak ustawień witryny: puste dane
        site = None
    if site is None:
        return {}
    data = {name: safe(getattr(site, name, ""), None) for name in SITE_FIELDS}
    logo = getattr(site, "organizer_logo", None)
    data["organizer_logo"] = ImageProxy(logo) if logo is not None else None
    links = getattr(site, "social_links", None)
    data["social_links"] = safe(links, None) if isinstance(links, list) else []
    return data


def _competition(competition) -> dict | None:
    if competition is None:
        return None
    data = {name: safe(getattr(competition, name, ""), None) for name in COMPETITION_FIELDS}
    data["genitive"] = str(getattr(competition, "genitive", "") or "")
    for name in ("site_logo", "logo", "favicon", "social_image"):
        image = getattr(competition, name, None) if getattr(competition, f"{name}_id", None) else None
        data[name] = ImageProxy(image) if image is not None else None
    return data


def curated_context(full: dict, request) -> dict:
    """Kontekst szablonu paczki zbudowany z pełnego kontekstu strony (``Context.flatten()``)."""
    user = getattr(request, "user", None)
    authenticated = bool(user is not None and getattr(user, "is_authenticated", False))
    user_data = {
        "is_authenticated": authenticated,
        "email": getattr(user, "email", "") if authenticated else "",
    }
    values = {key: safe(full[key], request) for key in PASSTHROUGH if key in full}
    values.update({flag: bool(full.get(flag)) for flag in ROLE_FLAGS})
    registration = full.get("registration") or {}
    values["registration"] = {
        key: safe(registration.get(key), request)
        for key in ("is_open", "reason", "opens_at", "closes_at", "message")
    }
    values["promo_available"] = bool(full.get("promo_available"))
    values["user"] = user_data
    values["request"] = {"path": getattr(request, "path", ""), "user": user_data}
    values["settings"] = {"cms": {"SiteSettings": _site_settings(full)}}
    values["competition"] = _competition(getattr(request, "competition", None))
    # Token CSRF **tego** odwiedzającego jest i tak na każdej stronie (``hx-headers`` w ``<body>``);
    # formularz „Wyloguj” w nagłówku motywu potrzebuje go w ``{% csrf_token %}``.
    if "csrf_token" in full:
        values["csrf_token"] = full["csrf_token"]
    return values
