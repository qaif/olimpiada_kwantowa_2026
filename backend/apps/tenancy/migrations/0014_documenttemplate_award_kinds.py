"""Kopie rodzajów nagród (MED-01) w ``DocumentKind`` – tekst dyplomu medalowego jako konfiguracja konkursu.

Wyłącznie lista wyboru; wierszy szablonów nie zakłada (Olimpiada Kwantowa medali nie wystawia).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenancy', '0013_competition_registration_mode'),
    ]

    operations = [
        migrations.AlterField(
            model_name='documenttemplate',
            name='kind',
            field=models.CharField(choices=[('LAUREAT', 'dyplom laureata'), ('FINALISTA', 'dyplom finalisty'), ('UCZESTNIK', 'zaświadczenie uczestnika'), ('OPIEKUN', 'zaświadczenie opiekuna'), ('WARSZTATY', 'zaświadczenie z warsztatów'), ('GUARDIAN_FORM', 'wzór zgody opiekuna'), ('INVOICE', 'faktura / rachunek'), ('ATTENDANCE_LIST', 'lista obecności'), ('STUDENT_STATUS', 'zaświadczenie o statusie ucznia'), ('MEDAL_GOLD', 'dyplom – złoty medal'), ('MEDAL_SILVER', 'dyplom – srebrny medal'), ('MEDAL_BRONZE', 'dyplom – brązowy medal'), ('HON_MENTION', 'dyplom – wyróżnienie')], max_length=24, verbose_name='rodzaj'),
        ),
    ]
