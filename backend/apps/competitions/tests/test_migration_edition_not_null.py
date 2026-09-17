"""Zapytanie kontrolne wydania D (§ 4.4): wiersz bez konkursu **przerywa wdrożenie**.

Migracje ``competitions.0021`` i ``submissions.0007`` kładą ``NOT NULL`` na kolumnach, które przez
całe wydanie B stały puste. Sam ``ALTER TABLE`` też by się wywrócił, gdyby sieroty w bazie były –
ale wywróciłby się błędem PostgreSQL w środku transakcji wdrożeniowej, bez wskazania, czego
brakuje i ile tego jest. Zapytanie kontrolne zamienia to w jedno zdanie po polsku z nazwą tabeli,
wypowiedziane **przed** dotknięciem schematu.

Test jest więc testem **procedury wdrożeniowej**, a nie modelu: sprawdza, że wdrożenie zatrzymuje
się na kontroli, i że po naprawieniu danych przechodzi.

Kształt (przewijanie migracji, ``transaction=True``, fikstura przywracająca czoło także po
przerwanym teście) jest ten sam, co w ``test_migration_edition_competition.py`` i w
``apps/accounts/tests/test_migrations.py`` – i z tych samych powodów.
"""

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

from apps.competitions.models import Edition

from .factories import EditionFactory

#: Stan sprzed domknięcia: kolumny są, backfill przeszedł, ``NULL`` jest jeszcze dozwolony.
BEFORE = ("competitions", "0020_backfill_edition_competition")
AFTER = ("competitions", "0021_edition_competition_not_null")
SUBMISSIONS_AFTER = ("submissions", "0007_submission_competition_not_null")


def migrate_to(target) -> None:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target])
    executor.loader.build_graph()


def migrate_to_head() -> None:
    """Przywraca czoło migracji **wszystkich** aplikacji, nie tylko przewijanej."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())
    executor.loader.build_graph()


@pytest.fixture
def before_not_null(transactional_db, competition):  # noqa: ARG001 - baza, używana przez efekt uboczny
    """Baza cofnięta przed ``NOT NULL``. Fikstura ``competition`` idzie przed przewinięciem."""
    migrate_to(BEFORE)
    yield competition
    migrate_to_head()


@pytest.mark.django_db(transaction=True)
def test_the_control_query_stops_the_deployment_on_an_edition_without_a_competition(before_not_null):
    """Jedna edycja bez właściciela wystarczy, żeby wdrożenie stanęło – z nazwą tabeli w komunikacie."""
    competition = before_not_null
    edition = EditionFactory()
    # ``update``, a nie argument fabryki: kolumnę wypełnia sam ``Edition.save()``, więc sierotę
    # w bazie da się dziś zrobić wyłącznie z pominięciem modelu – tak samo, jak zrobiłaby ją
    # migracja, której ktoś nie uruchomił.
    Edition.objects.filter(pk=edition.pk).update(competition=None)

    with pytest.raises(RuntimeError, match="competitions_edition"):
        migrate_to(AFTER)

    # Schemat ma być **nietknięty**: kontrola stoi przed ``AlterField``, więc po przerwanym
    # wdrożeniu kolumna dalej przyjmuje ``NULL`` i wiersz da się naprawić bez kopii zapasowej.
    Edition.objects.filter(pk=edition.pk).update(competition=competition)
    assert Edition.objects.get(pk=edition.pk).competition_id == competition.pk


@pytest.mark.django_db(transaction=True)
def test_the_deployment_goes_through_once_every_row_has_an_owner(before_not_null):
    """Kontrola pozytywna: komplet właścicieli znaczy, że ``NOT NULL`` wchodzi bez przeszkód."""
    competition = before_not_null
    edition = EditionFactory(competition=competition)

    migrate_to(SUBMISSIONS_AFTER)

    assert Edition.objects.get(pk=edition.pk).competition_id == competition.pk
    # Sprawdzamy **bazę**, a nie model: pytanie brzmi „czy migracja postawiła ``NOT NULL``”,
    # a nie „czy ktoś zdjął ``null=True`` w pliku modeli”. ``update`` omija ``save()``, więc jest
    # jedyną drogą, którą taki wiersz mógłby jeszcze powstać.
    with pytest.raises(IntegrityError), transaction.atomic():
        Edition.objects.filter(pk=edition.pk).update(competition=None)
