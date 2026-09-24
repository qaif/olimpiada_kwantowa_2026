"""Nowy rodzaj tekstu dokumentu: ``STUDENT_STATUS`` – wzór zaświadczenia o statusie ucznia.

Zmiana wyłącznie listy wyboru (``choices``) – bez danych i bez zmiany kolumny w bazie. Wiersza
szablonu dla Konkursu #1 **nie** wpisujemy: dopóki go nie ma, wzór składa się z napisów odwrotu
w ``apps.student_status.pdf``, a organizator z flagą ``document_templates`` pisze pierwszą wersję
na ekranie „Szablony dokumentów”.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenancy', '0008_competition_submission_forward_emails'),
    ]

    operations = [
        migrations.AlterField(
            model_name='documenttemplate',
            name='kind',
            field=models.CharField(choices=[('LAUREAT', 'dyplom laureata'), ('FINALISTA', 'dyplom finalisty'), ('UCZESTNIK', 'zaświadczenie uczestnika'), ('OPIEKUN', 'zaświadczenie opiekuna'), ('WARSZTATY', 'zaświadczenie z warsztatów'), ('GUARDIAN_FORM', 'wzór zgody opiekuna'), ('INVOICE', 'faktura / rachunek'), ('ATTENDANCE_LIST', 'lista obecności'), ('STUDENT_STATUS', 'zaświadczenie o statusie ucznia')], max_length=24, verbose_name='rodzaj'),
        ),
    ]
