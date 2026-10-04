"""Migracja ``tenancy.0011``: przełącznik „angielska wersja interfejsu” → zbiór języków konkursu.

I18N-01 § 1: każdy konkurs ma po migracji oferować **dokładnie to, co oferował przed nią**:

- przełącznik wyłączony (tak stoi Olimpiada Kwantowa) → sam język domyślny, czyli ``["pl"]``,
- przełącznik włączony → oba języki instalacji sprzed zmiany, ``["pl", "en"]``,
- konkurs z ``default_language="en"`` i wyłączonym przełącznikiem → ``["en"]`` (jedyna świadoma
  zmiana: wcześniej taki konkurs był pokazywany po polsku wbrew własnemu językowi domyślnemu).

Bazę przewija raz na moduł fikstura :func:`rewound` (``apps/core/tests/migration_helpers.py``); każdy
test zastaje ją w punkcie :data:`BEFORE` i sam puszcza migrację do :data:`AFTER`.
"""

from __future__ import annotations

import pytest

from apps.core.tests.migration_helpers import MIGRATION_TESTS, migrate_to, rewound_database
from apps.tenancy.models import Competition

pytestmark = MIGRATION_TESTS

BEFORE = [
    ("tenancy", "0010_path_prefix_routing_on_platform"),
    ("cms", "0030_hero_slider_images_intro_deadline"),
]
AFTER = [
    ("tenancy", "0011_competition_interface_languages"),
    ("cms", "0030_hero_slider_images_intro_deadline"),
]


@pytest.fixture(scope="module")
def rewound(django_db_setup, django_db_blocker):
    """Baza sprzed migracji: kolumna przełącznika jest, zbioru języków jeszcze nie ma."""
    with rewound_database(django_db_blocker, BEFORE) as db:
        yield db


def _languages(rewound) -> list[str]:
    """Sama kolumna zbioru języków. Żywy model zna kolumny z **późniejszych** migracji
    (``0012``: ``site_logo``, ``social_image``), których baza cofnięta do ``0011`` jeszcze nie ma –
    pełny ``SELECT`` modelu padałby na brakującej kolumnie."""
    return (
        Competition.objects.filter(pk=rewound.competition.pk)
        .values_list("interface_languages", flat=True)
        .get()
    )


def _set_switch(state, competition, enabled: bool) -> None:
    """Przełącznik witryny konkursu – modelem **historycznym** (żywy model kolumny już nie zna)."""
    settings_model = state.get_model("cms", "SiteSettings")
    row = settings_model.objects.filter(site_id=competition.site_id).first()
    if row is None:
        settings_model.objects.create(site_id=competition.site_id, english_interface_enabled=enabled)
    else:
        row.english_interface_enabled = enabled
        row.save(update_fields=["english_interface_enabled"])


def test_switch_off_keeps_polish_only(rewound):
    _set_switch(rewound.apps, rewound.competition, enabled=False)

    migrate_to(AFTER)

    assert _languages(rewound) == ["pl"]


def test_switch_on_keeps_polish_and_english(rewound):
    _set_switch(rewound.apps, rewound.competition, enabled=True)

    migrate_to(AFTER)

    assert _languages(rewound) == ["pl", "en"]


def test_english_default_without_the_switch_becomes_english(rewound):
    _set_switch(rewound.apps, rewound.competition, enabled=False)
    Competition.objects.filter(pk=rewound.competition.pk).update(default_language="en")

    migrate_to(AFTER)

    assert _languages(rewound) == ["en"]


def test_reverse_restores_the_switch(rewound):
    _set_switch(rewound.apps, rewound.competition, enabled=True)
    migrate_to(AFTER)

    state = migrate_to(BEFORE)

    settings_model = state.get_model("cms", "SiteSettings")
    assert settings_model.objects.get(site_id=rewound.competition.site_id).english_interface_enabled is True


def test_english_switched_on_only_on_an_alias_site_is_kept(rewound):
    """Przełącznik czytała witryna żądania – angielski włączony na witrynie aliasu też się liczy."""
    from wagtail.models import Locale, Page, Site

    _set_switch(rewound.apps, rewound.competition, enabled=False)
    root = Page.objects.filter(depth=1).order_by("path").first()
    alias_site = Site.objects.create(hostname="en.alias.invalid", port=80, root_page=root)
    locale = Locale.objects.order_by("pk").first()
    rewound.apps.get_model("tenancy", "CompetitionSiteAlias").objects.create(
        competition_id=rewound.competition.pk, site_id=alias_site.pk, locale_id=locale.pk
    )
    rewound.apps.get_model("cms", "SiteSettings").objects.create(
        site_id=alias_site.pk, english_interface_enabled=True
    )

    migrate_to(AFTER)

    assert _languages(rewound) == ["pl", "en"]
