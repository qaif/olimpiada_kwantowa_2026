"""Pozycja menu „Dla nauczycieli” – prośba organizatora z 22.09.2026.

Do wydania z 22.09.2026 rejestracja opiekuna szkolnego (``/register/supervisor/``) miała odnośnik
wyłącznie na ``/register/`` i na ``/login/``. Ten moduł pilnuje nowej, osobnej pozycji w głównym
menu: pokazuje się wyłącznie niezalogowanemu czytelnikowi na witrynie, która ma dziś włączony
przełącznik ``cms.SiteSettings.supervisor_registration_enabled``, i stoi zawsze na końcu menu –
za wszystkimi pozycjami z drzewa CMS (patrz ``apps.cms.context_processors._supervisor_menu_item``).

Przełącznik jest domyślnie **wyłączony** (patrz ``apps.accounts.supervisors.registration_enabled``),
więc stan domyślny tych testów to stan bez pozycji – włączenie jest tym, co trzeba zrobić jawnie,
tak samo jak w ``apps.web.tests.test_supervisor_registration_flag``.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.accounts.supervisors import reset_registration_cache
from apps.accounts.tests.factories import UserFactory
from apps.cms.models import SiteSettings

pytestmark = pytest.mark.django_db

REGISTER_SUPERVISOR_URL = "/register/supervisor/"
ITEM_TITLE = "Dla szkół/nauczycieli"


@pytest.fixture(autouse=True)
def _reset_supervisor_registration_cache():
    """Pamięć podręczna przełącznika jest stanem procesu – wycofanie transakcji jej nie czyści.

    Ten sam powód i ten sam wzorzec, co ``apps.web.tests.conftest._reset_supervisor_registration_cache``:
    bez sprzątania test, który włączył przełącznik, zostawiałby „włączone” kolejnym testom (nawet
    po wycofaniu transakcji), aż do upływu trzydziestosekundowego TTL.
    """
    reset_registration_cache()
    yield
    reset_registration_cache()


@pytest.fixture
def supervisor_registration_on(competition) -> SiteSettings:
    """Włącza przełącznik na witrynie konkursu z fixture ``competition``."""
    row = SiteSettings.for_site(competition.site)
    row.supervisor_registration_enabled = True
    row.save()
    return row


def _titles(menu) -> list[str]:
    return [item["title"] for item in menu]


def test_item_present_for_anonymous_when_switch_on(client_for, competition, supervisor_registration_on):
    menu = client_for(competition).get("/").context["cms_menu"]

    assert ITEM_TITLE in _titles(menu)


def test_item_absent_when_switch_off(client_for, competition):
    """Stan domyślny: przełącznik wyłączony, więc odnośnika nie ma nigdzie w menu."""
    menu = client_for(competition).get("/").context["cms_menu"]

    assert ITEM_TITLE not in _titles(menu)


def test_item_absent_for_authenticated_user_even_when_switch_on(
    client_for, competition, supervisor_registration_on
):
    """Zalogowane konto nie ma z odnośnika żadnego pożytku – patrz docstring ``_supervisor_menu_item``."""
    client = client_for(competition)
    client.force_login(UserFactory())

    menu = client.get("/").context["cms_menu"]

    assert ITEM_TITLE not in _titles(menu)


def test_item_is_the_last_one_in_the_menu(client_for, competition, supervisor_registration_on):
    menu = client_for(competition).get("/").context["cms_menu"]

    assert menu, "menu nie może być puste – inaczej test niczego nie sprawdza"
    assert menu[-1]["title"] == ITEM_TITLE
    assert menu[-1]["slug"] == "nauczyciele"
    assert menu[-1]["url"] == reverse("web:register-supervisor")
    assert menu[-1]["url"] == REGISTER_SUPERVISOR_URL


def test_item_is_active_on_the_supervisor_registration_page(
    client_for, competition, supervisor_registration_on
):
    menu = client_for(competition).get(REGISTER_SUPERVISOR_URL).context["cms_menu"]

    item = next(entry for entry in menu if entry["title"] == ITEM_TITLE)
    assert item["active"] is True
    # Pozostałe pozycje nie mają się nagle podświetlić razem z nią.
    assert all(entry["active"] is False for entry in menu if entry["title"] != ITEM_TITLE)


# --- lista rozwijana (23.09.2026): rejestracja + plakaty --------------------------------------


def test_dropdown_has_registration_and_posters(client_for, competition, supervisor_registration_on):
    from apps.promo.tests.helpers import make_material

    make_material(competition)
    menu = client_for(competition).get("/").context["cms_menu"]

    item = next(entry for entry in menu if entry["title"] == ITEM_TITLE)
    assert [(kid["title"], kid["url"]) for kid in item["children"]] == [
        ("Rejestracja nauczyciela", REGISTER_SUPERVISOR_URL),
        ("Plakaty do pobrania", reverse("web:posters")),
    ]


def test_logged_in_user_gets_only_the_posters(client_for, competition, supervisor_registration_on):
    """Rejestracja nie ma sensu dla zalogowanego, plakaty – owszem."""
    from apps.promo.tests.helpers import make_material

    make_material(competition)
    client = client_for(competition)
    client.force_login(UserFactory())

    item = next(entry for entry in client.get("/").context["cms_menu"] if entry["title"] == ITEM_TITLE)
    assert [kid["title"] for kid in item["children"]] == ["Plakaty do pobrania"]


def test_posters_alone_keep_the_dropdown_when_registration_is_off(client_for, competition):
    from apps.promo.tests.helpers import make_material

    make_material(competition)
    item = next(
        entry
        for entry in client_for(competition).get("/").context["cms_menu"]
        if entry["title"] == ITEM_TITLE
    )
    assert [kid["title"] for kid in item["children"]] == ["Plakaty do pobrania"]


def test_dropdown_is_rendered_as_details(client_for, competition, supervisor_registration_on):
    body = client_for(competition).get("/").content.decode()
    menu = body.split('class="nav nav--cms"', 1)[1].split("</nav>", 1)[0]
    assert f">{ITEM_TITLE}</summary>" in menu
    assert f'href="{REGISTER_SUPERVISOR_URL}"' in menu
