"""Rubryka oceniania, notatki recenzentów przy pracy i terminy pojedynczych recenzji.

Jedna migracja na trzy funkcje panelu recenzenta, bo wchodzą razem i razem są testowane;
rozbicie na trzy dałoby trzy kroki wdrożenia bez ani jednego momentu, w którym system
działałby inaczej.
"""

import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone

import apps.grading.models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("competitions", "0014_problem_model_solution_stage_review_days"),
        ("grading", "0006_review_cancel_reason"),
        ("submissions", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="review",
            name="rubric",
            field=models.JSONField(
                blank=True,
                default=apps.grading.models.default_rubric,
                verbose_name="rubryka",
            ),
        ),
        migrations.AddField(
            model_name="review",
            name="due_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="termin recenzji"),
        ),
        migrations.AddField(
            model_name="review",
            name="reminded_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="przypomnienie wysłane"),
        ),
        migrations.CreateModel(
            name="RubricCriterion",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("order", models.PositiveSmallIntegerField(default=1, verbose_name="kolejność")),
                ("title", models.CharField(max_length=200, verbose_name="kryterium")),
                ("description", models.TextField(blank=True, verbose_name="opis")),
                ("max_points", models.PositiveSmallIntegerField(verbose_name="maksimum punktów")),
                ("created_at", models.DateTimeField(default=timezone.now, verbose_name="utworzone")),
                (
                    "problem",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="rubric_criteria",
                        to="competitions.problem",
                    ),
                ),
            ],
            options={
                "verbose_name": "kryterium rubryki",
                "verbose_name_plural": "kryteria rubryki",
                "ordering": ("problem", "order", "id"),
            },
        ),
        migrations.AddConstraint(
            model_name="rubriccriterion",
            constraint=models.CheckConstraint(
                condition=models.Q(("max_points__gte", 1)),
                name="grading_rubric_criterion_max_points_positive",
            ),
        ),
        migrations.CreateModel(
            name="ReviewNote",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("text", models.CharField(max_length=1000, verbose_name="treść")),
                ("created_at", models.DateTimeField(default=timezone.now, verbose_name="dodana")),
                (
                    "author",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="review_notes",
                        to="accounts.committeemember",
                    ),
                ),
                (
                    "submission",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="review_notes",
                        to="submissions.submission",
                    ),
                ),
            ],
            options={
                "verbose_name": "notatka recenzencka",
                "verbose_name_plural": "notatki recenzenckie",
                "ordering": ("submission", "created_at", "id"),
            },
        ),
    ]
