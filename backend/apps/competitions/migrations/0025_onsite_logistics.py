"""Logistyka etapu stacjonarnego: cztery puste tabele za flagą ``onsite_logistics`` (§ 1.5.2).

**Migracja nie zmienia ani jednego wiersza i nie dokłada ani jednej kolumny do tabel istniejących.**
Konkurs #1 flagi nie włącza, więc ``competitions_venue``, ``competitions_logisticssettings``,
``competitions_arrivalform`` i ``competitions_attendancerecord`` powstają puste i puste zostają:
nie czyta ich żaden ekran, żaden eksport i żadne zadanie okresowe (``docs/UNIWERSALNY-ETAP-2.md``
§ 0.1). ``Stage.location`` – dzisiejszy jedyny ślad świata fizycznego – zostaje nietknięte.

Cztery tabele, a nie trzy: ``competitions_logisticssettings`` niesie decyzję organizatora **D21**
(§ 6) o zbieraniu potrzeb szczególnych, czyli danych, które bywają danymi o zdrowiu (art. 9 RODO).
Domyślna wartość ``collect_special_needs=False`` jest właśnie tą decyzją zapisaną w schemacie:
konkurs, który nic nie ustawi, nie zbiera niczego, a ``competitions_arrivalform.note`` zostaje
w nim pustym napisem, bo nie ma drogi, którą cokolwiek by tam trafiło
(``apps.competitions.logistics.save_arrival_form``).

Wszystkie operacje są odwracalne z definicji (``CreateModel`` → ``DROP TABLE``, ``AddConstraint`` →
``DROP CONSTRAINT``), więc ``reverse_code`` nie ma czego opisywać (§ 0.7).
"""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('competitions', '0024_pipeline_from_stages'),
        ('tenancy', '0004_competition_site_alias'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='LogisticsSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('collect_special_needs', models.BooleanField(default=False, verbose_name='zbieraj potrzeby szczególne')),
                ('updated_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='zmienione')),
                ('competition', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='logistics_settings', to='tenancy.competition', verbose_name='konkurs')),
            ],
            options={
                'verbose_name': 'ustawienia logistyki',
                'verbose_name_plural': 'ustawienia logistyki',
            },
        ),
        migrations.CreateModel(
            name='Venue',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=160, verbose_name='nazwa')),
                ('address', models.CharField(blank=True, max_length=255, verbose_name='adres')),
                ('city', models.CharField(blank=True, max_length=120, verbose_name='miejscowość')),
                ('capacity', models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='pojemność')),
                ('note', models.TextField(blank=True, verbose_name='uwagi')),
                ('competition', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='venues', to='tenancy.competition', verbose_name='konkurs')),
            ],
            options={
                'verbose_name': 'miejsce zawodów',
                'verbose_name_plural': 'miejsca zawodów',
                'ordering': ('competition', 'name', 'id'),
            },
        ),
        migrations.CreateModel(
            name='ArrivalForm',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('arrives_on', models.DateField(blank=True, null=True, verbose_name='przyjazd')),
                ('departs_on', models.DateField(blank=True, null=True, verbose_name='wyjazd')),
                ('needs', models.JSONField(blank=True, default=list, verbose_name='potrzeby')),
                ('note', models.CharField(blank=True, max_length=500, verbose_name='uwagi')),
                ('submitted_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='złożony')),
                ('entry', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='arrival_form', to='competitions.stageentry', verbose_name='wpis do etapu')),
                ('venue', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='arrivals', to='competitions.venue', verbose_name='miejsce')),
            ],
            options={
                'verbose_name': 'formularz przyjazdu',
                'verbose_name_plural': 'formularze przyjazdu',
                'ordering': ('entry', 'id'),
            },
        ),
        migrations.CreateModel(
            name='AttendanceRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('present', models.BooleanField(default=False, verbose_name='obecny')),
                ('checked_in_at', models.DateTimeField(blank=True, null=True, verbose_name='odnotowano')),
                ('entry', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='attendance', to='competitions.stageentry', verbose_name='wpis do etapu')),
                ('recorded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='attendance_recorded', to=settings.AUTH_USER_MODEL, verbose_name='odnotował')),
            ],
            options={
                'verbose_name': 'obecność',
                'verbose_name_plural': 'obecności',
                'ordering': ('entry', 'id'),
                'constraints': [models.CheckConstraint(condition=models.Q(('present', False), ('checked_in_at__isnull', False), _connector='OR'), name='competitions_attendancerecord_present_has_time')],
            },
        ),
        migrations.AddConstraint(
            model_name='venue',
            constraint=models.UniqueConstraint(fields=('competition', 'name'), name='competitions_venue_unique_name'),
        ),
        migrations.AddConstraint(
            model_name='arrivalform',
            constraint=models.CheckConstraint(condition=models.Q(('arrives_on__isnull', True), ('departs_on__isnull', True), ('departs_on__gte', models.F('arrives_on')), _connector='OR'), name='competitions_arrivalform_departure_after_arrival'),
        ),
    ]
