"""Schemat wydania B dla domeny zawodów: edycja dostaje właściciela.

Jedna kolumna, jedna tabela – i to jest cały schemat tej aplikacji, bo edycja jest **korzeniem**:
etap, zadanie, wpis do etapu, termin rozmowy i wydarzenie dochodzą do konkursu przez nią
(``docs/UNIWERSALNY-ETAP-1.md`` § 3.4). Kolumna ``competition_id`` przy każdym z nich byłaby drugą
drogą do tej samej prawdy, czyli drugą okazją do rozjazdu – a rozjazd w tabeli izolacji znaczy wyciek.

**Kolumna jest nullowalna i to jest stan przejściowy, nie projekt.** Wydanie B kładzie schemat
**przed** kodem, który go wymaga, żeby stara i nowa wersja aplikacji mogły przez chwilę stać obok
siebie – to jest warunek wdrożenia bez przestoju (§ 4.1). ``ADD COLUMN ... NULL`` w PostgreSQL nie
przepisuje pliku tabeli, więc migracja nie blokuje bazy nawet przy komplecie edycji. Domknięcie na
``NOT NULL`` wchodzi w wydaniu D, po zapytaniu kontrolnym (§ 4.4).

Czego ta migracja **nie** robi: nie rusza więzów ``competitions_edition_single_current`` ani
``unique`` na ``year_label``. Oba zawężają się do pary z konkursem dopiero w wydaniu D – wcześniej
byłyby **luźniejsze** niż dziś, bo w PostgreSQL wiersze z ``NULL`` w kolumnie unikalnej nie kolidują
ze sobą, a to znaczyłoby dwie edycje bieżące w bazie, która ma mieć jedną.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("competitions", "0018_alter_stage_format"),
        # ``0002``, a nie ``0001``: backfill w ``0020`` liczy na to, że Konkurs #1 jest już w bazie.
        ("tenancy", "0002_competition_from_site"),
    ]

    operations = [
        migrations.AddField(
            model_name="edition",
            name="competition",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="editions",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
    ]
