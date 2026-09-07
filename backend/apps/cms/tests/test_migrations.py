"""Migracja danych ``cms.0007``: ``DocumentPage.attachment`` → lista ``DocumentPageAttachment``.

Migracje danych są kodem, który wykonuje się raz i którego nikt potem nie ogląda – a ta
decyduje o tym, czy po wdrożeniu link do regulaminu w wersji .docx nadal jest na stronie.
Test cofa bazę do stanu sprzed migracji, tworzy tam dokument z załącznikiem i sprawdza, co
zastanie po ponownym „migrate” do przodu.

Dwie rzeczy, przez które ten test wygląda inaczej niż reszta pakietu:

- **``transaction=True``.** Przewijanie migracji to DDL po DML; PostgreSQL odmawia wtedy
  ``ALTER TABLE`` w tej samej transakcji („pending trigger events”), więc test musi commitować,
- **własne dane od zera.** Testy transakcyjne czyszczą bazę po sobie, więc drzewo stron
  i kolekcja Root utworzone przez migracje mogą już nie istnieć w chwili, gdy ten test rusza.
  Nie zakładamy więc niczego o zawartości bazy i budujemy komplet wierszy sami.

Fixture przywraca czoło migracji także wtedy, gdy test przerwie się w połowie – inaczej cały
dalszy przebieg pakietu zastałby bazę bez tabel z ``0007``.
"""

import uuid

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = ("cms", "0006_site_settings_defaults")
AFTER = ("cms", "0007_page_attachments")

#: Ścieżki treebearda dla wierszy tworzonych przez test – z końca alfabetu, żeby nie kolidowały
#: z drzewem zbudowanym przez migracje, jeśli akurat w bazie jest.
TEST_PATH = "ZZZZ"


def migrate_to(target):
    """Przewija bazę do wskazanej migracji i zwraca stan aplikacji z tamtego momentu."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target])
    executor.loader.build_graph()
    return executor.loader.project_state([target]).apps


@pytest.fixture
def rewound_apps(transactional_db):  # noqa: ARG001 - fixture bazy, używana przez efekt uboczny
    yield migrate_to(BEFORE)
    migrate_to(AFTER)


@pytest.mark.django_db(transaction=True)
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
