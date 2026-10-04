"""Menu serwisu w języku interfejsu: standardowe tytuły z szablonu konkursu (IQO, 4.10.2026)."""

import pytest
from django.test import RequestFactory
from django.utils import translation

from apps.cms.context_processors import (
    FALLBACK_MENU,
    HOME_ITEM_TITLE,
    MENU_TITLES,
    STANDARD_TITLES,
    _label,
    _menu_item,
)

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("language", "expected"),
    [("pl", "Zadania"), ("en", "Problems"), ("es", "Problemas")],
)
def test_standard_page_title_follows_the_interface_language(language, expected):
    with translation.override(language):
        assert _label("Zadania") == expected


def test_title_changed_by_the_editor_stays_as_written():
    with translation.override("en"):
        assert _label("Nasze zadania 2026") == "Nasze zadania 2026"


def test_menu_item_and_new_catalog_entries():
    request = RequestFactory().get("/")
    with translation.override("ru"):
        assert _menu_item("dokumenty", "Dokumenty", "/dokumenty/", request)["title"] == "Документы"
        assert _label("Harmonogram") == "Расписание"
        assert _label("Strona główna") != "Strona główna"
    with translation.override("pl"):
        assert _menu_item("dokumenty", "Dokumenty", "/dokumenty/", request)["title"] == "Dokumenty"


def test_fallback_and_module_titles_are_in_the_translated_catalog():
    titles = {item["title"] for item in FALLBACK_MENU} | {HOME_ITEM_TITLE, MENU_TITLES["komitety"]}
    assert titles <= STANDARD_TITLES
