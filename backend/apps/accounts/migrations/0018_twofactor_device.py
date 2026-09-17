"""Drugi składnik logowania (TOTP) – jedna tabela, ``apps.accounts.twofactor``.

Migracja jest wyłącznie dokładająca: nie ma tu ani jednej zmiany istniejącej kolumny, więc
wdrożenie na działającej produkcji nie blokuje żadnej tabeli i nie wymaga przerwy. Konta bez
wiersza w tej tabeli zachowują się dokładnie tak, jak przed nią – logują się samym hasłem.
"""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0017_bulk_registration_invites'),
    ]

    operations = [
        migrations.CreateModel(
            name='TwoFactorDevice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('secret', models.TextField(verbose_name='sekret (zaszyfrowany)')),
                ('confirmed_at', models.DateTimeField(blank=True, null=True, verbose_name='potwierdzone')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='utworzone')),
                ('backup_codes', models.JSONField(blank=True, default=list, verbose_name='kody zapasowe (skróty)')),
                ('last_counter', models.BigIntegerField(default=0, verbose_name='ostatni użyty krok')),
                ('last_used_at', models.DateTimeField(blank=True, null=True, verbose_name='ostatnie użycie')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='two_factor', to=settings.AUTH_USER_MODEL, verbose_name='konto')),
            ],
            options={
                'verbose_name': 'drugi składnik logowania',
                'verbose_name_plural': 'drugie składniki logowania',
            },
        ),
    ]
