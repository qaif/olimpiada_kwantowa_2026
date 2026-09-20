"""``manage.py replace_page_text`` – poprawka jednego zdania na stronie prowadzonej w /cms/.

Przypadek wzorcowy to uwaga organizatora z 20.09.2026: zakres odpowiedzialności Komitetu
Merytorycznego dostaje dopisek „rozpatrywanie odwołań”, a strona „Skład komitetów” ma na
produkcji listy osób dopisane w /cms/, więc nie może przejść przez seed.
"""

import pytest
from django.core.management import CommandError, call_command
from wagtail.models import Revision

from apps.cms.models import DocumentPage

pytestmark = pytest.mark.django_db

OLD = "Rejestracja, komunikacja, obsługa systemu"
NEW = "Rejestracja, komunikacja, obsługa systemu, pomoc techniczna"


def run(*extra: str) -> None:
    call_command("replace_page_text", "--slug", "komitety", "--old", OLD, "--new", NEW, *extra, verbosity=0)


def body_of(page: DocumentPage) -> str:
    return str(DocumentPage.objects.get(pk=page.pk).body)


@pytest.fixture
def komitety() -> DocumentPage:
    call_command("seed_legacy_content", verbosity=0)
    return DocumentPage.objects.get(slug="komitety")


def test_report_mode_changes_nothing(komitety):
    before = body_of(komitety)

    run()

    assert body_of(komitety) == before


def test_apply_replaces_the_text_and_publishes_a_revision(web_client, komitety):
    revisions = Revision.objects.filter(object_id=str(komitety.pk)).count()
    blocks = len(komitety.body)

    run("--apply")

    page = DocumentPage.objects.get(pk=komitety.pk)
    assert NEW in body_of(komitety)
    assert len(page.body) == blocks
    assert page.has_unpublished_changes is False
    assert Revision.objects.filter(object_id=str(komitety.pk)).count() == revisions + 1
    # Edytor otwiera ostatnią rewizję – to w niej musi stać nowe brzmienie.
    assert NEW in str(page.get_latest_revision_as_object().body)
    assert NEW in web_client.get("/dokumenty/komitety/").content.decode()


def test_second_run_does_not_double_the_suffix(komitety):
    run("--apply")
    run("--apply")

    assert body_of(komitety).count("pomoc techniczna") == 1


def test_page_with_a_draft_is_refused(komitety):
    komitety.save_revision()  # szkic bez publikacji

    with pytest.raises(CommandError):
        run("--apply")
