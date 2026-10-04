"""Migracja ``delegation_logistics.0003_visa_letter_workflow`` (VISA-01): cofnięcie i ponowne zastosowanie.

Migracja jest odwracalna w sensie schematu (nowa tabela wniosków, nowe kolumny rejestru listów), ale
cofnięcie zabiera kody weryfikacyjne – a ponowne zastosowanie nadaje **nowe** (OPERACJE § 31.8, L4).
Test sprawdza obie połowy na bazie przewiniętej w transakcji (``apps/core/tests/migration_helpers.py``).
"""

from __future__ import annotations

import pytest
from django.db import connection

from apps.core.tests.migration_helpers import MIGRATION_TESTS, migrate_to, rewound_database

pytestmark = MIGRATION_TESTS

BEFORE = ("delegation_logistics", "0002_encrypted_diet")
AFTER = ("delegation_logistics", "0003_visa_letter_workflow")


@pytest.fixture(scope="module")
def rewound(django_db_setup, django_db_blocker):
    with rewound_database(django_db_blocker, BEFORE) as db:
        yield db


def _columns(table: str) -> set[str]:
    with connection.cursor() as cursor:
        return {column.name for column in connection.introspection.get_table_description(cursor, table)}


def test_reverse_and_forward_round_trip(rewound):
    tables = connection.introspection.table_names()
    assert "delegation_logistics_letterrequest" not in tables
    assert "verification_code" not in _columns("delegation_logistics_invitationletter")

    migrate_to(AFTER)
    assert "delegation_logistics_letterrequest" in connection.introspection.table_names()
    assert {"verification_code", "verification_base_url", "revoked_at"} <= _columns(
        "delegation_logistics_invitationletter"
    )

    migrate_to(BEFORE)
    assert "delegation_logistics_letterrequest" not in connection.introspection.table_names()
