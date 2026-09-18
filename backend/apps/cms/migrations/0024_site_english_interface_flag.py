"""Przełącznik „angielska wersja interfejsu” w ustawieniach serwisu.

Sama kolumna, bez przepisywania danych: wartość domyślna ``False`` jest tu **decyzją
organizatora** („do polskiej olimpiady niech będzie wersja tylko w języku polskim na razie”),
a nie ostrożnością migracji. Istniejące wiersze ``UserPreference.language`` zostają nietknięte –
zapisany przez kogoś angielski przestaje być stosowany, ale nie znika i wraca w dniu, w którym
organizator ten przełącznik włączy.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cms', '0023_competition_cms_groups'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='english_interface_enabled',
            field=models.BooleanField(default=False, help_text='Wyłączone: serwis jest po polsku niezależnie od ustawień przeglądarki, a w pasku konta nie ma przełącznika języka. Włączenie dokłada flagę „EN” i pozwala każdemu wybrać angielski; zapisane wcześniej wybory wracają wtedy same.', verbose_name='angielska wersja interfejsu'),
        ),
    ]
