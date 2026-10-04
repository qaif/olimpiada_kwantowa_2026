"""Medale olimpiady międzynarodowej (MED-01): schemat nagród etapu, ręczne zmiany i język dokumentu.

Tabele nowe i puste – migracja nie dotyka danych żadnego konkursu.
"""

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('competitions', '0033_video_room'),
        ('results', '0008_certificate_award_kinds'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CertificateLanguage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('language', models.CharField(max_length=10, verbose_name='język')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='utworzono')),
                ('certificate', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='medal_language', to='results.certificate', verbose_name='dokument')),
            ],
            options={
                'verbose_name': 'język dokumentu',
                'verbose_name_plural': 'języki dokumentów',
            },
        ),
        migrations.CreateModel(
            name='MedalScheme',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('gold_percent', models.DecimalField(decimal_places=2, default=Decimal('8'), max_digits=5, validators=[django.core.validators.MinValueValidator(Decimal('0')), django.core.validators.MaxValueValidator(Decimal('100'))], verbose_name='złoto – % uczestników')),
                ('silver_percent', models.DecimalField(decimal_places=2, default=Decimal('17'), max_digits=5, validators=[django.core.validators.MinValueValidator(Decimal('0')), django.core.validators.MaxValueValidator(Decimal('100'))], verbose_name='srebro – kolejne %')),
                ('bronze_percent', models.DecimalField(decimal_places=2, default=Decimal('25'), max_digits=5, validators=[django.core.validators.MinValueValidator(Decimal('0')), django.core.validators.MaxValueValidator(Decimal('100'))], verbose_name='brąz – kolejne %')),
                ('tie_policy', models.CharField(choices=[('INCLUSIVE', 'na korzyść uczestników (cała grupa dostaje wyższą nagrodę)'), ('EXCLUSIVE', 'w granicach puli (grupa, która by ją przekroczyła, dostaje niższą)')], default='INCLUSIVE', max_length=16, verbose_name='remis na granicy puli')),
                ('hm_percent_of_best', models.DecimalField(blank=True, decimal_places=2, default=Decimal('50'), max_digits=5, null=True, validators=[django.core.validators.MinValueValidator(Decimal('0')), django.core.validators.MaxValueValidator(Decimal('100'))], verbose_name='wyróżnienie – % najlepszego wyniku')),
                ('hm_full_solution', models.BooleanField(default=True, verbose_name='wyróżnienie za pełne rozwiązanie zadania')),
                ('updated_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='zmieniony')),
                ('frozen_at', models.DateTimeField(blank=True, null=True, verbose_name='ogłoszone')),
                ('publication_published_at', models.DateTimeField(blank=True, null=True, verbose_name='publikacja wyników z chwili ogłoszenia')),
                ('awards', models.JSONField(blank=True, default=dict, verbose_name='nagrody per wpis')),
                ('thresholds', models.JSONField(blank=True, default=dict, verbose_name='progi')),
                ('public_rows', models.JSONField(blank=True, default=list, verbose_name='tabela publiczna')),
                ('country_table', models.JSONField(blank=True, default=list, verbose_name='ranking krajów')),
                ('frozen_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='ogłosił')),
                ('stage', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='medal_scheme', to='competitions.stage', verbose_name='etap')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='zmienił')),
            ],
            options={
                'verbose_name': 'schemat medali',
                'verbose_name_plural': 'schematy medali',
                'ordering': ('-updated_at', '-id'),
            },
        ),
        migrations.CreateModel(
            name='MedalOverride',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('award', models.CharField(choices=[('GOLD', 'złoty medal'), ('SILVER', 'srebrny medal'), ('BRONZE', 'brązowy medal'), ('HM', 'wyróżnienie'), ('NONE', 'bez nagrody')], max_length=8, verbose_name='nagroda')),
                ('justification', models.TextField(max_length=2000, verbose_name='uzasadnienie')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='wpisano')),
                ('updated_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='zmieniono')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='wpisał')),
                ('entry', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='medal_overrides', to='competitions.stageentry', verbose_name='wpis')),
                ('scheme', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='overrides', to='medals.medalscheme')),
            ],
            options={
                'verbose_name': 'ręczna nagroda',
                'verbose_name_plural': 'ręczne nagrody',
                'ordering': ('scheme', 'entry_id'),
                'constraints': [models.UniqueConstraint(fields=('scheme', 'entry'), name='medals_override_one_per_entry')],
            },
        ),
    ]
