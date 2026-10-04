"""Wpis katalogu dla wbudowanego motywu ``classic`` (wygląd aplikacji, bez wersji i plików).

Galeria w panelu koordynatora i audyt mają jeden model dla obu przypadków – „motyw z paczki”
i „wygląd wbudowany”. Wybór ``classic`` w konkursie to ``Competition.theme_version = NULL``.
"""

from django.db import migrations


def create_classic(apps, schema_editor):
    Theme = apps.get_model("themes", "Theme")
    Theme.objects.get_or_create(
        slug="classic",
        defaults={
            "name": "Klasyczny",
            "author": "Platforma Olimpiady",
            "description": "Wbudowany wygląd platformy: granat, papier i czerwień z logotypu, Inter i Source Serif 4.",
            "is_builtin": True,
        },
    )


def remove_classic(apps, schema_editor):
    apps.get_model("themes", "Theme").objects.filter(slug="classic", is_builtin=True).delete()


class Migration(migrations.Migration):
    dependencies = [("themes", "0001_initial")]

    operations = [migrations.RunPython(create_classic, remove_classic)]
