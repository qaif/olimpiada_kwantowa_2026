"""Migracja danych ``cms.0007``: ``DocumentPage.attachment`` → lista ``DocumentPageAttachment``.

Migracje danych są kodem, który wykonuje się raz i którego nikt potem nie ogląda – a ta
decyduje o tym, czy po wdrożeniu link do regulaminu w wersji .docx nadal jest na stronie.
Test cofa bazę do stanu sprzed migracji, tworzy tam dokument z załącznikiem i sprawdza, co
zastanie po ponownym „migrate” do przodu.

Przewijanie migracji to DDL po DML; PostgreSQL odmawia wtedy ``ALTER TABLE`` w tej samej transakcji
(„pending trigger events”) – do 25.09.2026 był to powód, dla którego test był transakcyjny. Dziś
bazę przewija fikstura modułu w transakcji z więzami przełączonymi na natychmiastowe, a na końcu
modułu wycofuje ją razem z przewinięciem (``apps/core/tests/migration_helpers.py``). Wiersze test
buduje sam, od zera – nie zakłada niczego o drzewie stron zbudowanym przez migracje.
"""

import uuid

import pytest

from apps.core.tests.migration_helpers import MIGRATION_TESTS, migrate_to, rewound_database

pytestmark = MIGRATION_TESTS

BEFORE = ("cms", "0006_site_settings_defaults")
AFTER = ("cms", "0007_page_attachments")

#: Ścieżki treebearda dla wierszy tworzonych przez test – z końca alfabetu, żeby nie kolidowały
#: z drzewem zbudowanym przez migracje, jeśli akurat w bazie jest.
TEST_PATH = "ZZZZ"


@pytest.fixture(scope="module")
def rewound_apps(django_db_setup, django_db_blocker):
    with rewound_database(django_db_blocker, BEFORE) as db:
        yield db.apps


def test_migration_moves_the_single_attachment_into_the_inline_list(rewound_apps):
    Collection = rewound_apps.get_model("wagtailcore", "Collection")
    ContentType = rewound_apps.get_model("contenttypes", "ContentType")
    Document = rewound_apps.get_model("wagtaildocs", "Document")
    DocumentPage = rewound_apps.get_model("cms", "DocumentPage")
    Locale = rewound_apps.get_model("wagtailcore", "Locale")

    collection = Collection.objects.create(path=TEST_PATH, depth=1, numchild=0, name="Testowa")
    locale, _ = Locale.objects.get_or_create(language_code="pl")
    document = Document.objects.create(
        title="Regulamin (DOCX)", collection=collection, file="documents/regulamin.docx"
    )
    page = DocumentPage.objects.create(
        path=TEST_PATH,
        depth=1,
        numchild=0,
        title="Regulamin",
        draft_title="Regulamin",
        slug="regulamin-migracja",
        url_path="/regulamin-migracja/",
        content_type=ContentType.objects.get_for_model(DocumentPage),
        locale=locale,
        translation_key=uuid.uuid4(),
        attachment=document,
    )

    new_apps = migrate_to(AFTER)
    Attachment = new_apps.get_model("cms", "DocumentPageAttachment")
    rows = list(Attachment.objects.filter(page_id=page.pk))

    assert len(rows) == 1
    assert rows[0].document_id == document.pk
    assert rows[0].sort_order == 0
    # Etykieta wynika z rozszerzenia pliku: .docx to wersja źródłowa, nie plik do druku.
    assert rows[0].label == "Wersja źródłowa (DOCX)"
