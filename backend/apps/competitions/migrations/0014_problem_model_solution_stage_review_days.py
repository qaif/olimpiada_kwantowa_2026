"""Wzorcówka i uwagi dla recenzentów przy zadaniu oraz dni na recenzję przy etapie.

``review_deadline_days`` dostaje domyślne 14 także w danych istniejących: etapy sprzed tej
migracji nie miały terminu osobistego recenzji, a dwa tygodnie to okno, którym organizator i tak
posługiwał się nieformalnie.
"""

import apps.competitions.storage
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("competitions", "0013_problem_scoring_scale"),
    ]

    operations = [
        migrations.AddField(
            model_name="problem",
            name="model_solution_pdf",
            field=models.FileField(
                blank=True,
                storage=apps.competitions.storage.private_media_storage,
                upload_to="problems/model-solutions/",
                verbose_name="rozwiązanie wzorcowe (PDF)",
            ),
        ),
        migrations.AddField(
            model_name="problem",
            name="reviewer_notes",
            field=models.TextField(blank=True, verbose_name="uwagi dla recenzentów"),
        ),
        migrations.AddField(
            model_name="stage",
            name="review_deadline_days",
            field=models.PositiveSmallIntegerField(default=14, verbose_name="dni na recenzję"),
        ),
        migrations.AddConstraint(
            model_name="stage",
            constraint=models.CheckConstraint(
                condition=models.Q(("review_deadline_days__gte", 1)),
                name="competitions_stage_review_days_positive",
            ),
        ),
    ]
