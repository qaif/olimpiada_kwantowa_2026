"""Jedna nullowalna kolumna w profilu uczestnika: dowiązanie do słownika organizatora (§ 1.3.3).

Migracja nie zmienia ani jednego istniejącego wiersza. Powstaje ``custom_institution_ref_id``
w ``accounts_participant`` – **nullowalna** i taka zostanie: uczestnik wybierający szkołę z wykazu
SIO albo wpisujący ją wolnym tekstem ma tu pustkę i to jest stan poprawny, a nie brakujące dane.
Reguła „nullowalne → backfill → osobne wydanie z ``NOT NULL``” (§ 0.7) nie ma tu więc drugiego
kroku, bo nie ma czego wypełniać.

Kolumna stoi **obok** ``school_ref``, a nie zamiast – dwa nullowalne klucze obce do dwóch różnych
wykazów, tak jak ``Certificate.entry``/``supervisor``. ``PROTECT`` z tego samego powodu, co przy
``school_ref``: skasowanie wiersza słownika zabrałoby uczestnikowi informację o placówce, a import
wykazu wygasza (``is_active=False``), nie kasuje.

Kolumna jest niewidoczna dla Konkursu #1: wypełnia ją wyłącznie gałąź rejestracji czytana przy
włączonej fladze ``custom_school_directory`` (``apps.accounts.services._resolve_institution``).
Formularz, serializer i eksporty zostają bez zmian, bo nic ich o tę kolumnę nie pyta.

``AddField`` kolumny nullowalnej jest odwracalne z definicji (``RemoveField``).
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0028_registration_profile"),
        # Tabela słownika musi już stać – to na nią wskazuje nowy klucz obcy.
        ("schools", "0005_custom_institution"),
    ]

    operations = [
        migrations.AddField(
            model_name="participant",
            name="custom_institution_ref",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="participants",
                to="schools.custominstitution",
                verbose_name="placówka ze słownika organizatora",
            ),
        ),
    ]
