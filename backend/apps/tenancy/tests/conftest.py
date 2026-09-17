"""Minimalne fikstury wielodostępności na czas zadania T1.

Fikstura dwóch konkursów dla **całej** suity (``backend/conftest.py``, ``competition``,
``other_competition``, autouse ``_bind_competition``) powstaje w zadaniu T7 – razem z fabrykami,
które przyjmą argument ``competition``. Do tego czasu testy tej aplikacji budują sobie konkursy
same: trzy linijki tutaj są tańsze niż fikstura projektowa wprowadzona bez fabryk, którą T7 i tak
musiałby przepisać.

Witryny tworzymy z ``root_page`` wziętym z drzewa, a nie zakładamy, że drzewo stron postawione
migracjami jeszcze w bazie jest: testy transakcyjne (te przewijające migracje) czyszczą bazę po
sobie, więc w chwili startu tego pakietu ``wagtailcore.Site`` może nie mieć ani jednego wiersza.
"""

from __future__ import annotations

import pytest
from django.conf import settings as django_settings
from wagtail.models import Locale, Page, Site

from apps.tenancy.models import Competition, RoutingMode

#: Hosty konkursów w testach. Domena ``.invalid`` jest zarezerwowana normą (RFC 2606) i nigdy nie
#: zostanie kupiona, więc test, który przypadkiem wyjdzie do sieci, nie trafi pod cudzy adres.
HOST_A = "kwantowa.invalid"
HOST_B = "fizyczna.invalid"


def root_page() -> Page:
    """Korzeń drzewa stron; zakłada go, gdy w bazie go nie ma.

    Nie zakładamy, że korzeń postawiony migracją ``cms.0002`` w bazie jest: test transakcyjny
    (przewijający migracje) czyści bazę po sobie, a ``flush`` odtwarza wyłącznie typy treści
    i uprawnienia – nie wiersze wpisane przez ``RunPython``.
    """
    root = Page.objects.filter(depth=1).order_by("path").first()
    if root is not None:
        return root
    locale = Locale.objects.order_by("pk").first() or Locale.objects.create(
        language_code=django_settings.LANGUAGE_CODE
    )
    return Page.add_root(title="Root", slug="root", locale=locale)


def make_site(hostname: str, *, default: bool = False) -> Site:
    """Witryna Wagtaila wskazująca korzeń drzewa – tyle, ile potrzeba do rozstrzygania hosta."""
    root = root_page()
    return Site.objects.create(
        hostname=hostname, port=80, site_name=hostname, root_page=root, is_default_site=default
    )


def make_competition(hostname: str, slug: str, **kwargs) -> Competition:
    """Konkurs z własną witryną. Domyślnie tak, jak Konkurs #1: własna domena, bez prefiksu."""
    site = make_site(hostname, default=kwargs.pop("default_site", False))
    values = {
        "name": f"Olimpiada {slug}",
        "organizer_name": "Organizator testowy",
        "primary_domain": hostname,
        "routing_mode": RoutingMode.DOMAIN,
    }
    values.update(kwargs)
    return Competition.objects.create(site=site, slug=slug, **values)


@pytest.fixture(autouse=True)
def _allow_test_hosts(settings):
    """Hosty konkursów testowych na liście dozwolonych – inaczej ``get_host()`` podnosi 400.

    Fikstura jest ``autouse``, bo dotyczy **każdego** testu tego pakietu: rozstrzyganie konkursu
    zaczyna się od nagłówka ``Host``, a Django sprawdza go, zanim dojdzie do naszej warstwy.
    Wpis z kropką wiodącą dopuszcza całą domenę ``.invalid`` – inaczej test hosta nieznanego
    przechodziłby z niewłaściwego powodu (400 zamiast rozstrzygnięcia).
    """
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, ".invalid"]


@pytest.fixture
def competition(db) -> Competition:  # noqa: ARG001 - fikstura bazy, używana przez efekt uboczny
    """Konkurs #1 – ten, który w bazie testowej założyła migracja ``tenancy.0002``.

    Nie zakładamy drugiego: unikalny identyfikator ``kwantowa`` jest już zajęty, a przede
    wszystkim testy mają pracować na **tym** konkursie, który dostanie produkcja. Zmieniamy mu
    wyłącznie domenę – żeby dało się odróżnić host konkursu od hosta konkursu drugiego.
    """
    existing = Competition.objects.select_related("site").order_by("pk").first()
    if existing is None:
        # Baza po teście transakcyjnym: wiersze wpisane przez ``RunPython`` zniknęły.
        Site.objects.filter(is_default_site=True).update(is_default_site=False)
        return make_competition(HOST_A, "kwantowa", default_site=True)
    Site.objects.exclude(pk=existing.site_id).update(is_default_site=False)
    Site.objects.filter(pk=existing.site_id).update(hostname=HOST_A, is_default_site=True)
    # ``update`` nie wywołuje sygnału synchronizującego domenę, więc wpisujemy ją wprost –
    # w teście ma być widać, co jest ustawiane, a nie co się dzieje przy okazji.
    Competition.objects.filter(pk=existing.pk).update(primary_domain=HOST_A)
    existing.refresh_from_db()
    return existing


@pytest.fixture
def other_competition(db) -> Competition:  # noqa: ARG001 - jw.
    """Konkurs #2 – istnieje wyłącznie po to, żeby dało się sprawdzić, że go nie widać."""
    return make_competition(HOST_B, "fizyczna")
