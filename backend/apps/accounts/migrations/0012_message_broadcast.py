"""Rejestr komunikatów rozsyłanych przez organizatora (``MessageBroadcast``).

Tabela trzyma **treść i liczniki**, nigdy listy adresów. Kto był odbiorcą, wynika z grupy
zapisanej przy komunikacie i da się to odtworzyć zapytaniem; kopia adresów przy każdej wysyłce
mnożyłaby zbiory danych kontaktowych bez żadnego pożytku (RODO, zasada minimalizacji).

``created_at`` ma indeks, bo jedynym porządkiem, w jakim ta tabela jest czytana, jest „od
najnowszego” – ekran koordynatora pokazuje ostatnie wysyłki i nic poza nimi.
"""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0011_invitation_email_delivery'),
    ]

    operations = [
        migrations.CreateModel(
            name='MessageBroadcast',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now, verbose_name='wysłana')),
                ('group', models.CharField(choices=[('EDITION_PARTICIPANTS', 'uczestnicy bieżącej edycji'), ('STAGE_REGISTERED', 'zapisani do etapu'), ('STAGE_QUALIFIED', 'zakwalifikowani do etapu'), ('COMMITTEE', 'członkowie komitetu'), ('COMMITTEE_DISTRICT', 'komitet jednego województwa'), ('CUSTOM', 'wklejona lista adresów')], max_length=32, verbose_name='grupa odbiorców')),
                ('subject', models.CharField(max_length=200, verbose_name='temat')),
                ('body', models.TextField(verbose_name='treść')),
                ('recipient_count', models.PositiveIntegerField(default=0, verbose_name='liczba odbiorców')),
                ('sent_count', models.PositiveIntegerField(default=0, verbose_name='przekazanych do wysyłki')),
                ('status', models.CharField(choices=[('QUEUED', 'w kolejce'), ('SENT', 'przekazana do wysyłki'), ('FAILED', 'nieudana')], default='QUEUED', max_length=16, verbose_name='stan')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='broadcasts', to=settings.AUTH_USER_MODEL, verbose_name='wysłał')),
            ],
            options={
                'verbose_name': 'komunikat',
                'verbose_name_plural': 'komunikaty',
                'ordering': ('-created_at', '-id'),
            },
        ),
    ]
