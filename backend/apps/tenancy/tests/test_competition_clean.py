"""Walidacja ``Competition.clean()`` – prefiks ścieżki nie może przechwycić istniejącej strony.

Druga strona reguły z ``docs/UNIWERSALNY-ETAP-1.md`` § 2.3: ``CMSPage.clean()`` pilnuje, żeby nowa
strona nie zajęła prefiksu konkursu; tutaj pilnujemy, żeby nowy prefiks nie zajął sluga strony
drugiego poziomu domyślnej witryny (np. ``zadania`` Konkursu #1).
"""

import pytest
from django.core.exceptions import ValidationError
from wagtail.models import Site

from apps.cms.models import RESERVED_SLUGS, ContentPage, taken_first_segments
from apps.tenancy.models import Competition, RoutingMode

pytestmark = pytest.mark.django_db


def _competition_with_prefix(prefix: str) -> Competition:
    return Competition(
        slug=f"test-{prefix}",
        name="Konkurs testowy",
        routing_mode=RoutingMode.PATH,
        path_prefix=prefix,
    )


def _existing_page_slug() -> str:
    """Slug strony drugiego poziomu domyślnej witryny – z drzewa albo utworzony na potrzeby testu."""
    taken = taken_first_segments() - RESERVED_SLUGS
    if taken:
        return sorted(taken)[0]
    root = Site.objects.get(is_default_site=True).root_page.specific
    page = ContentPage(title="Strona próbna", slug="strona-probna")
    root.add_child(instance=page)
    return page.slug


def test_prefix_equal_to_an_existing_page_slug_is_rejected(competition):
    slug = _existing_page_slug()

    with pytest.raises(ValidationError) as excinfo:
        _competition_with_prefix(slug).clean()

    assert "adresem strony" in excinfo.value.message_dict["path_prefix"][0]


def test_prefix_from_the_application_reserved_list_keeps_its_own_message(competition):
    with pytest.raises(ValidationError) as excinfo:
        _competition_with_prefix("coordinator").clean()

    assert "adresów aplikacji" in excinfo.value.message_dict["path_prefix"][0]


def test_free_prefix_passes(competition):
    _competition_with_prefix("fizyczna").clean()
