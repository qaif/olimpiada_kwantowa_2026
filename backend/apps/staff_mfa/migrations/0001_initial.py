"""SEC-01: polityka 2FA konkursu i okres przejściowy konta – dwie nowe, puste tabele.

Migracja wyłącznie dokładająca; przy ``TWO_FACTOR_ENABLED=0`` nikt do tych tabel nie pisze.
"""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('tenancy', '0017_document_kind_choices_merged'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TwoFactorGrace',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('required_since', models.DateTimeField(default=django.utils.timezone.now, verbose_name='wymagane od')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='two_factor_grace', to=settings.AUTH_USER_MODEL, verbose_name='konto')),
            ],
            options={
                'verbose_name': 'okres przejściowy 2FA',
                'verbose_name_plural': 'okresy przejściowe 2FA',
            },
        ),
        migrations.CreateModel(
            name='TwoFactorPolicy',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('mode', models.CharField(choices=[('auto', 'automatycznie (personel konkursów z danymi wrażliwymi)'), ('custom', 'wybrane role')], default='auto', max_length=8, verbose_name='tryb')),
                ('roles', models.JSONField(blank=True, default=list, verbose_name='wymagane role (tryb „wybrane”)')),
                ('grace_days', models.PositiveSmallIntegerField(blank=True, help_text='Puste = wartość platformy (TWO_FACTOR_GRACE_DAYS). Zero = wymóg od razu.', null=True, verbose_name='okres przejściowy (dni)')),
                ('allow_remember', models.BooleanField(default=True, help_text='Wyłączone: kod przy każdym logowaniu, niezależnie od TWO_FACTOR_REMEMBER_DAYS.', verbose_name='pozwól zapamiętać urządzenie')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='zmieniono')),
                ('competition', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='two_factor_policy', to='tenancy.competition', verbose_name='konkurs')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='zmienił(a)')),
            ],
            options={
                'verbose_name': 'polityka 2FA konkursu',
                'verbose_name_plural': 'polityki 2FA konkursów',
            },
        ),
    ]
