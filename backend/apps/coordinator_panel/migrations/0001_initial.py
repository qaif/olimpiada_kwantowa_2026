# Napisana ręcznie (UX-PANEL-04): przypięte pozycje menu koordynatora.

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("tenancy", "0017_document_kind_choices_merged"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PinnedItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("path", models.CharField(max_length=300, verbose_name="ścieżka (względem prefiksu konkursu)")),
                ("position", models.PositiveSmallIntegerField(default=0, verbose_name="kolejność")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="przypięto")),
                (
                    "competition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="tenancy.competition",
                        verbose_name="konkurs",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="coordinator_pins",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="konto",
                    ),
                ),
            ],
            options={
                "verbose_name": "przypięta pozycja menu koordynatora",
                "verbose_name_plural": "przypięte pozycje menu koordynatora",
                "ordering": ("position", "pk"),
                "constraints": [
                    models.UniqueConstraint(fields=("user", "competition", "path"), name="coordpin_one_per_path")
                ],
            },
        ),
    ]
