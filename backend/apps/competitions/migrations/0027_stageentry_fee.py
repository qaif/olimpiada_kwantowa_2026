"""Skrót do należności przy wpisie do etapu: ``StageEntry.fee`` (§ 1.5.1).

**Migracja nie zmienia ani jednego wiersza.** Konkurs #1 jest bezpłatny, więc kolumna dostaje
``NULL`` wszędzie i tak zostaje; flaga ``fees`` jest domyślnie wyłączona, a jedyny czytelnik tego
pola (``apps.tenancy.fees.submission_blocked``) wychodzi przy niej z odpowiedzią „nie blokuje”
jeszcze przed zapytaniem do bazy (decyzja organizatora D16).

``ADD COLUMN ... NULL`` bez ``DEFAULT`` w PostgreSQL 16 nie przepisuje pliku tabeli, więc
wdrożenie nie blokuje tabeli wpisów nawet przy komplecie uczestników. Domknięcia na ``NOT NULL``
nie będzie nigdy – zawody bezpłatne są stanem docelowym, a nie brakiem do uzupełnienia (§ 0.7).

Operacja jest odwracalna z definicji (``AddField`` → ``DROP COLUMN``), więc ``reverse_code`` nie
ma czego opisywać.

Zależność od ``tenancy.0006_fees`` jest jednostronna i taka ma zostać: tabela należności musi
stać, zanim wskaże na nią klucz obcy, a rejestr wpisowego nie wie nic o wpisach do etapów.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("competitions", "0026_teams"),
        ("tenancy", "0006_fees"),
    ]

    operations = [
        migrations.AddField(
            model_name="stageentry",
            name="fee",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="stage_entries",
                to="tenancy.participantfee",
                verbose_name="wpisowe",
            ),
        ),
    ]
