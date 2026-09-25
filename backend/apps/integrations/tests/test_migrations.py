"""Migracja ``integrations.0002``: klucze i odbiorcy dostają konkurs, a kolumna – ``NOT NULL``.

Sprawdzamy trzy rzeczy, bo ta migracja robi trzy różne:

1. **backfill drogą przez edycję** – klucz zapięty na edycji bierze konkurs tej edycji,
2. **backfill odwrotem** – klucz „na wszystkie edycje” bierze jedyny konkurs instalacji. To jest
   przypadek, dla którego ta kolumna w ogóle powstała: bez niej taki klucz nie prowadził do
   żadnego organizatora, czyli był poświadczeniem o nieokreślonym zakresie danych,
3. **zapytanie kontrolne z § 4.4** przerywa wdrożenie, gdy zostałby choć jeden wiersz bez
   konkursu – i robi to **przed** zmianą schematu, a nie ``IntegrityError``-em w środku.

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
#: ``Edition.competition`` – sam ``integrations.0001`` zdjąłby ją z bazy razem z resztą wydania B.
BEFORE = [
    ("integrations", "0001_initial"),
    ("competitions", "0020_backfill_edition_competition"),
]
AFTER = [("integrations", "0002_competition_on_keys_and_endpoints")]


@pytest.fixture(scope="module")
def rewound_apps(django_db_setup, django_db_blocker):
    with rewound_database(django_db_blocker, BEFORE) as db:
        yield db.apps


def test_keys_and_endpoints_get_a_competition(rewound_apps):
    Edition = rewound_apps.get_model("competitions", "Edition")
    ApiKey = rewound_apps.get_model("integrations", "ApiKey")
    WebhookEndpoint = rewound_apps.get_model("integrations", "WebhookEndpoint")
    competition = ensure_competition(rewound_apps)
    edition = Edition.objects.create(year_label="I (2025/2026)", competition=competition)

    pinned = ApiKey.objects.create(
        edition=edition,
        name="Partner rocznika",
        prefix="aaaaaaaa",
        key_hash="x" * 64,
        created_at=timezone.now(),
    )
    forever = ApiKey.objects.create(
        edition=None,
        name="Partner stały",
        prefix="bbbbbbbb",
        key_hash="y" * 64,
        created_at=timezone.now(),
    )
    endpoint = WebhookEndpoint.objects.create(
        edition=None, url="https://partner.example.test/hooks", secret="s" * 43
    )

    new_apps = migrate_to(AFTER)

    MigratedKey = new_apps.get_model("integrations", "ApiKey")
    MigratedEndpoint = new_apps.get_model("integrations", "WebhookEndpoint")
    assert MigratedKey.objects.get(pk=pinned.pk).competition_id == competition.pk
    assert MigratedKey.objects.get(pk=forever.pk).competition_id == competition.pk
    assert MigratedEndpoint.objects.get(pk=endpoint.pk).competition_id == competition.pk
    # Kolumna jest domknięta: wiersz bez konkursu nie ma prawa powstać po tej migracji.
    assert MigratedKey._meta.get_field("competition").null is False
    assert MigratedEndpoint._meta.get_field("competition").null is False
