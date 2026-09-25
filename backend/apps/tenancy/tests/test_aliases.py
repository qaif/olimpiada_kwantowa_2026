"""Alias witryny konkursu: druga domena, ten sam konkurs, drugi język treści (§ 1.6.2).

Sprawdzamy dwie rzeczy naraz, bo tylko razem mają sens:

1. **że gałąź działa** – host aliasu oddaje ten sam konkurs, co domena główna, a drzewo stron
   serwuje mu Wagtail z korzenia drugiej witryny,
2. **że dla Konkursu #1 nie istnieje** – bez ``WAGTAIL_I18N_ENABLED`` nie ma jej w zapytaniu,
   a liczba zapytań rozstrzygania jest ta sama z aliasem i bez niego.

Punkt drugi jest ważniejszy: pierwszy opisuje funkcję, której nikt jeszcze nie włączył, drugi –
instalację, która chodzi na produkcji.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from wagtail.models import Locale, Page, Site

from apps.tenancy.aliases import CompetitionSiteAlias, alias_match, content_translations_enabled
from apps.tenancy.resolution import resolve_competition, resolve_for_request

from .conftest import HOST_A, HOST_B, root_page

pytestmark = pytest.mark.django_db

#: Host drugiej witryny. Sufiks ``.invalid`` jest na liście dozwolonych hostów pakietu
#: (autouse ``_allow_test_hosts``), więc żądanie dochodzi do **naszego** rozstrzygania, a nie
#: kończy się kodem 400 Django.
HOST_A_EN = "en.kwantowa.invalid"


def request_for(host: str, path: str = "/"):
    return RequestFactory().get(path, HTTP_HOST=host)


def english_locale() -> Locale:
    """``Locale`` treści angielskiej. Instalacja ma dziś **jeden** ``Locale`` – ``pl``."""
    locale, _ = Locale.objects.get_or_create(language_code="en")
    return locale


def english_site(hostname: str = HOST_A_EN) -> Site:
    """Druga witryna z własnym korzeniem drzewa stron w ``Locale`` angielskim."""
    locale = english_locale()
    root = root_page().add_child(
        instance=Page(title=hostname, slug=hostname.replace(".", "-"), locale=locale)
    )
    return Site.objects.create(hostname=hostname, port=80, site_name=hostname, root_page=root)


def attach_alias(competition, *, site: Site | None = None) -> CompetitionSiteAlias:
    """Alias założony tak, jak zakłada go operator: witryna + język + flaga konkursu."""
    competition.feature_flags = {**(competition.feature_flags or {}), "content_translations": True}
    competition.save(update_fields=["feature_flags"])
    return CompetitionSiteAlias.objects.create(
        competition=competition, site=site or english_site(), locale=english_locale()
    )


@pytest.fixture
def i18n_enabled(settings):
    """Instalacja prowadząca drzewa stron w kilku językach (``.env`` operatora)."""
    settings.WAGTAIL_I18N_ENABLED = True
    return settings


# --- gałąź wyłączona: dzisiejszy Konkurs #1 ---------------------------------------------------


def test_content_translations_are_disabled_out_of_the_box(settings):
    """Wdrożenie kodu nie włącza niczego – włącza operator wpisem w ``.env`` (§ 1.6.2)."""
    assert settings.WAGTAIL_I18N_ENABLED is False
    assert content_translations_enabled() is False


def test_alias_branch_leaves_no_trace_in_the_query_without_the_setting(competition):
    """Puste ``Q()`` znaczy brak złączenia, a nie złączenie zawsze fałszywe.

    Różnica jest widoczna dopiero w planie zapytania: warunek „zawsze fałszywy” i tak wymusiłby
    ``LEFT OUTER JOIN`` na tabeli aliasów przy **każdym** żądaniu instalacji jednojęzycznej.
    """
    assert not alias_match(competition.site).children
    assert not alias_match(None).children


def test_alias_is_ignored_while_the_installation_is_single_language(competition):
    """Wiersz w bazie bez ``WAGTAIL_I18N_ENABLED`` nie wystawia ani jednej domeny.

    To jest odwrót w jedną linijkę: operator, który wyłączy ustawienie, wraca do stanu sprzed
    włączenia bez kasowania czegokolwiek.
    """
    attach_alias(competition)

    assert resolve_competition(request_for(HOST_A_EN)) is None


def queries_to_resolve(host: str) -> int:
    """Liczba zapytań jednego rozstrzygnięcia – mierzona, a nie wpisana progiem.

    Wartość bezwzględna należy do ``test_invariants.QUERY_BUDGET`` (tam jest mierzona na całej
    stronie); tutaj chodzi o **różnicę** między instalacją z aliasami i bez, a ta ma być zerowa.
    """
    with CaptureQueriesContext(connection) as queries:
        resolve_competition(request_for(host))
    return len(queries.captured_queries)


def test_resolution_of_the_main_host_costs_the_same_with_and_without_aliases(competition, settings):
    """Budżet zapytań (§ 5.6) nie rośnie: gałąź aliasów dokłada alternatywę, a nie zapytanie."""
    attach_alias(competition)

    settings.WAGTAIL_I18N_ENABLED = False
    without = queries_to_resolve(HOST_A)
    settings.WAGTAIL_I18N_ENABLED = True
    with_aliases = queries_to_resolve(HOST_A)

    assert with_aliases == without


# --- gałąź włączona ----------------------------------------------------------------------------


def test_alias_host_resolves_to_the_same_competition(competition, i18n_enabled):
    """Sedno § 1.6.2: druga domena to ten sam konkurs, a nie drugi."""
    attach_alias(competition)

    assert resolve_competition(request_for(HOST_A_EN)) == competition


def test_alias_host_does_not_bring_a_path_prefix(competition, i18n_enabled):
    """Adresy zostają bez zmian: alias nie jest prefiksem ścieżki i niczego z adresu nie zdejmuje."""
    attach_alias(competition)

    resolution = resolve_for_request(request_for(HOST_A_EN, "/me/"))

    assert resolution.competition == competition
    assert resolution.path_prefix == ""


def test_main_host_still_resolves_when_an_alias_exists(competition, i18n_enabled):
    attach_alias(competition)

    assert resolve_competition(request_for(HOST_A)) == competition


def test_alias_without_the_competition_flag_resolves_to_nothing(competition, i18n_enabled):
    """Dwie bramki, nie jedna: ustawienie instalacji i flaga **tego** konkursu.

    Alias założony przed włączeniem ``content_translations`` jest konfiguracją w toku. Bez tej
    reguły o tym, czy domena już odpowiada, decydowałaby kolejność dwóch kroków operatora.
    """
    attach_alias(competition)
    competition.feature_flags = {"content_translations": False}
    competition.save(update_fields=["feature_flags"])

    assert resolve_competition(request_for(HOST_A_EN)) is None


def test_alias_of_an_inactive_competition_resolves_to_nothing(competition, i18n_enabled):
    attach_alias(competition)
    competition.is_active = False
    competition.save(update_fields=["is_active"])

    assert resolve_competition(request_for(HOST_A_EN)) is None


def test_alias_of_one_competition_is_invisible_to_the_other(competition, other_competition, i18n_enabled):
    """Reguła izolacji na poziomie rozstrzygania: alias konkursu A nie przesuwa konkursu B."""
    attach_alias(competition)

    assert resolve_competition(request_for(HOST_B)) == other_competition
    assert resolve_competition(request_for(HOST_A_EN)) == competition


def test_path_prefix_still_wins_over_the_alias(competition, other_competition, i18n_enabled):
    """Pierwszeństwo z etapu 1 § 2.3 zostaje: jawne wskazanie w adresie bije dopasowanie hosta."""
    from apps.tenancy.models import RoutingMode

    from .conftest import open_path_prefixes

    attach_alias(competition)
    open_path_prefixes(competition)
    other_competition.routing_mode = RoutingMode.PATH
    other_competition.path_prefix = "inny"
    other_competition.save(update_fields=["routing_mode", "path_prefix"])

    resolution = resolve_for_request(request_for(HOST_A_EN, "/inny/me/"))

    assert resolution.competition == other_competition
    assert resolution.path_prefix == "inny"


def test_several_aliases_do_not_multiply_the_result(competition, i18n_enabled):
    """Złączenie mnoży wiersze; ``distinct()`` sprawia, że limit liczy konkursy, a nie wiersze."""
    attach_alias(competition)
    german = Locale.objects.create(language_code="de")
    site = english_site("de.kwantowa.invalid")
    site.root_page.locale = german
    site.root_page.save(update_fields=["locale"])
    CompetitionSiteAlias.objects.create(competition=competition, site=site, locale=german)

    assert resolve_competition(request_for(HOST_A_EN)) == competition
    assert resolve_competition(request_for("de.kwantowa.invalid")) == competition


# --- reguły modelu ------------------------------------------------------------------------------


def test_the_main_site_of_a_competition_cannot_be_its_alias(competition):
    """Dwie odpowiedzi na pytanie „w jakim języku jest ta domena” to o jedną za dużo."""
    alias = CompetitionSiteAlias(competition=competition, site=competition.site, locale=english_locale())

    with pytest.raises(ValidationError) as error:
        alias.full_clean()

    assert "site" in error.value.error_dict


def test_the_site_of_another_competition_cannot_become_an_alias(competition, other_competition):
    alias = CompetitionSiteAlias(
        competition=competition, site=other_competition.site, locale=english_locale()
    )

    with pytest.raises(ValidationError) as error:
        alias.full_clean()

    assert "site" in error.value.error_dict


def test_locale_must_match_the_root_page_of_the_site(competition):
    """Deklaracja niezgodna z drzewem otwierałaby stronę poprawnie, tylko nie w tym języku."""
    site = english_site()
    alias = CompetitionSiteAlias(
        competition=competition, site=site, locale=Locale.objects.get(language_code="pl")
    )

    with pytest.raises(ValidationError) as error:
        alias.full_clean()

    assert "locale" in error.value.error_dict


def test_a_correct_alias_passes_validation(competition):
    site = english_site()
    alias = CompetitionSiteAlias(competition=competition, site=site, locale=english_locale())

    alias.full_clean()


def test_one_site_per_language_per_competition(competition):
    """Dwie angielskie witryny jednego konkursu znaczyłyby dwa adresy tej samej strony."""
    attach_alias(competition)
    second = english_site("en2.kwantowa.invalid")

    with pytest.raises(IntegrityError):
        CompetitionSiteAlias.objects.create(competition=competition, site=second, locale=english_locale())


def test_deleting_the_site_removes_the_alias(competition):
    """``CASCADE``, a nie ``PROTECT``: wiersz nie niesie danych zawodów, tylko przypisanie hosta."""
    alias = attach_alias(competition)
    alias.site.delete()

    assert not CompetitionSiteAlias.objects.filter(pk=alias.pk).exists()


def test_alias_is_readable_from_the_competition(competition):
    alias = attach_alias(competition)

    assert list(competition.site_aliases.all()) == [alias]
