"""Komenda ``migrate_statements`` (przegląd Critica T-09, finding 6).

Zmiana ustawień nie rusza plików już zapisanych: baza trzyma samą nazwę obiektu, a nazwa w starym
i nowym storage wygląda identycznie. Bez tej komendy instalacja sprzed T-09 miałaby treści zadań
dalej w miejscu, które stało się publicznym bucketem Wagtaila – i rekordy wskazujące na pustkę
w ``private_media``.
"""

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, storages
from django.core.management import call_command

from apps.competitions.models import Problem
from apps.competitions.storage import PRIVATE_MEDIA_ALIAS
from apps.competitions.tests.factories import StageFactory

pytestmark = pytest.mark.django_db

LEGACY_NAME = "problems/statements/stara-tresc.pdf"
PDF = b"%PDF-1.4 stara tresc zadania"


@pytest.fixture
def legacy_problem():
    """Zadanie „sprzed T-09”: plik w starym ``MEDIA_ROOT``, w ``private_media`` go nie ma."""
    problem = Problem.objects.create(stage=StageFactory(), number=1, title="Zadanie z archiwum")
    FileSystemStorage().save(LEGACY_NAME, ContentFile(PDF))
    Problem.objects.filter(pk=problem.pk).update(statement_pdf=LEGACY_NAME)
    problem.refresh_from_db()
    return problem


def test_command_moves_the_file_into_the_private_storage(legacy_problem):
    assert not storages[PRIVATE_MEDIA_ALIAS].exists(LEGACY_NAME)

    call_command("migrate_statements")

    legacy_problem.refresh_from_db()
    assert storages[PRIVATE_MEDIA_ALIAS].exists(legacy_problem.statement_pdf.name)
    with legacy_problem.statement_pdf.open("rb") as handle:
        assert handle.read() == PDF


def test_command_is_idempotent(legacy_problem):
    """Drugie uruchomienie (nieudany deploy, drugi węzeł, cron) nie tworzy duplikatu."""
    call_command("migrate_statements")
    legacy_problem.refresh_from_db()
    first_name = legacy_problem.statement_pdf.name

    call_command("migrate_statements")

    legacy_problem.refresh_from_db()
    assert legacy_problem.statement_pdf.name == first_name
    # Storage produkcyjny ma ``file_overwrite=False``: kopia zostawiłaby plik z sufiksem obok.
    directory, files = storages[PRIVATE_MEDIA_ALIAS].listdir("problems/statements")
    assert files == ["stara-tresc.pdf"]
    assert directory == []


def test_dry_run_changes_nothing(legacy_problem):
    call_command("migrate_statements", "--dry-run")

    assert not storages[PRIVATE_MEDIA_ALIAS].exists(LEGACY_NAME)


def test_record_without_a_file_anywhere_is_reported_not_crashed(capsys):
    problem = Problem.objects.create(stage=StageFactory(), number=2, title="Rekord bez pliku")
    Problem.objects.filter(pk=problem.pk).update(statement_pdf="problems/statements/nie-ma.pdf")

    call_command("migrate_statements")

    assert "BRAK PLIKU" in capsys.readouterr().err
