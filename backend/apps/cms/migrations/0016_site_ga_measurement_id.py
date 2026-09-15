"""Identyfikator Google Analytics 4 w ustawieniach serwisu.

Pole jest **puste** na każdej istniejącej instalacji i to jest jego stan domyślny: puste znaczy
„analityki nie ma”. Migracja niczego więc nie włącza – dopiero wpisanie ``G-…`` w ``/cms/``
zamienia pasek cookie w pytanie o zgodę i dokłada hosty Google'a do nagłówka CSP.
"""

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cms", "0015_site_tiktok_youtube"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesettings",
            name="ga_measurement_id",
            field=models.CharField(
                blank=True,
                help_text=(
                    "Puste pole = brak analityki: serwis nie wczytuje skryptów Google'a i nie "
                    "pyta o zgodę. Po wpisaniu identyfikatora pasek cookie zamienia się w pytanie "
                    "o zgodę, a statystyki zbierają się dopiero po jej udzieleniu."
                ),
                max_length=32,
                validators=[
                    django.core.validators.RegexValidator(
                        message=(
                            "Identyfikator Google Analytics 4 ma postać „G-” i co najmniej "
                            "sześciu wielkich liter lub cyfr, na przykład G-ABC1234DEF. "
                            "Znajdziesz go w GA4 w sekcji Administracja → Strumienie danych."
                        ),
                        regex="^G-[A-Z0-9]{6,}$",
                    )
                ],
                verbose_name="identyfikator Google Analytics (G-…)",
            ),
        ),
    ]
