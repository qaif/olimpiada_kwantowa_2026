"""Okres retencji danych osobowych uczestników – pole edycji.

Edycje istniejące dostają wartość domyślną (24 miesiące). Ta wartość niczego sama nie kasuje:
anonimizacja jest zadaniem okresowym (``apps.accounts.retention``) i liczy termin od ostatniego
deadline'u etapu, więc edycja bieżąca i edycje sprzed dwóch lat zachowują się różnie dopiero
wtedy, gdy zadanie faktycznie przejdzie.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("competitions", "0016_interview_video_and_problem_english"),
    ]

    operations = [
        migrations.AddField(
            model_name="edition",
            name="data_retention_months",
            field=models.PositiveSmallIntegerField(
                default=24,
                help_text=(
                    "Po ilu miesiącach od ostatniego deadline'u etapu anonimizować konta "
                    "uczestników tej edycji. Zero wyłącza automatyczną anonimizację."
                ),
                verbose_name="retencja danych (miesiące)",
            ),
        ),
    ]
