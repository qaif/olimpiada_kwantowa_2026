"""Zaświadczenia o statusie ucznia (``apps.student_status``, prośba organizatora z 24.09.2026).

Nowa, pusta tabela – nic nie jest przenoszone ani wypełniane. Funkcja stoi za flagą
``student_status_certificate`` (domyślnie wyłączoną), więc do jej zapalenia tabela zostaje pusta.
"""

import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('accounts', '0032_consentrecord_supervisor_and_more'),
        ('competitions', '0031_interview_score'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='StudentStatusCertificate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.PositiveSmallIntegerField(verbose_name='wersja')),
                ('is_current', models.BooleanField(default=True, verbose_name='bieżąca')),
                ('status', models.CharField(choices=[('PENDING', 'oczekuje na weryfikację'), ('ACCEPTED', 'zaakceptowane'), ('REJECTED', 'odrzucone'), ('SUPERSEDED', 'zastąpione nowszym plikiem')], default='PENDING', max_length=16, verbose_name='stan')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('object_key', models.CharField(blank=True, max_length=300, verbose_name='klucz obiektu')),
                ('sha256', models.CharField(max_length=64, verbose_name='sha256')),
                ('mime', models.CharField(max_length=40, verbose_name='typ')),
                ('size_bytes', models.PositiveIntegerField(verbose_name='rozmiar (B)')),
                ('scan_status', models.CharField(choices=[('PENDING', 'oczekuje na skan'), ('CLEAN', 'czysty'), ('INFECTED', 'zainfekowany'), ('ERROR', 'błąd skanu')], default='PENDING', max_length=16, verbose_name='skan antywirusowy')),
                ('scanned_at', models.DateTimeField(blank=True, null=True, verbose_name='przeskanowany')),
                ('uploaded_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='przesłane')),
                ('decided_at', models.DateTimeField(blank=True, null=True, verbose_name='rozpatrzone')),
                ('rejection_reason', models.TextField(blank=True, verbose_name='powód odrzucenia')),
                ('file_removed_at', models.DateTimeField(blank=True, null=True, verbose_name='plik usunięty')),
                ('decided_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='rozpatrzył(a)')),
                ('edition', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='student_status_certificates', to='competitions.edition', verbose_name='edycja')),
                ('participant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='student_status_certificates', to='accounts.participant', verbose_name='uczestnik')),
            ],
            options={
                'verbose_name': 'zaświadczenie o statusie ucznia',
                'verbose_name_plural': 'zaświadczenia o statusie ucznia',
                'ordering': ('participant_id', 'edition_id', '-version'),
                'indexes': [models.Index(fields=['edition', 'is_current', 'status'], name='student_status_filter_idx')],
                'constraints': [models.UniqueConstraint(fields=('participant', 'edition', 'version'), name='student_status_version_per_edition'), models.UniqueConstraint(condition=models.Q(('is_current', True)), fields=('participant', 'edition'), name='student_status_single_current')],
            },
        ),
    ]
