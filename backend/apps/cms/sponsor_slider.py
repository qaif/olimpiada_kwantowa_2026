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

import logging
from urllib.parse import urlsplit

from django.core.cache import cache
from django.db import DatabaseError
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from wagtail.images import get_image_model
from wagtail.signals import page_published, page_unpublished, post_page_move

from .blocks import PARTNER_LEVELS
from .models import PartnersPage, SiteSettings
from .tenancy import (
    competition_for_page,
    competition_for_request,
    competition_for_site,
    resolve_competition,
)

logger = logging.getLogger(__name__)

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

#: Jedyne schematy, pod którymi plansza dostaje odnośnik. Bez tego strażnika ``javascript:…``
#: wpisany wprost do adresu bloku (z pominięciem walidacji formularza – ``URLBlock``/``URLField``
#: łapią to w /cms/ i na ekranie koordynatora, ale nie każdy zapis idzie tą drogą, patrz migracje
#: danych i import) trafiłby do ``href`` na **każdej** stronie serwisu.
_ALLOWED_URL_SCHEMES = ("http://", "https://")


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


def _safe_url(url: str | None) -> str:
    """Adres, ale tylko pod ``http(s)://`` – inaczej pusty napis, czyli „bez odnośnika”.

    Broni przed ``javascript:…`` i podobnymi schematami wpisanymi wprost do danych bloku z
    pominięciem walidacji formularza (``URLBlock``/``URLField`` łapią to w /cms/ i na ekranie
    koordynatora, ale migracja danych albo import CSV nie przechodzą przez żaden formularz).
    """
    url = (url or "").strip()
    if url.lower().startswith(_ALLOWED_URL_SCHEMES):
        return url
    return ""


def _rendition_entry(image, *, name: str, url: str) -> dict | None:
    """Rendition logotypu jako gotowy wpis – albo ``None``, gdy pliku nie da się przygotować.

    Plik źródłowy bywa nieosiągalny: skasowany bezpośrednio w magazynie, awaria S3/MinIO,
    uszkodzony format, rekord bez pliku. To jest stan **danych**, a nie błąd programu – i nie może
    wywrócić strony, bo ten kod stoi w procesorze kontekstu wołanym na **każdej** stronie serwisu
    (``sponsor_slider`` niżej). Pomijamy wyłącznie tę jedną planszę; log niesie identyfikator
    obrazu, nigdy nazwę partnera ani adres (to nie są dane osobowe, ale i tak nie są tu potrzebne).
    """
    try:
        rendition = image.get_rendition(RENDITION_SPEC)
    except Exception:  # noqa: BLE001 - patrz docstring; jeden zepsuty plik nie może zdjąć strony
        logger.warning(
            "Nie udało się przygotować logotypu #%s do slidera sponsorów – pomijam wpis.",
            image.pk,
            exc_info=True,
        )
        return None
    return {
        "name": name,
        "url": _safe_url(url),
        "src": rendition.url,
        "width": rendition.width,
        "height": rendition.height,
    }


def organizer_entry(settings_row) -> dict | None:
    """Plansza organizatora – zawsze pierwsza, jeśli logotyp jest ustawiony w /cms/."""
    logo = settings_row.organizer_logo
    if logo is None:
        return None
    return _rendition_entry(logo, name=settings_row.organizer_name, url=settings_row.contact_url)


def partner_entries(site, levels: frozenset[str], *, skip_url: str = "") -> list[dict]:
    """Partnerzy strony ``/partnerzy/`` **tej** witryny, z logotypem, w kolejności strony.

    ``skip_url`` jest znormalizowanym adresem organizatora – partner pod tym samym adresem nie
    dubluje pierwszej planszy. Wpis, którego logotyp nie da się przygotować, jest pomijany
    (``_rendition_entry`` oddaje wtedy ``None``) – reszta partnerów zostaje na taśmie.
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
        url = _safe_url(value.get("url"))
        if url and skip_url and normalize_url(url) == skip_url:
            continue
        entry = _rendition_entry(logo, name=value.get("name", ""), url=url)
        if entry is not None:
            entries.append(entry)
    return entries


def _build_payload(competition) -> dict:
    """Ładunek slidera **bez** pamięci podręcznej – właściwa treść, patrz ``build_payload``."""
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


def build_payload(competition=None) -> dict:
    """Ładunek slidera **bez** pamięci podręcznej – liczy się od zera przy każdym wołaniu.

    Owinięty w łapacz wszystkiego: ``_rendition_entry`` łapie awarię pojedynczego logotypu, ale
    ten kod stoi w procesorze kontekstu **każdej** strony serwisu, więc jakikolwiek inny,
    nieprzewidziany wyjątek (błąd sterownika magazynu, awaria pamięci podręcznej renditionów…)
    ma dawać pusty pasek, a nie pięćsetkę na całym serwisie naraz.
    """
    try:
        return _build_payload(competition)
    except Exception:  # noqa: BLE001 - patrz docstring
        logger.warning("Nie udało się zbudować ładunku slidera sponsorów.", exc_info=True)
        return _empty_payload()


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


def _warm(competition) -> None:
    """Przelicza i zapisuje ładunek **od razu**, w żądaniu, które spowodowało zmianę.

    Rendition logotypu bywa kosztowna (dekodowanie i skalowanie pliku) – ten koszt ma zapłacić
    redaktor, który właśnie kliknął „Zapisz” (na ekranie koordynatora albo w /cms/), a nie
    przypadkowy gość odświeżający stronę główną milisekundę później na zimnej pamięci. Wołane
    tylko tam, gdzie **wiadomo**, którego konkursu dotyczyła zmiana; zdarzenia bez tej wiedzy
    (obraz w bibliotece, przeniesienie strony) zostawiają odbudowę leniwej ścieżce.
    """
    competition = resolve_competition(competition)
    if competition is None:
        return
    cache.set(cache_key(competition), build_payload(competition), CACHE_TTL_SECONDS)


#: Zdarzenia unieważniające ładunek — i to, dlaczego jest ich więcej niż trzy pierwotne:
#:
#: - **publikacja/wycofanie** strony „Partnerzy” (nowy albo zdjęty logotyp, zmiana poziomu) –
#:   od razu odbudowuje i zapisuje ładunek **tego** konkursu (``instance`` niesie stronę),
#: - **skasowanie i przeniesienie** strony „Partnerzy” – rzadsze i bez oczywistego zysku
#:   z natychmiastowej odbudowy, więc tylko czyszczenie; następne żądanie odbuduje leniwie,
#: - **zapis ``SiteSettings``** (włącznik, sekundy, filtr poziomów, **oraz** logotyp i adres
#:   organizatora – ten sam wiersz, edytowany w /cms/ → Ustawienia → Dane serwisu, a nie na
#:   ekranie koordynatora) – też z natychmiastową odbudową (``instance`` niesie witrynę),
#: - **zapis i skasowanie obrazu** w bibliotece – logotyp partnera i logotyp organizatora są
#:   opakowane w StreamField/FK, więc nie da się tanio ustalić, których witryn to dotyczy;
#:   czyszczymy pamięć **wszystkich** konkursów. To jest szerszy młot niż potrzeba (dowolny obraz
#:   w bibliotece, nawet zdjęcie do aktualności, czyści też ten pasek), ale zdarzenie jest rzadkie
#:   (redaktor edytujący bibliotekę obrazów), a koszt pomyłki – nieaktualny znak w menu do pięciu
#:   minut TTL – jest tym, czego dokładnie ta pamięć ma unikać.
@receiver(page_published, sender=PartnersPage, dispatch_uid="cms.sponsor_slider.reset_on_publish")
@receiver(page_unpublished, sender=PartnersPage, dispatch_uid="cms.sponsor_slider.reset_on_unpublish")
def _reset_on_partners_change(sender, instance=None, **kwargs) -> None:
    reset_cache()
    if instance is not None:
        _warm(competition_for_page(instance))


@receiver(post_delete, sender=PartnersPage, dispatch_uid="cms.sponsor_slider.reset_on_delete")
@receiver(post_page_move, sender=PartnersPage, dispatch_uid="cms.sponsor_slider.reset_on_move")
def _reset_on_partners_structural_change(sender, **kwargs) -> None:
    reset_cache()


@receiver(post_save, sender=SiteSettings, dispatch_uid="cms.sponsor_slider.reset_on_settings_save")
def _reset_on_settings_save(sender, instance=None, **kwargs) -> None:
    reset_cache()
    if instance is not None:
        _warm(competition_for_site(instance.site))


@receiver(post_save, sender=get_image_model(), dispatch_uid="cms.sponsor_slider.reset_on_image_save")
@receiver(post_delete, sender=get_image_model(), dispatch_uid="cms.sponsor_slider.reset_on_image_delete")
def _reset_on_image_change(sender, **kwargs) -> None:
    reset_cache()


def sponsor_slider(request) -> dict:
    """Procesor kontekstu: ładunek slidera **tego** konkursu dla ``templates/base.html``.

    Panel redakcyjny i panel administracyjny nie dostają paska – z tego samego powodu, co baner
    komunikatów (``apps.cms.announcements``): mają własną ramę i pasek wstrzyknięty w cudzy layout
    niczego by nie pokazał sensownie.

    Owinięte w łapacz wszystkiego, tak samo jak ``apps.cms.analytics.analytics_enabled_for_request``:
    to jest warstwa doklejana do **każdej** odpowiedzi, więc żaden wyjątek (nawet w samej pamięci
    podręcznej albo w rozstrzyganiu konkursu) nie może być nowym miejscem, w którym strona pada.
    """
    from apps.web.middleware import is_admin_path

    try:
        if is_admin_path(request.path):
            return {"sponsor_slider": _empty_payload()}
        return {"sponsor_slider": cached_payload(competition_for_request(request))}
    except Exception:  # noqa: BLE001 - patrz docstring
        logger.warning(
            "Procesor kontekstu slidera sponsorów zawiódł – pasek znika z tej odpowiedzi.",
            exc_info=True,
        )
        return {"sponsor_slider": _empty_payload()}
