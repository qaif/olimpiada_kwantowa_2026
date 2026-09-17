"""Migracja danych ``support.0003``: zastana korespondencja dostaje organizatora Konkursu #1.

To nie jest kosmetyka. Od wydania D kolejka koordynatora jest zawężona do jego konkursu, a pusty
konkurs znaczy „sprawa do operatora platformy”. Gdyby backfill nie zadziałał, cała dotychczasowa
korespondencja Olimpiady Kwantowej – razem z otwartymi sprawami i ich licznikiem – zniknęłaby
z panelu w dniu wdrożenia. To jest dokładnie ta klasa regresji, której zabrania § 0.

Test wygląda inaczej niż reszta pakietu z tych samych powodów, co ``apps/accounts/tests/
test_migrations.py``: przewijanie migracji to DDL po DML, więc potrzebny jest ``transaction=True``,
a fikstura przywraca czoło migracji także wtedy, gdy test przerwie się w połowie.
"""

import pytest

from apps.core.tests.migration_helpers import ensure_competition, migrate_to, migrate_to_head

BEFORE = [("support", "0002_supportticket_competition")]
AFTER = [("support", "0003_backfill_supportticket_competition")]


@pytest.fixture
def rewound_apps(transactional_db):  # noqa: ARG001 - fikstura bazy, używana przez efekt uboczny
    yield migrate_to(BEFORE)
    migrate_to_head()


@pytest.mark.django_db(transaction=True)
def test_existing_tickets_get_the_sole_competition(rewound_apps):
    """Sprawa sprzed wdrożenia dostaje Konkurs #1, a sprawa z kodu zostaje nietknięta.

    Oba przypadki w jednym teście z rozmysłem: reguła jest jedna – warunek backfillu stoi
    wyłącznie na ``NULL`` – więc to jest jedno zdanie o dwóch skutkach, a nie dwa zdania.
    """
    SupportTicket = rewound_apps.get_model("support", "SupportTicket")
    competition = ensure_competition(rewound_apps)
    orphan = SupportTicket.objects.create(
        email="ktos@example.invalid", subject="Sprawa sprzed wdrożenia", competition=None
    )
    # Wydania C i D stoją obok siebie: ``open_ticket`` właściciela już wypełnia, więc w chwili
    # migracji część wierszy ma go wpisanego przez kod.
    assigned = SupportTicket.objects.create(
        email="inny@example.invalid", subject="Sprawa z kodu", competition=competition
    )

    Migrated = migrate_to(AFTER).get_model("support", "SupportTicket")

    assert Migrated.objects.get(pk=orphan.pk).competition_id == competition.pk
    assert Migrated.objects.get(pk=assigned.pk).competition_id == competition.pk
