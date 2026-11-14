# PUB-02: załączniki zadań (pliki danych do pobrania przy treści zadania). Pisana ręcznie (bez bazy
# przy tworzeniu). Nowa tabela – żadnego przepisywania istniejących wierszy.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import apps.competitions.models
import apps.competitions.storage


class Migration(migrations.Migration):
    dependencies = [
        ("competitions", "0037_problem_publish_at"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ProblemAttachment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "file",
                    models.FileField(
                        storage=apps.competitions.storage.private_media_storage,
                        upload_to=apps.competitions.models.attachment_upload_to,
                        verbose_name="plik",
                    ),
                ),
                ("filename", models.CharField(max_length=150, verbose_name="nazwa pliku")),
                ("description", models.CharField(blank=True, max_length=300, verbose_name="opis")),
                ("position", models.PositiveSmallIntegerField(default=0, verbose_name="kolejność")),
                ("size", models.PositiveBigIntegerField(default=0, verbose_name="rozmiar (B)")),
                ("sha256", models.CharField(max_length=64, verbose_name="SHA-256")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="utworzono")),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="dodał",
                    ),
                ),
                (
                    "problem",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attachments",
                        to="competitions.problem",
                    ),
                ),
            ],
            options={
                "verbose_name": "załącznik zadania",
                "verbose_name_plural": "załączniki zadań",
                "ordering": ("problem", "position", "id"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("problem", "filename"), name="competitions_problemattachment_unique_name"
                    )
                ],
            },
        ),
    ]
