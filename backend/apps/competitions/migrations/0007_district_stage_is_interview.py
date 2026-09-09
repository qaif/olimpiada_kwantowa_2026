"""II etap I edycji dostaje formę „rozmowa kwalifikacyjna online”.

Migracja danych, a nie zmiana ``seed_edition_kwantowa``: komenda dotyka wyłącznie etapów, których
jeszcze nie ma (``--sync-dates`` świadomie nie synchronizuje formy), więc na środowiskach, gdzie
edycja już stoi – dev, staging, produkcja – etap okręgowy zostałby przy formie domyślnej. Zmiana
formy z panelu jest możliwa, ale wtedy trzeba by ją wyklikać osobno na każdym środowisku, czyli
dokładnie w tej sytuacji, w której wartości zaczynają się między nimi różnić.

Etykieta edycji jest tu wpisana **literałem**, a nie zaimportowana z modułu komendy: migracja musi
dać ten sam wynik za rok, gdy komenda będzie już opisywać inną edycję albo w ogóle zniknie.

Rewers jest pusty: przywracanie „rozwiązań pisemnych” cofnięciem migracji opisywałoby przebieg
etapu, o którym ta migracja nic nie wie (mógł w międzyczasie dostać zapisy na rozmowy). Formę
zmienia się z panelu, pod bramką ``STAGE_FORMAT_LOCKED``.
"""

from django.db import migrations

#: Ta sama wartość, co ``EDITION_LABEL`` w ``seed_edition_kwantowa`` – celowo skopiowana.
EDITION_LABEL = "I edycja 2026/2027"


def set_interview_format(apps, schema_editor):
    Stage = apps.get_model("competitions", "Stage")
    Stage.objects.filter(edition__year_label=EDITION_LABEL, kind="DISTRICT").update(format="INTERVIEW")


class Migration(migrations.Migration):
    dependencies = [
        ("competitions", "0006_stage_name_format_interviews"),
    ]

    operations = [
        migrations.RunPython(set_interview_format, migrations.RunPython.noop),
    ]
