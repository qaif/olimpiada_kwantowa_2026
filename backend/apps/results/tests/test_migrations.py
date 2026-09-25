"""Migracja ``results.0005``: szablon dokumentu dostaje konkurs, a kolumna – ``NOT NULL``.

Istotny jest tu **szablon bez edycji**. Dopóki zakres szedł przez ``edition``, taki wiersz nie
należał do nikogo, a to właśnie na nim kończy dopasowanie ``resolve_template`` – czyli winieta
organizatora A trafiłaby na dyplom organizatora B bez żadnego kliknięcia. Backfill ma go przypisać
jedynemu konkursowi instalacji, a nie zostawić pustego.

Bazę przewija fikstura modułu w transakcji wycofywanej na końcu modułu – wycofanie przywraca czoło
migracji bez ``migrate`` i bez ``flush`` (``apps/core/tests/migration_helpers.py``).
"""

import pytest
from django.utils import timezone

from apps.core.tests.migration_helpers import (
    MIGRATION_TESTS,
    ensure_competition,
    migrate_to,
    rewound_database,
)

pytestmark = MIGRATION_TESTS

#: Cel przewinięcia jest listą, bo backfill idzie drogą przez **wypełnioną** kolumnę
#: ``Edition.competition`` – sam ``results.0004`` zdjąłby ją z bazy razem z resztą wydania B.
BEFORE = [
    ("results", "0004_certificate_templates_and_workshop_attendance"),
    ("competitions", "0020_backfill_edition_competition"),
]
AFTER = [("results", "0005_certificatetemplate_competition")]


@pytest.fixture(scope="module")
def rewound_apps(django_db_setup, django_db_blocker):
    with rewound_database(django_db_blocker, BEFORE) as db:
        yield db.apps


def test_templates_get_a_competition_including_those_without_an_edition(rewound_apps):
    Edition = rewound_apps.get_model("competitions", "Edition")
    CertificateTemplate = rewound_apps.get_model("results", "CertificateTemplate")
    competition = ensure_competition(rewound_apps)
    edition = Edition.objects.create(year_label="I (2025/2026)", competition=competition)

    pinned = CertificateTemplate.objects.create(
        name="Winieta jubileuszowa", kind="", edition=edition, created_at=timezone.now()
    )
    shelf = CertificateTemplate.objects.create(
        name="Szablon na wszystko", kind="", edition=None, created_at=timezone.now()
    )

    new_apps = migrate_to(AFTER)

    Migrated = new_apps.get_model("results", "CertificateTemplate")
    assert Migrated.objects.get(pk=pinned.pk).competition_id == competition.pk
    assert Migrated.objects.get(pk=shelf.pk).competition_id == competition.pk
    assert Migrated._meta.get_field("competition").null is False
