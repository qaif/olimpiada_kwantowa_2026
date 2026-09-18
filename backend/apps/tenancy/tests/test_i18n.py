"""Wielojęzyczność treści nie rusza ani jednego adresu (§ 1.6.2, lista kontrolna § 0.5 p. 20–21).

Ten plik jest zapisem **zakazu**, a nie funkcji. Wagtail z włączonym ``WAGTAIL_I18N_ENABLED``
potrafi trzymać drzewo stron w kilku językach, ale najprostsza droga do wielojęzyczności w Django
– ``i18n_patterns`` – jest tu wykluczona: zrobiłaby z każdego adresu dwa, a adresy tego serwisu są
wklejone w listy aktywacyjne, w regulamin i w pisma (``/me/``, ``/results/12/``, ``/zgoda/…``).

Testy sprawdzają cztery rzeczy, z których żadna nie jest „czy tłumaczenie działa”:

1. ``i18n_patterns`` nie ma w urlconfie – ani w źródle, ani w zbudowanym drzewie wzorców,
2. wielojęzyczność treści jest domyślnie **wyłączona**, a języki treści to te same języki, co
   interfejsu,
3. kolejność warstw i ustawienia językowe zostają nietknięte,
4. instalacja Konkursu #1 ma nadal **jeden** ``Locale`` i ani jednego przycisku „Translate”.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.urls import get_resolver
from django.urls.resolvers import LocalePrefixPattern
from wagtail.models import Locale, Page

import config.urls
from apps.tenancy.models import Competition

from .conftest import HOST_A, root_page

#: Źródło mapy adresów. Test czyta **plik**, a nie zaimportowany moduł: zakaz dotyczy zapisu,
#: a import pokazałby wyłącznie to, co się wykonało.
URLCONF_SOURCE = Path(config.urls.__file__)


# --- 1. zakaz ``i18n_patterns`` ------------------------------------------------------------------


def test_no_i18n_patterns_in_urlconf():
    """Dowód z § 1.6.2 zapisany testem: prefiks języka nie wchodzi w żadnej postaci.

    Sam ``django.conf.urls.i18n`` zostaje – ``/i18n/setlang/`` to przełącznik języka, czyli widok
    pod stałym adresem, a nie prefiks doklejany do wszystkich pozostałych.
    """
    source = URLCONF_SOURCE.read_text(encoding="utf-8")

    assert not re.search(r"\bi18n_patterns\b", source), (
        "``i18n_patterns`` w config/urls.py zmieniłby każdy adres serwisu – patrz § 1.6.2."
    )
    assert "django.conf.urls.i18n" in source


def test_no_locale_prefix_anywhere_in_the_url_tree():
    """Druga strona tego samego zakazu: żaden wzorzec w drzewie nie niesie prefiksu języka.

    Test źródła pilnuje **naszego** pliku; ten pilnuje całości, łącznie z wzorcami dołożonymi
    przez aplikacje (``include``) – tam ``i18n_patterns`` też by zadziałało.
    """

    def prefixed(patterns) -> list[str]:
        found = []
        for pattern in patterns:
            if isinstance(getattr(pattern, "pattern", None), LocalePrefixPattern):
                found.append(str(pattern))
            found.extend(prefixed(getattr(pattern, "url_patterns", [])))
        return found

    assert prefixed(get_resolver().url_patterns) == []


@pytest.mark.django_db
def test_the_home_page_is_not_redirected_to_a_language_prefix(client_for, competition, settings):
    """Punkt 21 listy kontrolnej produkcji: ``/`` zostaje ``/``, a ``/pl/`` nie zaczyna istnieć."""
    settings.WAGTAIL_I18N_ENABLED = True
    client = client_for(competition)

    assert client.get("/").status_code != 302
    assert client.get("/pl/").status_code == 404


# --- 2. ustawienia wielojęzyczności treści ------------------------------------------------------


def test_content_translations_are_off_by_default():
    """Wdrożenie kodu niczego nie włącza – włączenie jest wpisem operatora w ``.env``."""
    assert django_settings.WAGTAIL_I18N_ENABLED is False


def test_content_languages_are_the_interface_languages():
    """Jedna lista języków, nie dwie: drugi komplet byłby drugim miejscem do zapomnienia."""
    assert django_settings.WAGTAIL_CONTENT_LANGUAGES == django_settings.LANGUAGES


def test_simple_translation_is_the_only_translation_app():
    """D19: tłumaczenie „strona po stronie” w ``/cms/``. XLIFF (``wagtail_localize``) – nie tu."""
    assert "wagtail.contrib.simple_translation" in django_settings.INSTALLED_APPS
    assert not [app for app in django_settings.INSTALLED_APPS if "localize" in app]


# --- 3. czego ten etap nie rusza ------------------------------------------------------------------


def test_language_settings_unchanged():
    assert django_settings.LANGUAGE_CODE == "pl"
    assert django_settings.LANGUAGES == [("pl", "polski"), ("en", "English")]
    assert [path.name for path in django_settings.LOCALE_PATHS] == ["locale"]


def test_middleware_order_unchanged():
    """``LocaleMiddleware`` → ``CompetitionMiddleware`` → ``PreferencesMiddleware`` (§ 4.4, T45).

    Kolejność jest kontraktem: język wybiera człowiek **zanim** poznamy konkurs, a ustawienie
    konta ma prawo nadpisać język domyślny konkursu, a nie odwrotnie.
    """
    order = list(django_settings.MIDDLEWARE)
    positions = {
        name: order.index(name)
        for name in (
            "django.middleware.locale.LocaleMiddleware",
            "apps.tenancy.middleware.CompetitionMiddleware",
            "apps.accounts.preferences.PreferencesMiddleware",
        )
    }

    assert (
        positions["django.middleware.locale.LocaleMiddleware"]
        < positions["apps.tenancy.middleware.CompetitionMiddleware"]
        < positions["apps.accounts.preferences.PreferencesMiddleware"]
    )


def test_competition_site_stays_one_to_one():
    """Druga witryna konkursu wchodzi aliasem; ``Competition.site`` zostaje ``OneToOne`` (D17)."""
    field = Competition._meta.get_field("site")

    assert field.one_to_one
    assert field.remote_field.model._meta.label == "wagtailcore.Site"


# --- 4. instalacja Konkursu #1 --------------------------------------------------------------------


@pytest.mark.django_db
def test_the_installation_still_has_exactly_one_locale(competition):
    """Migracje etapu 2 nie zakładają drugiego ``Locale`` i nie ruszają istniejącego wiersza."""
    locales = list(Locale.objects.values_list("language_code", flat=True))

    assert locales == [django_settings.LANGUAGE_CODE]


@pytest.mark.django_db
def test_every_page_of_the_installation_is_in_the_default_locale(competition):
    default = Locale.objects.get(language_code=django_settings.LANGUAGE_CODE)

    assert not Page.objects.exclude(locale=default).exists()


@pytest.mark.django_db
def test_translate_button_does_not_appear_with_a_single_locale(competition, settings):
    """§ 2.5: przy jednym ``Locale`` przycisk „Translate” nie ma dokąd prowadzić i się nie pojawia.

    Wołamy hak Wagtaila wprost, a nie przez ``/cms/``: interesuje nas **reguła** biblioteki, a nie
    to, czy akurat udało się zalogować redaktora. Użytkownikiem jest superużytkownik, czyli ktoś,
    komu uprawnienie na pewno nie brakuje – gdyby przycisk mógł się pojawić, pojawiłby się jemu.
    """
    from wagtail.contrib.simple_translation.wagtail_hooks import page_listing_more_buttons

    from apps.accounts.tests.factories import UserFactory

    settings.WAGTAIL_I18N_ENABLED = True
    operator = UserFactory(is_staff=True, is_superuser=True)
    page = root_page().add_child(instance=Page(title="Strona", slug="strona-testowa"))

    assert list(page_listing_more_buttons(page, operator)) == []


@pytest.mark.django_db
def test_the_competition_has_no_aliases(competition):
    """Konkurs #1 nie ma ani jednego aliasu, więc gałąź aliasów nie wykonuje się dla niego."""
    assert not competition.site_aliases.exists()
    assert competition.has_feature("content_translations") is False


@pytest.mark.django_db
def test_host_of_the_competition_resolves_the_same_with_content_i18n_on(competition, settings):
    """Włączenie ustawienia nie zmienia rozstrzygania konkursu pod jego własną domeną."""
    from apps.tenancy.resolution import resolve_competition

    from .test_aliases import request_for

    settings.WAGTAIL_I18N_ENABLED = True

    assert resolve_competition(request_for(HOST_A)) == competition
