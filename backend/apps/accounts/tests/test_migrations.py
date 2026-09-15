"""Migracja danych ``accounts.0010``: istniejące, działające konta dostają ``email_verified_at``.

To nie jest kosmetyka. Od tej zmiany logowanie jest zamknięte dla kont bez potwierdzonego adresu,
a listę „konta oczekujące na aktywację” w panelu koordynatora buduje ten sam warunek. Gdyby pole
zostało puste dla kont, które już działają, cała dotychczasowa baza uczestników i recenzentów
zostałaby w jednej chwili odcięta od panelu – bez niczyjej winy i bez linku w skrzynce, bo listy
aktywacyjne nigdy do nich nie poszły.

Test wygląda inaczej niż reszta pakietu z tych samych powodów, co ``apps/cms/tests/test_migrations.py``:
przewijanie migracji to DDL po DML, więc potrzebny jest ``transaction=True``, a fixture przywraca
czoło migracji także wtedy, gdy test przerwie się w połowie.
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

BEFORE = ("accounts", "0009_participant_consents")
AFTER = ("accounts", "0010_activation_and_phone")


def migrate_to(target):
    """Przewija bazę do wskazanej migracji i zwraca stan aplikacji z tamtego momentu."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target])
    executor.loader.build_graph()
    return executor.loader.project_state([target]).apps


def migrate_to_head() -> None:
    """Przywraca czoło migracji **wszystkich** aplikacji, nie tylko przewijanej.

    ``migrate_to(AFTER)`` nie wystarcza: cofnięcie jednej aplikacji zdejmuje po drodze każdą
    migrację z innych aplikacji, która od niej zależy, a powrót do konkretnego celu przywraca
    wyłącznie jego przodków. Reszta pakietu zastawała wtedy bazę bez tamtych tabel – i wywracała
    się w zupełnie innym miejscu, kilka minut później.
    """
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())
    executor.loader.build_graph()


@pytest.fixture
def rewound_apps(transactional_db):  # noqa: ARG001 - fixture bazy, używana przez efekt uboczny
    yield migrate_to(BEFORE)
    migrate_to_head()


@pytest.mark.django_db(transaction=True)
def test_existing_accounts_keep_access_after_the_migration(rewound_apps):
    User = rewound_apps.get_model("accounts", "User")
    joined = timezone.now().replace(microsecond=0)
    working = User.objects.create(email="dziala@example.test", is_active=True, date_joined=joined)
    # Konto zablokowane przez organizatora: ``is_active=False`` znaczy tu „zablokowane”, a nie
    # „niepotwierdzone”, i migracja nie ma prawa zrobić z niego konta z potwierdzonym adresem.
    blocked = User.objects.create(email="zablokowane@example.test", is_active=False, date_joined=joined)

    new_apps = migrate_to(AFTER)
    MigratedUser = new_apps.get_model("accounts", "User")

    assert MigratedUser.objects.get(pk=working.pk).email_verified_at == joined
    assert MigratedUser.objects.get(pk=blocked.pk).email_verified_at is None
    # Telefon dokładamy jako puste pole – wpisanie wartości zastępczej byłoby wpisaniem
    # uczestnikowi danych, których nie podał.
    assert MigratedUser.objects.get(pk=working.pk).email == "dziala@example.test"
