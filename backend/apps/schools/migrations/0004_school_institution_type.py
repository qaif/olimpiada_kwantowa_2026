"""Rodzaj placówki przy każdym wierszu słownika: dziś wszystkie są szkołą ponadpodstawową.

Kolumna jest **jedna** i wchodzi z wartością domyślną ``SECONDARY`` (``apps.schools.models.
InstitutionType``). To nie jest założenie na wyrost, tylko opis stanu: fixture
``szkoly-srednie-sio-2025.json`` powstaje z reguły doboru ``SECONDARY_KINDS`` (``apps.schools.
sio``), więc każdy z 8118 wgranych wierszy jest szkołą ponadpodstawową dla młodzieży i nie ma
w bazie ani jednego wiersza, dla którego ta wartość byłaby nieprawdą.

Dlatego **nie ma tu ``RunPython``**: ``AddField`` z ``default`` wpisuje wartość do wszystkich
istniejących wierszy jednym ``UPDATE`` w tej samej transakcji, a backfill pisany ręcznie robiłby
to drugi raz i byłby drugim miejscem, w którym ta wartość mogłaby się rozjechać z definicją pola.
Migracja jest odwracalna z definicji (``RemoveField``), a wycofanie zdejmuje kolumnę razem
z zawartością – wgrany słownik zostaje nietknięty.

Kolumna jest indeksowana, bo **każde** zapytanie wyszukiwarki po etapie 2 zawęża się nią do
rodzajów dopuszczonych przez konkurs (§ 1.3.4 planu etapu 2), a przy jednym rodzaju w bazie
planista i tak jej nie użyje – koszt to jeden indeks na tabeli, do której pisze wyłącznie
``seed_schools``.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("schools", "0003_school_city_parent"),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="institution_type",
            field=models.CharField(
                choices=[
                    ("PRIMARY", "szkoła podstawowa"),
                    ("SECONDARY", "szkoła ponadpodstawowa"),
                    ("UNIVERSITY", "uczelnia wyższa"),
                    ("FOREIGN", "placówka poza Polską"),
                    ("NONE", "bez szkoły"),
                    ("OTHER", "inna placówka"),
                ],
                db_index=True,
                default="SECONDARY",
                max_length=16,
                verbose_name="rodzaj placówki",
            ),
        ),
    ]
