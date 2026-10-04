"""Przełącznik „angielska wersja interfejsu” znika z ustawień serwisu (I18N-01 § 1).

Jego treść przeniosła ``tenancy.0011`` do ``Competition.interface_languages`` – dlatego ta
migracja zależy od tamtej: kolumna musi jeszcze istnieć w chwili, w której jest przepisywana.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("cms", "0030_hero_slider_images_intro_deadline"),
        ("tenancy", "0011_competition_interface_languages"),
    ]

    operations = [
        migrations.RemoveField(model_name="sitesettings", name="english_interface_enabled"),
    ]
