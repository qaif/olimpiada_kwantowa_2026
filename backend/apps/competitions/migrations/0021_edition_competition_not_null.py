"""Wydanie D dla korzenia domeny zawodów: edycja ma właściciela – i ma go na wyłączność.

Trzy zmiany, jedna reguła: od tej migracji **rocznik jest rocznikiem konkursu**.

1. ``competition`` przestaje być nullowalne. Nullowalność była stanem przejściowym wydania B,
   w którym schemat kładł się przed kodem, żeby stara i nowa wersja aplikacji mogły przez chwilę
   stać obok siebie (``docs/UNIWERSALNY-ETAP-1.md`` § 4.1). Edycja bez właściciela jest po wydaniu
   C wierszem niewidocznym – nie znajdzie jej ani ``current_edition()``, ani żaden manager
   zakresowany – więc dopuszczanie go dalej znaczyłoby wyłącznie ciche gubienie danych.
2. ``competitions_edition_single_current`` zawęża się z ``(is_current)`` do
   ``(competition, is_current)``. **Nazwa zostaje ta sama** (§ 1.4): ``RemoveConstraint`` +
   ``AddConstraint`` o jednej nazwie czyta się w logu jako zmiana zakresu tej samej reguły,
   a nie jako zniknięcie jednej więzi i pojawienie się drugiej, którą trzeba potem tropić.
3. ``year_label`` traci ``unique=True`` na rzecz ``competitions_edition_unique_year_label``
   na parze z konkursem. „I edycja 2026/2027” ma prawo istnieć w każdym konkursie; unikalność
   globalna znaczyłaby, że pierwszy organizator zajmuje oznaczenie rocznika wszystkim pozostałym.

Przed zmianą schematu idzie **zapytanie kontrolne** (§ 4.4). Niezerowy wynik przerywa wdrożenie
``RuntimeError``-em z nazwą tabeli, zanim ``ALTER TABLE`` cokolwiek ruszy – a kopia bazy z § 0.2
i tak leży obok. Jest to jedyna droga, którą wdrożenie może się tu zatrzymać, i ma się zatrzymać
głośno: ``NOT NULL`` postawione „siłą” na bazie z sierotami kończy się błędem PostgreSQL w środku
transakcji wdrożeniowej, bez wskazania, czego brakuje.

Kolejność operacji wynika z ich zależności: kontrola przed ``NOT NULL`` (bo to ona go broni),
zdjęcie starej więzi przed dołożeniem nowej o tej samej nazwie, zdjęcie ``unique`` z kolumny przed
dołożeniem więzi na parze (inaczej przez chwilę obowiązywałyby obie i zapis nowej edycji w drugim
konkursie dalej byłby niemożliwy).
"""

import django.db.models.deletion
from django.db import migrations, models

#: Tabela wymieniona w komunikacie wprost: wdrożenie zatrzymane bez nazwy tabeli zmusza do
#: szukania po omacku w bazie produkcyjnej, i to pod presją czasu.
TABLE = "competitions_edition"


def check_no_edition_without_competition(apps, schema_editor):
    """Zapytanie kontrolne przed ``NOT NULL`` (§ 4.4): edycja bez konkursu przerywa wdrożenie.

    Modele historyczne, bo migracja ma opisywać bazę z chwili swojego przebiegu, a nie kod, który
    akurat stoi w repozytorium. Zapytanie jest jedno i liczące – interesuje nas, **czy** takie
    wiersze są, a ich wypisanie zapisałoby do logu wdrożenia zawartość tabeli.
    """
    Edition = apps.get_model("competitions", "Edition")
    orphans = Edition.objects.filter(competition__isnull=True).count()
    if orphans:
        raise RuntimeError(
            f"{TABLE}: {orphans} edycji bez konkursu. Wydanie D wymaga właściciela dla każdego "
            "wiersza – uruchom backfill (competitions.0020_backfill_edition_competition) albo "
            "wskaż właściciela ręcznie, zanim powtórzysz migrację."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("competitions", "0020_backfill_edition_competition"),
    ]

    operations = [
        # ``elidable=False``: kontrola przed zmianą schematu nie jest krokiem budowy schematu
        # i nie wolno jej zwinąć przy ``squashmigrations``. Odwrotność to ``noop`` – zapytanie
        # kontrolne niczego nie zapisuje, więc nie ma czego cofać.
        migrations.RunPython(
            check_no_edition_without_competition, migrations.RunPython.noop, elidable=False
        ),
        migrations.AlterField(
            model_name="edition",
            name="competition",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="editions",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="edition",
            name="competitions_edition_single_current",
        ),
        migrations.AlterField(
            model_name="edition",
            name="year_label",
            field=models.CharField(max_length=64, verbose_name="oznaczenie edycji"),
        ),
        migrations.AddConstraint(
            model_name="edition",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_current", True)),
                fields=("competition", "is_current"),
                name="competitions_edition_single_current",
            ),
        ),
        migrations.AddConstraint(
            model_name="edition",
            constraint=models.UniqueConstraint(
                fields=("competition", "year_label"),
                name="competitions_edition_unique_year_label",
            ),
        ),
    ]
