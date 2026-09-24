"""Rejestr komunikatów zapamiętuje **parametr grupy**; lista grup odbiorców rośnie.

Prośba organizatora z 24.09.2026: „koordynator dostaje funkcję wysyłania maili do poszczególnych
grup uczestników, w tym do wszystkich”. Dochodzą grupy „wszyscy uczestnicy konkursu”, „zapisani
do etapu, bez wysłanej pracy”, uczestnicy z wybranego województwa (regionu), szkoły, klasy,
obecni na wybranym warsztacie oraz opiekunowie szkolni.

``target`` (JSON) trzyma parametr grupy z etykietą z chwili wysyłki – ``{"school": "sio:345",
"label": "XIV LO…, Warszawa"}`` – bo sama nazwa grupy („uczestnicy z wybranej szkoły”) w historii
nie mówi, do kogo poszedł list. Wiersze sprzed tej migracji dostają pusty słownik: ich grupy albo
parametru nie miały, albo nikt go wtedy nie zapisał, a zgadywanie go wstecz byłoby wymyślaniem
historii. Zmiana listy ``choices`` nie dotyka bazy (Django trzyma ją wyłącznie w modelu).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0032_consentrecord_supervisor_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='messagebroadcast',
            name='target',
            field=models.JSONField(blank=True, default=dict, verbose_name='parametry grupy'),
        ),
        migrations.AlterField(
            model_name='messagebroadcast',
            name='group',
            field=models.CharField(choices=[('ALL_PARTICIPANTS', 'wszyscy uczestnicy konkursu'), ('EDITION_PARTICIPANTS', 'uczestnicy bieżącej edycji (zapisani do etapu)'), ('STAGE_REGISTERED', 'zapisani do etapu'), ('STAGE_QUALIFIED', 'zakwalifikowani do etapu'), ('STAGE_NO_SUBMISSION', 'zapisani do etapu, bez wysłanej pracy'), ('REGION_PARTICIPANTS', 'uczestnicy z wybranego województwa (regionu)'), ('SCHOOL_PARTICIPANTS', 'uczestnicy z wybranej szkoły (placówki)'), ('GRADE_PARTICIPANTS', 'uczestnicy z wybranej klasy'), ('WORKSHOP_ATTENDEES', 'uczestnicy obecni na wybranym warsztacie'), ('SUPERVISORS', 'opiekunowie szkolni (nauczyciele)'), ('COMMITTEE', 'członkowie komitetu'), ('COMMITTEE_DISTRICT', 'komitet jednego województwa'), ('CUSTOM', 'wklejona lista adresów')], max_length=32, verbose_name='grupa odbiorców'),
        ),
    ]
