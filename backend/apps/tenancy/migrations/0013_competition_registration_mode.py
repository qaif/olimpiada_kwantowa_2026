"""Tryb rejestracji uczestników konkursu i domyślny limit delegacji (DEL-01).

Wartości domyślne opisują stan sprzed zmiany (``OPEN``), więc żaden konkurs nie zmienia zachowania
przez samo wdrożenie – ``iqo`` przestawia operator albo koordynator (``docs/OPERACJE.md``).
"""

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenancy', '0012_competition_site_logo_social_image'),
    ]

    operations = [
        migrations.AddField(
            model_name='competition',
            name='delegation_max_students',
            field=models.PositiveSmallIntegerField(default=6, help_text='Obowiązuje delegacje zakładane od teraz; limit istniejącej zmienia się przy niej.', validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(100)], verbose_name='domyślny limit uczniów delegacji'),
        ),
        migrations.AddField(
            model_name='competition',
            name='registration_mode',
            field=models.CharField(choices=[('OPEN', 'otwarta – uczestnik zakłada konto sam'), ('DELEGATIONS', 'przez delegacje krajowe – uczniów zgłasza opiekun drużyny')], default='OPEN', help_text='<strong>Uwaga:</strong> „przez delegacje krajowe” zamyka samodzielną rejestrację uczestników (formularz, API, Google/Facebook, import nauczyciela i rejestrację opiekunów szkolnych). Uczniów zgłaszają wtedy wyłącznie opiekunowie drużyn zaproszeni w panelu „Delegacje”. Konta, które już istnieją, działają dalej.', max_length=16, verbose_name='tryb rejestracji uczestników'),
        ),
    ]
