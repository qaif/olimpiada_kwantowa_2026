"""Rodzaje dokumentów nagród olimpiady międzynarodowej (MED-01): złoto, srebro, brąz, wyróżnienie.

Wyłącznie lista wyboru kolumny ``kind`` (bez zmiany długości) – istniejące wiersze bez zmian.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('results', '0007_publication_named_modes_and_qualified_only'),
    ]

    operations = [
        migrations.AlterField(
            model_name='certificate',
            name='kind',
            field=models.CharField(choices=[('LAUREAT', 'laureat'), ('FINALISTA', 'finalista'), ('UCZESTNIK', 'uczestnik'), ('OPIEKUN', 'opiekun'), ('WARSZTATY', 'uczestnik warsztatów'), ('MEDAL_GOLD', 'złoty medal'), ('MEDAL_SILVER', 'srebrny medal'), ('MEDAL_BRONZE', 'brązowy medal'), ('HON_MENTION', 'wyróżnienie')], max_length=16, verbose_name='rodzaj'),
        ),
        migrations.AlterField(
            model_name='certificatetemplate',
            name='kind',
            field=models.CharField(blank=True, choices=[('LAUREAT', 'laureat'), ('FINALISTA', 'finalista'), ('UCZESTNIK', 'uczestnik'), ('OPIEKUN', 'opiekun'), ('WARSZTATY', 'uczestnik warsztatów'), ('MEDAL_GOLD', 'złoty medal'), ('MEDAL_SILVER', 'srebrny medal'), ('MEDAL_BRONZE', 'brązowy medal'), ('HON_MENTION', 'wyróżnienie')], help_text='Puste = szablon dla wszystkich rodzajów dokumentów.', max_length=16, verbose_name='rodzaj dokumentu'),
        ),
    ]
