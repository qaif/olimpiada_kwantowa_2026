"""Nowelizacja regulaminu z 20 września 2026 r. – wersja w definicjach zgody (§ 1.1.2).

``apps.accounts.consents.TERMS_VERSION`` zmienia się w tym samym commicie, a wiersze definicji
mają być równe stałej (``test_consent_definitions_match_the_constant``). Świeża baza dostaje nową
wersję z migracji 0024 (czyta stałą na żywo); ta migracja dogania bazy już stojące.

Warunek na **starą** wersję jest celowy: definicja, której wersję organizator zmienił w panelu,
należy do niego i zostaje nietknięta. ``ConsentRecord`` nie jest ruszany – dowód mówi, na co ktoś
zgodził się wtedy, więc dotychczasowe wpisy dalej wskazują wersję 1.0 z 2 września.
"""

from django.db import migrations

OLD = "1.0 z 2 września 2026"
NEW = "z 20 września 2026"


def _swap(apps, before: str, after: str) -> None:
    apps.get_model("accounts", "ConsentDefinition").objects.filter(kind="TERMS", version=before).update(
        version=after
    )


def forwards(apps, schema_editor):  # noqa: ARG001 - podpis RunPython
    _swap(apps, OLD, NEW)


def backwards(apps, schema_editor):  # noqa: ARG001 - podpis RunPython
    _swap(apps, NEW, OLD)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0029_custom_institution")]

    operations = [migrations.RunPython(forwards, backwards)]
