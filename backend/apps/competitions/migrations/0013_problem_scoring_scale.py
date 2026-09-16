"""Nadpisanie skali punktacji w pojedynczym zadaniu (prośba organizatora).

Oba pola są ``null``, bo puste znaczy „dziedzicz skalę etapu”. Dzięki temu migracja niczego nie
przelicza i nie kopiuje: istniejące zadania nadal punktuje skala ich etapu, a nadpisanie jest
świadomą decyzją koordynatora podjętą później.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("competitions", "0012_edition_event"),
    ]

    operations = [
        migrations.AddField(
            model_name="problem",
            name="scoring_values",
            field=models.JSONField(blank=True, null=True, verbose_name="skala punktacji zadania"),
        ),
        migrations.AddField(
            model_name="problem",
            name="max_points",
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="maksimum punktów"),
        ),
    ]
