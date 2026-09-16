"""Migracja danych: grupa RBAC ``supervisor`` (opiekun szkolny).

Osobna migracja, a nie dopisanie nazwy do ``0002_rbac_groups``: tamta jest już zastosowana na
produkcji, więc zmiana jej treści nie utworzyłaby grupy nigdzie poza świeżą bazą.
"""

from django.db import migrations

GROUP = "supervisor"


def create_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name=GROUP)


def delete_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=GROUP).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0013_school_supervisors_and_quality"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [migrations.RunPython(create_group, delete_group)]
