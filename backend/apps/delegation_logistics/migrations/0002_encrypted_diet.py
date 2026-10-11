"""Dieta szyfrowana jak reszta danych o zdrowiu (poprawka po przeglądzie LOG-01, M2).

Kolumna zmienia typ z ``varchar(16)`` na ``text``; istniejące wartości są szyfrowane w tej samej
migracji. Odczyt i tak przyjąłby jawną wartość (``crypto.decrypt`` oddaje napis bez przedrostka bez
zmian), ale dieta religijna albo medyczna nie ma leżeć jawnie w bazie do najbliższej poprawki wiersza.
"""

from django.db import migrations

import apps.delegation_logistics.crypto


def encrypt_existing(apps, schema_editor):
    from apps.delegation_logistics.crypto import PREFIX, encrypt

    Member = apps.get_model("delegation_logistics", "DelegationMember")
    table = Member._meta.db_table
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"SELECT id, diet FROM {table} WHERE diet <> ''")  # noqa: S608 - nazwa tabeli z modelu
        rows = [(pk, value) for pk, value in cursor.fetchall() if value and not value.startswith(PREFIX)]
        for pk, value in rows:
            cursor.execute(f"UPDATE {table} SET diet = %s WHERE id = %s", [encrypt(value), pk])  # noqa: S608


class Migration(migrations.Migration):
    dependencies = [
        ("delegation_logistics", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="delegationmember",
            name="diet",
            field=apps.delegation_logistics.crypto.EncryptedTextField(
                blank=True,
                choices=[
                    ("NONE", "Bez ograniczeń"),
                    ("VEGETARIAN", "Wegetariańska"),
                    ("VEGAN", "Wegańska"),
                    ("HALAL", "Halal"),
                    ("KOSHER", "Koszerna"),
                    ("GLUTEN_FREE", "Bezglutenowa"),
                    ("OTHER", "Inna (opis w uwagach)"),
                ],
                verbose_name="dieta",
            ),
        ),
        migrations.RunPython(encrypt_existing, migrations.RunPython.noop),
    ]
