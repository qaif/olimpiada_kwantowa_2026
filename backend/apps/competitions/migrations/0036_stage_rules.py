# ZOZ-01: Zasady Organizacji Zawodów etapu – wersje dokumentu (``apps.competitions.stage_rules``).
# Pisana ręcznie (bez bazy przy tworzeniu). Nowa, pusta tabela – operacja natychmiastowa; treść
# ZOZ trafia do bazy komendą ``import_stage_rules`` albo z panelu koordynatora, nie migracją.

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

import apps.competitions.stage_rules
import apps.competitions.storage


class Migration(migrations.Migration):
    dependencies = [
        ("competitions", "0035_stage_auto_register_new_participants"),
    ]

    operations = [
        migrations.CreateModel(
            name="StageRules",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version", models.CharField(max_length=20, verbose_name="wersja")),
                ("document_date", models.DateField(verbose_name="data dokumentu")),
                (
                    "title",
                    models.CharField(
                        default="Zasady Organizacji Zawodów (ZOZ)", max_length=200, verbose_name="tytuł"
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("DRAFT", "projekt"), ("PUBLISHED", "opublikowany"), ("WITHDRAWN", "wycofany")],
                        default="DRAFT",
                        max_length=16,
                        verbose_name="status",
                    ),
                ),
                ("body_html", models.TextField(verbose_name="treść (HTML)")),
                (
                    "pdf",
                    models.FileField(
                        blank=True,
                        max_length=255,
                        storage=apps.competitions.storage.private_media_storage,
                        upload_to=apps.competitions.stage_rules.rules_upload_to,
                        verbose_name="plik PDF",
                    ),
                ),
                ("published_at", models.DateTimeField(blank=True, null=True, verbose_name="opublikowano")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="utworzono")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="zmieniono")),
                (
                    "stage",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="rules_versions",
                        to="competitions.stage",
                        verbose_name="etap",
                    ),
                ),
            ],
            options={
                "verbose_name": "Zasady Organizacji Zawodów (wersja)",
                "verbose_name_plural": "Zasady Organizacji Zawodów",
                "ordering": ("stage", "-created_at"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("stage", "version"), name="competitions_stagerules_unique_version"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(models.Q(("status", "PUBLISHED"), _negated=True), ("published_at__isnull", False), _connector="OR"),
                        name="competitions_stagerules_published_has_date",
                    ),
                ],
            },
        ),
    ]
