"""Nowy rodzaj tekstu dokumentu: ``VISA_INVITATION`` – list zapraszający do wizy (LOG-01).

Zmiana wyłącznie listy wyboru (``choices``) – bez danych i bez zmiany kolumny w bazie (ta sama
droga, co ``0009_document_kind_student_status``). Wiersza szablonu nie wpisujemy: dopóki go nie ma,
list składa się z angielskich napisów odwrotu w ``apps.delegation_logistics.letters``.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenancy', '0014_merge_20261004_1935'),
    ]

    operations = [
        migrations.AlterField(
            model_name='documenttemplate',
            name='kind',
            field=models.CharField(choices=[('LAUREAT', 'dyplom laureata'), ('FINALISTA', 'dyplom finalisty'), ('UCZESTNIK', 'zaświadczenie uczestnika'), ('OPIEKUN', 'zaświadczenie opiekuna'), ('WARSZTATY', 'zaświadczenie z warsztatów'), ('GUARDIAN_FORM', 'wzór zgody opiekuna'), ('INVOICE', 'faktura / rachunek'), ('ATTENDANCE_LIST', 'lista obecności'), ('STUDENT_STATUS', 'zaświadczenie o statusie ucznia'), ('VISA_INVITATION', 'list zapraszający (wiza)')], max_length=24, verbose_name='rodzaj'),
        ),
    ]
