"""Schemat wydania C dla części informacyjnej: komunikat organizatora dostaje właściciela.

Jedna kolumna i jedna tabela – bo w całym ``apps.cms`` jest dokładnie **jeden** model, który nie ma
jak dojść do konkursu inną drogą. Strony Wagtaila należą do konkursu przez swoją witrynę
(``Competition.site``, relacja jeden do jednego), ustawienia serwisu też
(``SiteSettings`` dziedziczy po ``BaseSiteSetting``), a obecność na warsztatach – przez uczestnika.
Komunikat nie wisi przy niczym: wisi nad całym serwisem, więc bez własnej kolumny był globalny dla
instalacji (``docs/UNIWERSALNY-ETAP-1.md`` § 3.2 i § 3.7, czwarty z czterech globalnych odczytów).

**Kolumna jest nullowalna i to jest stan przejściowy, nie projekt.** ``ADD COLUMN ... NULL``
w PostgreSQL nie przepisuje pliku tabeli, więc migracja nie blokuje bazy; domknięcie na ``NOT NULL``
wchodzi w wydaniu D, po zapytaniu kontrolnym (§ 4.4). Backfill jest osobno, w ``0022`` – tak samo,
jak przy ``competitions.0019``/``0020``, żeby dało się cofnąć dane bez cofania schematu.

Czego ta migracja **nie** robi: nie rusza drzewa stron, slugów, rewizji ani ``SiteSettings``
(§ 6, T4). ``Announcement`` jest jedyną tabelą tej aplikacji, której dotyka.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cms", "0020_site_supervisor_registration_flag"),
        # ``0002``, a nie ``0001``: backfill w ``0022`` liczy na to, że Konkurs #1 jest już w bazie.
        ("tenancy", "0002_competition_from_site"),
    ]

    operations = [
        migrations.AddField(
            model_name="announcement",
            name="competition",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="announcements",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
    ]
