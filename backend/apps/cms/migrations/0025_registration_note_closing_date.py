"""Komunikat o rejestracji: zapowiedź startu → termin zakończenia (uwaga organizatora z 20.09.2026).

Zmienia się wartość domyślna pola **i** wiersze, które nadal niosą poprzednią wartość domyślną.
Warunek na starą treść jest tu całą ostrożnością: komunikat zredagowany w /cms/ (albo wyczyszczony)
należy do redakcji i zostaje taki, jaki jest. Cofnięcie przywraca zapowiedź tą samą regułą.
"""

from django.db import migrations, models

OLD = "Oficjalny start rejestracji: 21 września 2026."
NEW = "Zakończenie rejestracji: 28.02.2027"


def _swap(apps, before: str, after: str) -> None:
    apps.get_model("cms", "SiteSettings").objects.filter(registration_note=before).update(
        registration_note=after
    )


def forwards(apps, schema_editor):
    _swap(apps, OLD, NEW)


def backwards(apps, schema_editor):
    _swap(apps, NEW, OLD)


class Migration(migrations.Migration):
    dependencies = [("cms", "0024_site_english_interface_flag")]

    operations = [
        migrations.AlterField(
            model_name="sitesettings",
            name="registration_note",
            field=models.CharField(
                blank=True,
                default=NEW,
                max_length=200,
                verbose_name="komunikat o rejestracji",
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
