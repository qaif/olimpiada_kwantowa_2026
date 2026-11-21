import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("tenancy", "0017_document_kind_choices_merged"),
    ]

    operations = [
        migrations.CreateModel(
            name="MapSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "min_town_count",
                    models.PositiveSmallIntegerField(
                        default=3,
                        validators=[
                            django.core.validators.MinValueValidator(3),
                            django.core.validators.MaxValueValidator(50),
                        ],
                        verbose_name="próg ochrony (najmniej uczestników w miejscowości)",
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="zmienione")),
                (
                    "competition",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="participant_map_settings",
                        to="tenancy.competition",
                        verbose_name="konkurs",
                    ),
                ),
            ],
            options={
                "verbose_name": "ustawienia mapy uczestników",
                "verbose_name_plural": "ustawienia mapy uczestników",
            },
        ),
    ]
