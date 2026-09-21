"""Pasek rotujących logotypów w menu: co pokazać, w jakiej kolejności i z jaką pamięcią podręczną.

Uwaga organizatora z 21.09.2026 („jak na Olimpiadzie Biologicznej”, ``olimpbiol.pl``): pasek stoi
w nagłówku, po prawej stronie pozycji „FAQ”, pokazuje po kilka logotypów naraz i przesuwa się
o jeden co ``sponsor_slider_seconds`` sekund – patrz ``static/js/sponsor-slider.js`` za samo
przewijanie i ``templates/cms/_sponsor_slider.html`` za znacznik.

Ten moduł odpowiada wyłącznie za **co** trafia do paska:

- **pierwsza plansza to zawsze organizator** (``SiteSettings.organizer_logo``/``organizer_name``,
  ten sam znak co w stopce – patrz ``templates/base.html``), zanim jeszcze zapyta się o partnerów.
  Adresem jest ``contact_url`` – ten sam, co pod logotypem w stopce,
- **partnerzy idą w kolejności strony** ``/partnerzy/`` i tylko ci z logotypem; poziom współpracy
  filtruje ``SiteSettings.sponsor_slider_levels`` (puste = każdy poziom),
- **bez duplikatu organizatora.** Jeśli ten sam partner stoi też na liście „Partnerzy” pod tym
  samym adresem (Fundacja bywa też swoim własnym wpisem), jego znak nie pokazuje się drugi raz –
  adresy porównujemy znormalizowane (bez ``http(s)://``, ``www.`` i końcowego ``/``), bo redakcja
  wpisuje je swobodnie.

**Pamięć podręczna jest per witryna** (jak w ``apps.cms.announcements``) – pasek renderuje się na
każdej stronie serwisu, więc bez niej każda odsłona kosztowałaby zapytanie o ustawienia, o stronę
partnerów i o rendition każdego logotypu. TTL jest dłuższy niż przy komunikatach (5 minut, nie
minuta): zmiana loga albo partnera nie jest zdarzeniem awaryjnym, a i tak czyścimy pamięć od razu
przy zapisie (sygnały niżej) – TTL jest wyłącznie zabezpieczeniem dla procesów, które akurat nie
dostały sygnału.

Ładunek trzyma **gotowe napisy** (adres rendition, szerokość, wysokość), a nie obiekty obrazów:
odczyt z pamięci podręcznej ma kosztować zero zapytań, więc wołanie ``{% image %}`` przy każdym
żądaniu jest dokładnie tym, czego ta pamięć ma unikać.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django.core.cache import cache
from django.db import DatabaseError
from django.db.models.signals import post_save
from django.dispatch import receiver
from wagtail.signals import page_published, page_unpublished

from .blocks import PARTNER_LEVELS
from .models import PartnersPage, SiteSettings
from .tenancy import competition_for_request, resolve_competition

#: Przedrostek i czas życia pamięci podręcznej ładunku slidera. Pasek stoi na **każdej** stronie
#: serwisu – patrz uzasadnienie w docstringu modułu.
CACHE_PREFIX = "cms:sponsor_slider:payload"
CACHE_TTL_SECONDS = 300

#: Rendition logotypów w pasku: kadr 160×48 (patrz ``static/css/app.css``, ``.sponsor-slider``).
RENDITION_SPEC = "max-160x48"

#: Znane klucze poziomów współpracy – do przecięcia z zapisanym filtrem (patrz
#: ``validate_sponsor_slider_levels`` w ``apps.cms.models``: broni zapisu, to tu broni odczytu,
#: bo poziom mógł zniknąć z kodu **po** zapisaniu filtra).
LEVEL_KEYS = frozenset(key for key, _ in PARTNER_LEVELS)


def cache_key(competition) -> str:
    """Klucz wpisu dla konkursu. ``None`` ma własny klucz – patrz ``apps.cms.announcements``."""
    return f"{CACHE_PREFIX}:{getattr(competition, 'pk', None) or 'none'}"


def normalize_url(url: str | None) -> str:
    """Adres bez schematu, ``www.`` i końcowego ukośnika – do porównań „to ta sama instytucja”.

    Używane w dwóch miejscach: tutaj (pomijanie partnera zdublowanego z organizatorem) i na
    ekranie koordynatora (``apps.web.views.coordinator_sponsor_slider``, ten sam powód w podglądzie).
    """
    url = (url or "").strip()
    if not url:
        return ""
    parsed = urlsplit(url)
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return f"{netloc}{parsed.path.rstrip('/')}"


def _empty_payload() -> dict:
    return {"seconds": 0, "entries": []}


def _rendition_entry(image, *, name: str, url: str) -> dict:
    rendition = image.get_rendition(RENDITION_SPEC)
    return {
        "name": name,
        "url": url,
        "src": rendition.url,
        "width": rendition.width,
        "height": rendition.height,
    }


def organizer_entry(settings_row) -> dict | None:
    """Plansza organizatora – zawsze pierwsza, jeśli logotyp jest ustawiony w /cms/."""
    logo = settings_row.organizer_logo
    if logo is None:
        return None
    return _rendition_entry(
        logo,
        name=settings_row.organizer_name,
        url=(settings_row.contact_url or "").strip(),
    )


def partner_entries(site, levels: frozenset[str], *, skip_url: str = "") -> list[dict]:
    """Partnerzy strony ``/partnerzy/`` **tej** witryny, z logotypem, w kolejności strony.

    ``skip_url`` jest znormalizowanym adresem organizatora – partner pod tym samym adresem nie
    dubluje pierwszej planszy.
    """
    page = PartnersPage.objects.live().child_of(site.root_page).first()
    if page is None:
        return []
    entries = []
    for block in page.partners:
        value = block.value
        logo = value.get("logo")
        if logo is None:
            continue
        if levels and value.get("level") not in levels:
            continue
        url = (value.get("url") or "").strip()
        if url and skip_url and normalize_url(url) == skip_url:
            continue
        entries.append(_rendition_entry(logo, name=value.get("name", ""), url=url))
    return entries


def build_payload(competition=None) -> dict:
    """Ładunek slidera **bez** pamięci podręcznej – liczy się od zera przy każdym wołaniu."""
    competition = resolve_competition(competition)
    site = getattr(competition, "site", None)
    if site is None:
        return _empty_payload()
    try:
        settings_row = SiteSettings.for_site(site)
    except DatabaseError:  # pragma: no cover - baza bez migracji ustawień
        return _empty_payload()
    if not settings_row.sponsor_slider_enabled:
        return _empty_payload()
    organizer = organizer_entry(settings_row)
    skip_url = normalize_url(organizer["url"]) if organizer and organizer["url"] else ""
    levels = frozenset(settings_row.sponsor_slider_levels or []) & LEVEL_KEYS
    try:
        partners = partner_entries(site, levels, skip_url=skip_url)
    except DatabaseError:  # pragma: no cover - baza bez migracji partnerów
        return _empty_payload()
    entries = ([organizer] if organizer else []) + partners
    if not entries:
        return _empty_payload()
    return {"seconds": settings_row.sponsor_slider_seconds, "entries": entries}


def cached_payload(competition=None) -> dict:
    """Ładunek slidera **tego** konkursu z pamięcią podręczną (5 minut), unieważnianą przy zapisie."""
    competition = resolve_competition(competition)
    key = cache_key(competition)
    cached = cache.get(key)
    if cached is not None:
        return cached
    data = build_payload(competition)
    cache.set(key, data, CACHE_TTL_SECONDS)
    return data


def reset_cache(**_kwargs) -> None:
    """Zapomina zapamiętane ładunki **wszystkich** konkursów – patrz ``apps.cms.announcements``."""
    cache.delete_many(_all_cache_keys())


def _all_cache_keys() -> list[str]:
    from apps.tenancy.models import Competition

    keys = [cache_key(None)]
    try:
        keys += [f"{CACHE_PREFIX}:{pk}" for pk in Competition.objects.values_list("pk", flat=True)]
    except DatabaseError:  # pragma: no cover - baza bez migracji tabeli konkursów
        pass
    return keys


#: Trzy zdarzenia unieważniają ładunek: publikacja/wycofanie strony „Partnerzy” (nowy albo zdjęty
#: logotyp, zmiana poziomu) i zapis ``SiteSettings`` (włącznik, sekundy, filtr poziomów, **oraz**
#: logotyp i adres organizatora – ten sam wiersz, edytowany w /cms/ → Ustawienia → Dane serwisu,
#: a nie na ekranie koordynatora). Bez tego ostatniego zmiana znaku organizatora czekałaby na
#: wygaśnięcie TTL, mimo że stopka pokazuje ją od razu.
@receiver(page_published, sender=PartnersPage, dispatch_uid="cms.sponsor_slider.reset_on_publish")
@receiver(page_unpublished, sender=PartnersPage, dispatch_uid="cms.sponsor_slider.reset_on_unpublish")
@receiver(post_save, sender=SiteSettings, dispatch_uid="cms.sponsor_slider.reset_on_settings_save")
def _reset_on_change(sender, **kwargs) -> None:
    reset_cache()


def sponsor_slider(request) -> dict:
    """Procesor kontekstu: ładunek slidera **tego** konkursu dla ``templates/base.html``.

    Panel redakcyjny i panel administracyjny nie dostają paska – z tego samego powodu, co baner
    komunikatów (``apps.cms.announcements``): mają własną ramę i pasek wstrzyknięty w cudzy layout
    niczego by nie pokazał sensownie.
    """
    from apps.web.middleware import is_admin_path

    if is_admin_path(request.path):
        return {"sponsor_slider": _empty_payload()}
    return {"sponsor_slider": cached_payload(competition_for_request(request))}
