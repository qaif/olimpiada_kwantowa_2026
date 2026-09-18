"""Słownik własny organizatora: **nowa, pusta tabela** obok wykazu publicznego (§ 1.3.3).

Migracja nie dotyka ``School`` ani jednym słowem – żadnej kolumny, żadnego wiersza, żadnego
indeksu. Powstaje wyłącznie ``schools_custominstitution``, pusta: wiersze wgrywa dopiero import
CSV z panelu koordynatora (``apps.schools.custom.import_custom_institutions``), a Konkurs #1 nie
ma go z czego wywołać, bo flagi ``custom_school_directory`` nie włącza.

Tabela osobna, a nie kolumna właściciela w ``School`` – to jest decyzja D2 etapu 1: wykaz SIO
zostaje rejestrem publicznym wspólnym dla instalacji, a lista placówek organizatora należy do
jednego konkursu. Uzasadnienie w komplecie stoi w docstringu ``apps/schools/custom.py``.

``competition`` jest ``CASCADE`` (słownik jest konfiguracją samego konkursu, tak jak ``Region``).
Region stoi w tabeli jako **kod** (``region_code``), a nie klucz obcy – uzasadnienie przy polu
w ``apps/schools/custom.py``; dzięki temu ta migracja nie zależy od ``accounts`` i kasowanie
regionów nie ciągnie za sobą kaskady do tabeli, której w przewijanym stanie może nie być.

Zależność od ``tenancy`` wskazuje najstarszą migrację, która ma już tabelę konkursów – tak samo
jak ``accounts.0025_region`` i ``accounts.0028_registration_profile`` – żeby nie wiązać tego
wydania z niczym, czego nie potrzebuje.

``CreateModel`` jest odwracalne z definicji (``DeleteModel``), a wycofanie zdejmuje tabelę razem
z zawartością; wykaz publiczny zostaje nietknięty.
"""

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("schools", "0004_school_institution_type"),
        # Własna kolumna konkursu; wskazujemy najstarszą migrację, która ma tabelę konkursów.
        ("tenancy", "0004_competition_site_alias"),
    ]

    operations = [
        migrations.CreateModel(
            name="CustomInstitution",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "external_id",
                    models.CharField(
                        blank=True, max_length=64, verbose_name="identyfikator organizatora"
                    ),
                ),
                ("name", models.CharField(max_length=255, verbose_name="nazwa")),
                (
                    "institution_type",
                    models.CharField(
                        choices=[
                            ("PRIMARY", "szkoła podstawowa"),
                            ("SECONDARY", "szkoła ponadpodstawowa"),
                            ("UNIVERSITY", "uczelnia wyższa"),
                            ("FOREIGN", "placówka poza Polską"),
                            ("NONE", "bez szkoły"),
                            ("OTHER", "inna placówka"),
                        ],
                        db_index=True,
                        default="OTHER",
                        max_length=16,
                        verbose_name="rodzaj placówki",
                    ),
                ),
                ("country", models.CharField(blank=True, max_length=2, verbose_name="kraj")),
                (
                    "region_code",
                    models.SlugField(blank=True, max_length=40, verbose_name="kod regionu"),
                ),
                ("city", models.CharField(blank=True, max_length=120, verbose_name="miejscowość")),
                (
                    "postal_code",
                    models.CharField(blank=True, max_length=12, verbose_name="kod pocztowy"),
                ),
                ("address", models.CharField(blank=True, max_length=255, verbose_name="adres")),
                (
                    "search_text",
                    models.CharField(
                        db_index=True,
                        default="",
                        editable=False,
                        max_length=400,
                        verbose_name="tekst wyszukiwania",
                    ),
                ),
                (
                    "city_search",
                    models.CharField(
                        db_index=True,
                        default="",
                        editable=False,
                        max_length=120,
                        verbose_name="miejscowość (postać porównawcza)",
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="aktywna")),
                (
                    "source_label",
                    models.CharField(blank=True, max_length=120, verbose_name="źródło"),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        default=django.utils.timezone.now, verbose_name="dodana"
                    ),
                ),
                (
                    "competition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="custom_institutions",
                        to="tenancy.competition",
                        verbose_name="konkurs",
                    ),
                ),
            ],
            options={
                "verbose_name": "placówka organizatora",
                "verbose_name_plural": "placówki organizatora",
                "ordering": ("name", "id"),
                "indexes": [
                    models.Index(
                        fields=["competition", "search_text"], name="schools_custom_search_idx"
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("external_id", ""), _negated=True),
                        fields=("competition", "external_id"),
                        name="schools_custominstitution_unique_external_id",
                    )
                ],
            },
        ),
    ]
