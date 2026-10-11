"""Przegląd SEC-01: poprzednie adresy e-mail (list o resecie 2FA) i „zapomnij wszystkie urządzenia”.

Dwie nowe, puste tabele; przy ``TWO_FACTOR_ENABLED=0`` nikt do nich nie pisze.
"""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('staff_mfa', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TrustRevocation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('revoked_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='unieważniono')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='konto')),
            ],
            options={
                'verbose_name': 'unieważnienie zapamiętanych urządzeń',
                'verbose_name_plural': 'unieważnienia zapamiętanych urządzeń',
            },
        ),
        migrations.CreateModel(
            name='PreviousEmail',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(max_length=254, verbose_name='poprzedni adres')),
                ('changed_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='zmieniono')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='konto')),
            ],
            options={
                'verbose_name': 'poprzedni adres e-mail (bezpieczeństwo 2FA)',
                'verbose_name_plural': 'poprzednie adresy e-mail (bezpieczeństwo 2FA)',
                'indexes': [models.Index(fields=['user', 'changed_at'], name='staff_mfa_prevemail_idx')],
            },
        ),
    ]
