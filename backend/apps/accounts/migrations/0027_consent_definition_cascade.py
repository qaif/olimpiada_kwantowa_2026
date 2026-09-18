"""``ConsentDefinition.competition``: ``PROTECT`` → ``CASCADE``. Sama zmiana więzu, zero danych.

Migracja ``0024`` wpisuje definicje zgód **każdemu** konkursowi w bazie. Przy ``PROTECT`` znaczyło
to, że od tamtej chwili żadnego konkursu nie da się skasować – a kasowanie konkursu jest zwykłą
czynnością operatora platformy (kreator ``/setup/``, testy izolacji, sprzątanie po imporcie).

Definicja zgody jest **konfiguracją samego konkursu**, a nie cudzymi danymi: opisuje, o co ten
konkurs pyta w formularzu. Dowód (``ConsentRecord``) nie ma do niej klucza obcego i nie ma go mieć –
``document_version`` jest kopią napisu z chwili złożenia oświadczenia – więc kaskada nie zabiera
ani jednego wpisu dowodowego i nie zostawia żadnego bez treści.

``PROTECT`` zostaje tam, gdzie wiersz niesie cudze dane: ``Participant``, ``CommitteeMember``,
``InvitationCode`` i ``Region``.

``AlterField`` jest odwracalne z definicji – zmienia wyłącznie regułę kasowania po stronie ORM-u
(i więz ``ON DELETE`` w bazie), a nie treść ani kształt kolumny.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0026_regions_from_voivodeships"),
        ("tenancy", "0004_competition_site_alias"),
    ]

    operations = [
        migrations.AlterField(
            model_name="consentdefinition",
            name="competition",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="consent_definitions",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
    ]
