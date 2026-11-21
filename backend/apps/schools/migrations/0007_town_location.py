"""Tabela punktów miejscowości dla mapy uczestników (MAP-01).

Sama struktura – wiersze wgrywa komenda ``seed_town_locations`` (przy każdym wdrożeniu, zaraz po
``seed_schools``), tak samo jak słownik szkół: dane referencyjne idą z fixture'u, nie z migracji.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("schools", "0006_pg_trgm_search_indexes"),
    ]

    operations = [
        migrations.CreateModel(
            name="TownLocation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("city_parent", models.CharField(max_length=120, verbose_name="gmina (miejscowość nadrzędna)")),
                (
                    "voivodeship",
                    models.CharField(
                        choices=[
                            ("dolnoslaskie", "dolnośląskie"),
                            ("kujawsko-pomorskie", "kujawsko-pomorskie"),
                            ("lubelskie", "lubelskie"),
                            ("lubuskie", "lubuskie"),
                            ("lodzkie", "łódzkie"),
                            ("malopolskie", "małopolskie"),
                            ("mazowieckie", "mazowieckie"),
                            ("opolskie", "opolskie"),
                            ("podkarpackie", "podkarpackie"),
                            ("podlaskie", "podlaskie"),
                            ("pomorskie", "pomorskie"),
                            ("slaskie", "śląskie"),
                            ("swietokrzyskie", "świętokrzyskie"),
                            ("warminsko-mazurskie", "warmińsko-mazurskie"),
                            ("wielkopolskie", "wielkopolskie"),
                            ("zachodniopomorskie", "zachodniopomorskie"),
                        ],
                        max_length=100,
                        verbose_name="województwo",
                    ),
                ),
                ("latitude", models.FloatField(verbose_name="szerokość geograficzna")),
                ("longitude", models.FloatField(verbose_name="długość geograficzna")),
            ],
            options={
                "verbose_name": "położenie miejscowości",
                "verbose_name_plural": "położenia miejscowości",
                "ordering": ("voivodeship", "city_parent"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("city_parent", "voivodeship"), name="schools_town_location_unique"
                    )
                ],
            },
        ),
    ]
