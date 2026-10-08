"""Grupa „uczestnicy bez zaświadczenia o statusie ucznia” (prośba organizatora z 8.10.2026).

Zmiana wyłącznie listy wyboru – bez zmiany kolumny i danych.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0039_broadcast_group_teachers"),
    ]

    operations = [
        migrations.AlterField(
            model_name="messagebroadcast",
            name="group",
            field=models.CharField(
                choices=[('ALL_PARTICIPANTS', 'wszyscy uczestnicy konkursu'), ('ALL_PARTICIPANTS_AND_TEACHERS', 'wszyscy uczestnicy i nauczyciele'), ('EDITION_PARTICIPANTS', 'uczestnicy bieżącej edycji (zapisani do etapu)'), ('STAGE_REGISTERED', 'zapisani do etapu'), ('STAGE_QUALIFIED', 'zakwalifikowani do etapu'), ('STAGE_NO_SUBMISSION', 'zapisani do etapu, bez wysłanej pracy'), ('REGION_PARTICIPANTS', 'uczestnicy z wybranego województwa (regionu)'), ('SCHOOL_PARTICIPANTS', 'uczestnicy z wybranej szkoły (placówki)'), ('GRADE_PARTICIPANTS', 'uczestnicy z wybranej klasy'), ('WORKSHOP_ATTENDEES', 'uczestnicy obecni na wybranym warsztacie'), ('NO_STUDENT_STATUS', 'uczestnicy bez zaświadczenia o statusie ucznia'), ('SUPERVISORS', 'nauczyciele'), ('COMMITTEE', 'członkowie komitetu'), ('COMMITTEE_DISTRICT', 'komitet jednego województwa'), ('CUSTOM', 'wklejona lista adresów')],
                max_length=32,
                verbose_name="grupa odbiorców",
            ),
        ),
    ]
