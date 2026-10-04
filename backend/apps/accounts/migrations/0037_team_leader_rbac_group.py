"""Migracja danych: grupa RBAC ``team_leader`` (opiekun drużyny narodowej, DEL-01).

Osobna migracja z tego samego powodu, co ``0014_supervisor_rbac_group``: ``0002_rbac_groups``
jest zastosowana na produkcji, a role konkursu i grupy RBAC muszą opisywać ten sam zbiór
(asercja w ``apps.accounts.models``).
"""

from django.db import migrations

GROUP = "team_leader"


def create_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name=GROUP)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0036_delegations"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    # Cofnięcie zostawia grupę: pusta grupa nikomu niczego nie nadaje, a kasowanie jej kaskadą przez
    # historyczny model ``User.groups`` przy przewijaniu migracji w testach wywraca się na rozjeździe
    # klas ``Group`` w stanie migracji. Ponowne wykonanie i tak robi ``get_or_create``.
    operations = [migrations.RunPython(create_group, migrations.RunPython.noop)]
