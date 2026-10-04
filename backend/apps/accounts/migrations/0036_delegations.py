"""Delegacje krajowe (DEL-01): delegacja, opiekun drużyny, zaproszenie, rola i pola profilu.

Wyłącznie nowe tabele i kolumny **nullowalne** – żaden istniejący wiersz nie dostaje wartości,
więc migracja jest odwracalna i Olimpiada Kwantowa po niej wygląda dokładnie tak, jak przed nią.
Więz „dokładnie jeden właściciel zgody” jest przebudowany (trzeci właściciel: opiekun drużyny);
istniejące wiersze spełniają nowy warunek z definicji, bo ``team_leader`` jest w nich pusty.
"""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0035_committee_video_room_issuer'),
        ('competitions', '0033_video_room'),
        ('tenancy', '0013_competition_registration_mode'),
    ]

    operations = [
        migrations.CreateModel(
            name='Delegation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('max_students', models.PositiveSmallIntegerField(verbose_name='limit uczniów')),
                ('status', models.CharField(choices=[('ACTIVE', 'otwarta'), ('CLOSED', 'zamknięta')], default='ACTIVE', max_length=16, verbose_name='stan')),
                ('note', models.TextField(blank=True, max_length=2000, verbose_name='notatka koordynatora')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='utworzona')),
            ],
            options={
                'verbose_name': 'delegacja',
                'verbose_name_plural': 'delegacje',
                'ordering': ('country__name', 'id'),
            },
        ),
        migrations.CreateModel(
            name='DelegationInvitation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(max_length=254, verbose_name='adres opiekuna')),
                ('token_hash', models.CharField(editable=False, max_length=64, unique=True, verbose_name='sha256 tokenu')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='utworzone')),
                ('sent_at', models.DateTimeField(blank=True, null=True, verbose_name='wysłane')),
                ('expires_at', models.DateTimeField(verbose_name='wygasa')),
                ('accepted_at', models.DateTimeField(blank=True, null=True, verbose_name='przyjęte')),
                ('revoked_at', models.DateTimeField(blank=True, null=True, verbose_name='cofnięte')),
            ],
            options={
                'verbose_name': 'zaproszenie opiekuna drużyny',
                'verbose_name_plural': 'zaproszenia opiekunów drużyn',
                'ordering': ('-created_at', '-id'),
            },
        ),
        migrations.CreateModel(
            name='DelegationLeader',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('accepted_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='przyjął zaproszenie')),
            ],
            options={
                'verbose_name': 'opiekun drużyny',
                'verbose_name_plural': 'opiekunowie drużyn',
                'ordering': ('accepted_at', 'id'),
            },
        ),
        migrations.RemoveConstraint(
            model_name='consentrecord',
            name='accounts_consentrecord_exactly_one_owner',
        ),
        migrations.AddField(
            model_name='participant',
            name='registered_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='registered_participants', to=settings.AUTH_USER_MODEL, verbose_name='zgłoszony przez'),
        ),
        migrations.AlterField(
            model_name='membership',
            name='role',
            field=models.CharField(choices=[('participant', 'uczestnik'), ('reviewer', 'recenzent'), ('appeals', 'komisja odwoławcza'), ('coordinator', 'koordynator'), ('supervisor', 'opiekun szkolny'), ('team_leader', 'opiekun drużyny narodowej')], max_length=16, verbose_name='rola'),
        ),
        migrations.AddField(
            model_name='delegation',
            name='competition',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='delegations', to='tenancy.competition', verbose_name='konkurs'),
        ),
        migrations.AddField(
            model_name='delegation',
            name='country',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='delegations', to='accounts.region', verbose_name='kraj'),
        ),
        migrations.AddField(
            model_name='delegation',
            name='edition',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='delegations', to='competitions.edition', verbose_name='edycja'),
        ),
        migrations.AddField(
            model_name='participant',
            name='delegation',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='students', to='accounts.delegation', verbose_name='delegacja'),
        ),
        migrations.AddField(
            model_name='delegationinvitation',
            name='accepted_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='delegationinvitation',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='delegationinvitation',
            name='delegation',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='invitations', to='accounts.delegation'),
        ),
        migrations.AddField(
            model_name='delegationleader',
            name='delegation',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='leaders', to='accounts.delegation'),
        ),
        migrations.AddField(
            model_name='delegationleader',
            name='edition',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='delegation_leaders', to='competitions.edition'),
        ),
        migrations.AddField(
            model_name='delegationleader',
            name='invited_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='zaprosił'),
        ),
        migrations.AddField(
            model_name='delegationleader',
            name='user',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='delegation_leaderships', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='consentrecord',
            name='team_leader',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='consents', to='accounts.delegationleader', verbose_name='opiekun drużyny'),
        ),
        migrations.AddIndex(
            model_name='consentrecord',
            index=models.Index(fields=['team_leader', 'kind'], name='accounts_consent_tl_idx'),
        ),
        migrations.AddConstraint(
            model_name='consentrecord',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('participant__isnull', False), ('supervisor__isnull', True), ('team_leader__isnull', True)), models.Q(('participant__isnull', True), ('supervisor__isnull', False), ('team_leader__isnull', True)), models.Q(('participant__isnull', True), ('supervisor__isnull', True), ('team_leader__isnull', False)), _connector='OR'), name='accounts_consentrecord_exactly_one_owner'),
        ),
        migrations.AddConstraint(
            model_name='delegation',
            constraint=models.UniqueConstraint(fields=('edition', 'country'), name='accounts_delegation_unique_country'),
        ),
        migrations.AddConstraint(
            model_name='delegationinvitation',
            constraint=models.UniqueConstraint(condition=models.Q(('accepted_at__isnull', True), ('revoked_at__isnull', True)), fields=('delegation', 'email'), name='accounts_delegation_invitation_one_open'),
        ),
        migrations.AddConstraint(
            model_name='delegationleader',
            constraint=models.UniqueConstraint(fields=('user', 'edition'), name='accounts_delegation_leader_one_per_edition'),
        ),
    ]
