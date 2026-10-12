"""Kopia dokumentów ``wagtaildocs.Document`` → ``cms.Document`` (audyt 10.10.2026, W2; plan w ``0031``).

Co i dlaczego tak:

- **klucze główne zostają.** Na identyfikator dokumentu wskazują ``<a linktype="document" id=N>``
  w treściach stron, bloki ``DocumentChooserBlock`` w StreamFieldach i adresy ``/documents/<id>/<nazwa>``
  rozesłane w listach i zakładkach. Kopia z nowymi kluczami wymagałaby przepisania każdej treści,
- **kopia SQL-em (``INSERT … SELECT``), a nie ORM-em**: ``created_at`` ma ``auto_now_add`` i ORM
  nadpisałby datę wgrania dniem migracji, a ta data jest w ``/cms/`` jedyną informacją, kiedy plik
  trafił do biblioteki,
- **wszystko, co trzyma typ treści dokumentu, przechodzi na nowy typ**: tagi (``taggit``), indeks
  wyszukiwarki, indeks odwołań (zakładka „Użycie” w ``/cms/``) i dziennik zmian. Bez tego tagi
  zniknęłyby z dokumentów, a „Użycie” pokazywałoby zero odwołań przy plikach wpiętych w strony,
- **uprawnienia są dopisywane, nie przenoszone.** Każda grupa i każde konto z uprawnieniem
  ``wagtaildocs.*_document`` (modelowym albo na kolekcji – ``GroupCollectionPermission``) dostaje
  odpowiednik ``cms.*_document``. Stare zostają: nic ich już nie sprawdza, a cofnięcie migracji
  nie musi ich odtwarzać,
- **wiersze ``wagtaildocs.Document`` zostają** jako zamrożona kopia. Kod poprzedniego wydania
  (wycofanie wdrożenia bez cofania migracji) czyta je dalej, a nowy kod ich nie widzi.

Pliki nadal leżą tam, gdzie leżały (``documents/<nazwa>`` w ``public-media``) – przenosi je polecenie
``migrate_documents_to_private``, które trzeba uruchomić zaraz po tej migracji.
"""

from django.conf import settings
from django.core.management.color import no_style
from django.db import migrations

#: Uprawnienia modelowe dokumentu – domyślne Django plus ``choose`` Wagtaila.
CODENAMES = ("add_document", "change_document", "delete_document", "view_document", "choose_document")

#: (aplikacja, model, pola typu treści) – miejsca, w których Wagtail i taggit pamiętają typ dokumentu.
CONTENT_TYPE_REFERENCES = (
    ("taggit", "TaggedItem", ("content_type",)),
    ("wagtailsearch", "IndexEntry", ("content_type",)),
    ("wagtailcore", "ReferenceIndex", ("content_type", "base_content_type", "to_content_type")),
    ("wagtailcore", "ModelLogEntry", ("content_type",)),
)

#: Kolumny wspólne obu tabel – kopiowane 1:1.
COLUMNS = (
    "id",
    "title",
    "file",
    "created_at",
    "uploaded_by_user_id",
    "collection_id",
    "file_size",
    "file_hash",
)


def _content_types(apps):
    ContentType = apps.get_model("contenttypes", "ContentType")
    old, _ = ContentType.objects.get_or_create(app_label="wagtaildocs", model="document")
    new, _ = ContentType.objects.get_or_create(app_label="cms", model="document")
    return old, new


def _remap_content_type(apps, source, target):
    for app_label, model_name, fields in CONTENT_TYPE_REFERENCES:
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:  # pragma: no cover - aplikacja wyłączona w tej instalacji
            continue
        for field in fields:
            model.objects.filter(**{field: source}).update(**{field: target})


def _copy_rows(schema_editor, source_table, target_table, *, update_existing):
    quote = schema_editor.quote_name
    columns = ", ".join(quote(column) for column in COLUMNS)
    if update_existing:
        assignments = ", ".join(f"{quote(c)} = EXCLUDED.{quote(c)}" for c in COLUMNS if c != "id")
        conflict = f"ON CONFLICT ({quote('id')}) DO UPDATE SET {assignments}"
    else:
        conflict = f"ON CONFLICT ({quote('id')}) DO NOTHING"
    # Nazwy tabel i kolumn pochodzą z ``_meta`` i stałej ``COLUMNS`` (cytowane ``quote_name``),
    # a nie od użytkownika – w tym SQL-u nie ma żadnej wartości z zewnątrz.
    schema_editor.execute(
        f"INSERT INTO {quote(target_table)} ({columns}) "  # noqa: S608 - patrz komentarz wyżej
        f"SELECT {columns} FROM {quote(source_table)} {conflict}"
    )


def _reset_sequence(schema_editor, model):
    for statement in schema_editor.connection.ops.sequence_reset_sql(no_style(), [model]):
        schema_editor.execute(statement)


def _grant_new_permissions(apps, old_ct, new_ct):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model(settings.AUTH_USER_MODEL)
    GroupCollectionPermission = apps.get_model("wagtailcore", "GroupCollectionPermission")
    GroupPermission = Group.permissions.through
    UserPermission = User.user_permissions.through

    for old in Permission.objects.filter(content_type=old_ct, codename__in=CODENAMES):
        new, _ = Permission.objects.get_or_create(
            content_type=new_ct, codename=old.codename, defaults={"name": old.name}
        )
        GroupPermission.objects.bulk_create(
            [
                GroupPermission(group_id=group_id, permission_id=new.pk)
                for group_id in GroupPermission.objects.filter(permission_id=old.pk).values_list(
                    "group_id", flat=True
                )
            ],
            ignore_conflicts=True,
        )
        UserPermission.objects.bulk_create(
            [
                UserPermission(user_id=user_id, permission_id=new.pk)
                for user_id in UserPermission.objects.filter(permission_id=old.pk).values_list(
                    "user_id", flat=True
                )
            ],
            ignore_conflicts=True,
        )
        for group_id, collection_id in GroupCollectionPermission.objects.filter(permission=old).values_list(
            "group_id", "collection_id"
        ):
            GroupCollectionPermission.objects.get_or_create(
                group_id=group_id, collection_id=collection_id, permission=new
            )


def copy_documents(apps, schema_editor):
    OldDocument = apps.get_model("wagtaildocs", "Document")
    NewDocument = apps.get_model("cms", "Document")
    _copy_rows(schema_editor, OldDocument._meta.db_table, NewDocument._meta.db_table, update_existing=False)
    # Licznik kluczy za najwyższym skopiowanym – inaczej pierwszy nowy dokument dostałby id=1.
    _reset_sequence(schema_editor, NewDocument)
    old_ct, new_ct = _content_types(apps)
    _remap_content_type(apps, old_ct, new_ct)
    _grant_new_permissions(apps, old_ct, new_ct)


def uncopy_documents(apps, schema_editor):
    """Wstecz: typ treści wraca na ``wagtaildocs``, wiersze ``cms.Document`` znikają.

    Wiersze dopisane po migracji ``0034`` (wstecz) już oddało do ``wagtaildocs.Document``.
    Uprawnienia ``cms.*_document`` zostają – usuwa je razem z typem treści ``remove_stale_contenttypes``.
    """
    NewDocument = apps.get_model("cms", "Document")
    old_ct, new_ct = _content_types(apps)
    _remap_content_type(apps, new_ct, old_ct)
    schema_editor.execute(f"DELETE FROM {schema_editor.quote_name(NewDocument._meta.db_table)}")  # noqa: S608 - nazwa z _meta


class Migration(migrations.Migration):
    dependencies = [
        ("cms", "0031_private_document_model"),
        ("wagtaildocs", "0014_alter_document_file_size"),
        ("wagtailsearch", "0010_add_text_fields"),
        ("wagtailcore", "0098_apitoken"),
        ("taggit", "0006_rename_taggeditem_content_type_object_id_taggit_tagg_content_8fc721_idx"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("auth", "0012_alter_user_first_name_max_length"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [migrations.RunPython(copy_documents, uncopy_documents)]
