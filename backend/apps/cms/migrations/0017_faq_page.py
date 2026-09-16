"""Typ strony „Najczęstsze pytania” (``/faq/``) wraz z wierszami pytań.

Sama migracja nie zakłada strony w drzewie – robi to ``manage.py seed_legacy_content``, bo treść
pytań jest treścią redakcyjną i podlega tym samym regułom, co reszta importu („zredagowane
w /cms/” i „skasowane w /cms/” zostają nietknięte).
"""

import django.db.models.deletion
import modelcluster.fields
import wagtail.fields
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cms", "0016_site_ga_measurement_id"),
        ("wagtailcore", "0094_alter_page_locale"),
    ]

    operations = [
        migrations.CreateModel(
            name="FAQPage",
            fields=[
                (
                    "page_ptr",
                    models.OneToOneField(
                        auto_created=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        parent_link=True,
                        primary_key=True,
                        serialize=False,
                        to="wagtailcore.page",
                    ),
                ),
                ("intro", wagtail.fields.RichTextField(blank=True, verbose_name="wprowadzenie")),
            ],
            options={
                "verbose_name": "najczęstsze pytania",
                "verbose_name_plural": "najczęstsze pytania",
            },
            bases=("wagtailcore.page",),
        ),
        migrations.CreateModel(
            name="FAQEntry",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("sort_order", models.IntegerField(blank=True, editable=False, null=True)),
                (
                    "section",
                    models.CharField(
                        blank=True,
                        help_text="Nagłówek grupy, np. „Konto i rejestracja”. Puste = pytanie bez sekcji.",
                        max_length=100,
                        verbose_name="sekcja",
                    ),
                ),
                ("question", models.CharField(max_length=250, verbose_name="pytanie")),
                ("answer", wagtail.fields.RichTextField(verbose_name="odpowiedź")),
                (
                    "page",
                    modelcluster.fields.ParentalKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="entries", to="cms.faqpage"
                    ),
                ),
            ],
            options={
                "verbose_name": "pytanie",
                "verbose_name_plural": "pytania",
                "ordering": ["sort_order"],
                "abstract": False,
            },
        ),
    ]
