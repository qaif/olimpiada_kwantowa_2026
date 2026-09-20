"""Linia podpisu dokumentów: „Komitet Główny” → „Komitet Sterujący” (decyzja organizatora, 20.09.2026).

``apps.results.certificates.SIGNATURE_LINE`` zmienia się w tym samym commicie, a wiersze
szablonów mają być równe stałej (``apps/tenancy/tests/test_documents.py``). Świeża baza dostaje
nowe brzmienie z migracji 0005 (czyta stałą na żywo); ta migracja dogania bazy już stojące.

Podmiana dotyczy **samej nazwy komitetu** i tylko wierszy, w których ona stoi: szablon
zredagowany w panelu (inne brzmienie podpisu) należy do koordynatora i zostaje nietknięty,
a konkurs założony z szablonu ma w tej linii własną nazwę w dopełniaczu – też zostaje, zmienia
się w niej wyłącznie komitet. Cofnięcie przywraca poprzednią nazwę tą samą regułą.
"""

from django.db import migrations

OLD = "Komitetu Głównego"
NEW = "Komitetu Sterującego"


def _swap(apps, before: str, after: str) -> None:
    DocumentTemplate = apps.get_model("tenancy", "DocumentTemplate")
    for row in DocumentTemplate.objects.filter(signature_line__contains=before).only("pk", "signature_line"):
        DocumentTemplate.objects.filter(pk=row.pk).update(
            signature_line=row.signature_line.replace(before, after)
        )


def forwards(apps, schema_editor):  # noqa: ARG001 - podpis RunPython
    _swap(apps, OLD, NEW)


def backwards(apps, schema_editor):  # noqa: ARG001 - podpis RunPython
    _swap(apps, NEW, OLD)


class Migration(migrations.Migration):
    dependencies = [("tenancy", "0006_fees")]

    operations = [migrations.RunPython(forwards, backwards)]
