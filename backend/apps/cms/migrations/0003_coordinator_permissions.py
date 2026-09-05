"""Uprawnienia Wagtaila dla grupy ``coordinator`` (T-09).

Zamiast wyliczać kodowe nazwy uprawnień (te zmieniały się między wersjami Wagtaila – ``permission``
zamiast ``permission_type`` w ``GroupPagePermission``), **kopiujemy** komplet uprawnień z
wbudowanych grup ``Editors`` i ``Moderators``, które Wagtail zakłada we własnych migracjach
danych. Sumarycznie daje to redaktora **i** moderatora: dodawanie, edycję, publikowanie
i blokowanie stron na całym drzewie oraz zarządzanie obrazami i dokumentami w kolekcjach.

Kopiowane są trzy rzeczy:

1. ``Group.permissions`` (m2m), w tym ``wagtailadmin.access_admin`` – bez niego ``/cms/`` odpowiada
   przekierowaniem/403 nawet dla użytkownika z prawami do stron,
2. ``GroupPagePermission`` – prawa do poddrzewa stron,
3. ``GroupCollectionPermission`` – prawa do kolekcji mediów.

Recenzenci, uczestnicy i komisja odwoławcza nie dostają nic: dla nich ``/cms/`` pozostaje zamknięte.
Migracja jest idempotentna (``get_or_create`` / ``add``) i nie tworzy użytkowników.
"""

from django.db import migrations

#: Grupy wzorcowe zakładane przez ``wagtailcore.0002_initial_data``.
SOURCE_GROUPS = ("Editors", "Moderators")
#: Grupa RBAC platformy – ta sama stała, co ``apps.accounts.models.GROUP_COORDINATOR``.
TARGET_GROUP = "coordinator"


def grant(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    GroupPagePermission = apps.get_model("wagtailcore", "GroupPagePermission")
    GroupCollectionPermission = apps.get_model("wagtailcore", "GroupCollectionPermission")

    target, _ = Group.objects.get_or_create(name=TARGET_GROUP)
    sources = list(Group.objects.filter(name__in=SOURCE_GROUPS))
    if not sources:  # pragma: no cover - wagtailcore.0002_initial_data zawsze je tworzy
        return

    for source in sources:
        target.permissions.add(*source.permissions.all())
        for page_permission in GroupPagePermission.objects.filter(group=source):
            GroupPagePermission.objects.get_or_create(
                group=target,
                page_id=page_permission.page_id,
                permission_id=page_permission.permission_id,
            )
        for collection_permission in GroupCollectionPermission.objects.filter(group=source):
            GroupCollectionPermission.objects.get_or_create(
                group=target,
                collection_id=collection_permission.collection_id,
                permission_id=collection_permission.permission_id,
            )


def revoke(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    GroupPagePermission = apps.get_model("wagtailcore", "GroupPagePermission")
    GroupCollectionPermission = apps.get_model("wagtailcore", "GroupCollectionPermission")

    target = Group.objects.filter(name=TARGET_GROUP).first()
    if target is None:
        return
    GroupPagePermission.objects.filter(group=target).delete()
    GroupCollectionPermission.objects.filter(group=target).delete()
    for source in Group.objects.filter(name__in=SOURCE_GROUPS):
        target.permissions.remove(*source.permissions.all())


class Migration(migrations.Migration):
    dependencies = [
        ("cms", "0002_initial_tree"),
        ("accounts", "0002_rbac_groups"),
        # Grupy wzorcowe i ich uprawnienia powstają w tych migracjach danych Wagtaila.
        ("wagtailcore", "0094_alter_page_locale"),
        ("wagtailadmin", "0005_editingsession_is_editing"),
        ("wagtailimages", "0027_image_description"),
        ("wagtaildocs", "0014_alter_document_file_size"),
    ]

    operations = [migrations.RunPython(grant, revoke)]
