"""Migracja ``cms.0032``: dokumenty ``wagtaildocs.Document`` → ``cms.Document`` (audyt 10.10.2026, W2).

Podmiana modelu dokumentu na instalacji z danymi to miejsce, w którym łatwo zgubić coś po cichu:
identyfikatory (na nie wskazują treści stron i rozesłane linki), datę wgrania (``auto_now_add``),
tagi (generyczne, po typie treści) i uprawnienia grup na kolekcjach. Test cofa bazę do stanu
sprzed kopii, buduje tam dokument „produkcyjny” i sprawdza, co zastanie po migracji – i po jej
cofnięciu.
"""

import datetime

import pytest

from apps.core.tests.migration_helpers import MIGRATION_TESTS, migrate_to, rewound_database

pytestmark = MIGRATION_TESTS

BEFORE = ("cms", "0031_private_document_model")
AFTER = ("cms", "0034_document_rollback_copy")

UPLOADED_AT = datetime.datetime(2025, 3, 1, 12, 0, tzinfo=datetime.UTC)


@pytest.fixture(scope="module")
def rewound_apps(django_db_setup, django_db_blocker):
    with rewound_database(django_db_blocker, BEFORE) as db:
        yield db.apps


def _legacy_world(apps):
    Collection = apps.get_model("wagtailcore", "Collection")
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    GroupCollectionPermission = apps.get_model("wagtailcore", "GroupCollectionPermission")
    Tag = apps.get_model("taggit", "Tag")
    TaggedItem = apps.get_model("taggit", "TaggedItem")
    OldDocument = apps.get_model("wagtaildocs", "Document")

    root = Collection.objects.get(depth=1)
    document = OldDocument.objects.create(
        id=4242, title="Regulamin", collection=root, file="documents/regulamin.pdf", file_hash="abc"
    )
    OldDocument.objects.filter(pk=document.pk).update(created_at=UPLOADED_AT)
    old_ct = ContentType.objects.get(app_label="wagtaildocs", model="document")
    tag = Tag.objects.create(name="regulaminy", slug="regulaminy")
    TaggedItem.objects.create(tag=tag, content_type=old_ct, object_id=document.pk)
    group = Group.objects.create(name="redakcja-testowa")
    choose = Permission.objects.get(content_type=old_ct, codename="choose_document")
    group.permissions.add(choose)
    GroupCollectionPermission.objects.create(group=group, collection=root, permission=choose)
    return document, group, tag


def test_documents_are_copied_with_their_keys_dates_tags_and_permissions(rewound_apps):
    document, group, tag = _legacy_world(rewound_apps)

    apps = migrate_to(AFTER)
    NewDocument = apps.get_model("cms", "Document")
    ContentType = apps.get_model("contenttypes", "ContentType")
    TaggedItem = apps.get_model("taggit", "TaggedItem")
    GroupCollectionPermission = apps.get_model("wagtailcore", "GroupCollectionPermission")
    Group = apps.get_model("auth", "Group")

    copied = NewDocument.objects.get(pk=document.pk)
    assert copied.title == "Regulamin"
    assert copied.file.name == "documents/regulamin.pdf"
    assert copied.created_at == UPLOADED_AT
    assert copied.file_hash == "abc"

    new_ct = ContentType.objects.get(app_label="cms", model="document")
    assert TaggedItem.objects.filter(tag_id=tag.pk, content_type=new_ct, object_id=document.pk).exists()

    group = Group.objects.get(pk=group.pk)
    assert group.permissions.filter(content_type=new_ct, codename="choose_document").exists()
    assert GroupCollectionPermission.objects.filter(
        group_id=group.pk, permission__content_type=new_ct, permission__codename="choose_document"
    ).exists()

    # Nowy dokument dostaje klucz za skopiowanymi – licznik nie startuje od 1.
    fresh = NewDocument.objects.create(title="Nowy", collection_id=copied.collection_id, file="x/nowy.pdf")
    assert fresh.pk > document.pk


def test_rolling_back_returns_new_documents_to_wagtail(rewound_apps):
    document, _group, _tag = _legacy_world(rewound_apps)
    apps = migrate_to(AFTER)
    NewDocument = apps.get_model("cms", "Document")
    added = NewDocument.objects.create(
        title="Wgrany po migracji", collection_id=document.collection_id, file="0123/nowy.pdf"
    )

    apps = migrate_to(BEFORE)
    OldDocument = apps.get_model("wagtaildocs", "Document")
    ContentType = apps.get_model("contenttypes", "ContentType")
    TaggedItem = apps.get_model("taggit", "TaggedItem")

    assert OldDocument.objects.filter(pk=added.pk, file="0123/nowy.pdf").exists()
    assert OldDocument.objects.filter(pk=document.pk).exists()
    old_ct = ContentType.objects.get(app_label="wagtaildocs", model="document")
    assert TaggedItem.objects.filter(content_type=old_ct, object_id=document.pk).exists()
