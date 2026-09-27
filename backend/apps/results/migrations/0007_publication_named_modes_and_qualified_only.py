"""Nazwiska po każdym etapie zawodów i lista samych awansujących.

Migracja jest **wyłącznie schematem**. Opublikowane tabele są zamrożone i zostają co do bajtu:
``FULL`` zachowuje znaczenie (nazwisko tylko przy wierszu ``qualified``), zmienia się jedynie
jego etykieta, a nowe pole ``qualified_only`` dostaje ``False`` – każda dotychczasowa publikacja
była pełną tabelą.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("results", "0006_certificate_template_version"),
    ]

    operations = [
        migrations.AlterField(
            model_name="resultspublication",
            name="anonymization",
            field=models.CharField(
                choices=[
                    ("CODE", "kod uczestnika"),
                    ("INITIALS_SCHOOL", "inicjały i szkoła"),
                    ("FULL", "imię i nazwisko awansujących, za zgodą"),
                    ("FULL_ALL", "imię i nazwisko wszystkich, za zgodą"),
                ],
                default="CODE",
                max_length=24,
                verbose_name="anonimizacja",
            ),
        ),
        migrations.AddField(
            model_name="resultspublication",
            name="qualified_only",
            field=models.BooleanField(default=False, verbose_name="tylko awansujący"),
        ),
    ]
