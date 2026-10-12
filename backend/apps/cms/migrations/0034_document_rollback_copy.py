"""Wyłącznie wstecz: dokumenty ``cms.Document`` wracają do ``wagtaildocs.Document`` (plan w ``0031``).

Do przodu nie robi nic. Istnieje dla kolejności cofania: ``0033`` wstecz przepina klucze obce
załączników z powrotem na ``wagtaildocs_document``, więc każdy dokument wpięty w stronę musi tam
już być – także ten wgrany po migracji i ten, któremu ``migrate_documents_to_private`` zmieniło
klucz pliku (stąd ``DO UPDATE``, a nie samo „wstaw brakujące”).

Plik po cofnięciu leży nadal w prywatnym storage pod nowym kluczem, a ``wagtaildocs.Document``
szuka go w ``default`` – cofnięcie bez ponownego przeniesienia plików do publicznego bucketu
zostawia dokumenty bez treści – pliki trzeba wtedy skopiować z powrotem ręcznie (``mc cp``).
"""

import importlib

from django.db import migrations

_copy = importlib.import_module("apps.cms.migrations.0032_copy_wagtail_documents")


def copy_back(apps, schema_editor):
    OldDocument = apps.get_model("wagtaildocs", "Document")
    NewDocument = apps.get_model("cms", "Document")
    _copy._copy_rows(
        schema_editor, NewDocument._meta.db_table, OldDocument._meta.db_table, update_existing=True
    )
    _copy._reset_sequence(schema_editor, OldDocument)


class Migration(migrations.Migration):
    dependencies = [
        ("cms", "0033_document_foreign_keys"),
    ]

    operations = [migrations.RunPython(migrations.RunPython.noop, copy_back)]
