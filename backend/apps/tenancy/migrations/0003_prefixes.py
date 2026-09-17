"""Prefiksy identyfikatorów drukowanych: kod uczestnika i numer dyplomu, per konkurs.

Dwie stałe modułu przestają być stałymi: ``apps.accounts.models.PUBLIC_CODE_PREFIX`` (``"OLM-"``)
i ``apps.results.models.CERTIFICATE_NUMBER_PREFIX`` (``"OK"``). Oba identyfikatory są drukowane
i przepisywane ręcznie z list wyników i dyplomów, więc dwie olimpiady pod jednym prefiksem
znaczyłyby dwa różne kody nie do odróżnienia okiem (``docs/UNIWERSALNY-ETAP-1.md`` § 3.3).

Migracja jest **wyłącznie schematem**, bez ``RunPython``: wartości domyślne pól są dokładnie tymi
stałymi, które obowiązywały do dziś, więc Konkurs #1 dostaje ``OLM-`` i ``OK`` samym ``AddField``.
Dopisywanie tu danych byłoby wpisaniem literałem tego, co i tak wpisuje ``default`` – a każdy
wiersz w migracji danych to wiersz do sprawdzenia przed wdrożeniem.

Kody i numery **już nadane** zostają nietknięte. Pole opisuje nowe identyfikatory, a nie zastane:
przepisanie ``public_code`` uczestnikom zabrałoby im identyfikator, pod którym stoją w ogłoszonych
tabelach wyników i w korespondencji z organizatorem.

Uwaga dla migracji danych, które czytają ``Competition`` modelem historycznym (backfille z § 3.2):
kolejność tej migracji względem nich **nie jest** wymuszona zależnością, bo zależności nie da się
postawić z żadnej ze stron – tamte migracje są na produkcji już wykonane, a dopisanie im zależności
od migracji niewykonanej to ``InconsistentMigrationHistory`` przy najbliższym ``migrate``;
zależność odwrotna wiązałaby z kolei ``tenancy`` z czterema aplikacjami domenowymi. Dlatego każdy
taki backfill pyta wyłącznie o klucz główny (``values_list("pk")``), a nie o cały wiersz: zapytanie
o komplet kolumn przewracałoby się przy cofaniu migracji, gdyby ta migracja została zdjęta jako
pierwsza. Wzorzec jest w ``accounts.0020_backfill_competition_and_memberships``.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tenancy", "0002_competition_from_site")]

    operations = [
        migrations.AddField(
            model_name="competition",
            name="public_code_prefix",
            field=models.CharField(default="OLM-", max_length=8, verbose_name="prefiks kodu uczestnika"),
        ),
        migrations.AddField(
            model_name="competition",
            name="certificate_prefix",
            field=models.CharField(default="OK", max_length=8, verbose_name="prefiks numeru dyplomu"),
        ),
    ]
