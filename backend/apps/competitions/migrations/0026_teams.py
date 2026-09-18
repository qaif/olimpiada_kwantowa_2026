"""Drużyny: dwie puste tabele i drugi rodzaj właściciela wpisu do etapu (§ 1.2.3).

**Migracja nie zmienia ani jednego wiersza.** Konkurs #1 drużyn nie ma i flagi ``team_entries``
nie włącza, więc ``competitions_team`` i ``competitions_teammember`` powstają puste i puste
zostają, a ``competitions_stageentry.team_id`` dostaje ``NULL`` wszędzie i tak zostaje. Właściciel
każdego istniejącego wpisu pozostaje tym samym uczestnikiem, co przed wdrożeniem
(``docs/UNIWERSALNY-ETAP-2.md`` § 0.1).

Jedyna zmiana kolumny istniejącej idzie w stronę **luźniejszą**: ``competitions_stageentry
.participant_id`` traci ``NOT NULL``. Reguła z § 0.7 zakazuje zakładania ``NOT NULL`` na kolumnie
wypełnianej w tym samym wydaniu i tego właśnie tu nie ma – ``DROP NOT NULL`` nie czyta ani nie
przepisuje ani jednego wiersza, a odwrotność (``SET NOT NULL``) jest wykonalna dopóty, dopóki
żadna drużyna nie ma wpisu, czyli w każdej bazie, w której ta migracja da się cofnąć.

Znullowanie i więz ``competitions_stageentry_single_owner`` stoją w **jednej** migracji z rozmysłu:
gdyby szły osobno, istniałby stan bazy, w którym wpis wolno zapisać bez żadnego właściciela – i to
właśnie w nim wylądowałaby instalacja, której wdrożenie przerwałoby się w połowie.

``ADD COLUMN ... NULL`` bez ``DEFAULT`` w PostgreSQL 16 nie przepisuje pliku tabeli, więc wdrożenie
nie blokuje tabeli wpisów nawet przy komplecie uczestników. Wszystkie operacje są odwracalne
z definicji (``CreateModel`` → ``DROP TABLE``, ``AddField`` → ``DROP COLUMN``, ``AddConstraint`` →
``DROP CONSTRAINT``, ``AlterField`` → ``SET NOT NULL``), więc ``reverse_code`` nie ma czego opisywać.
"""

import apps.accounts.models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        # Skład drużyny wskazuje profil uczestnika, a profil ma od wydania D konkurs ``NOT NULL`` –
        # to ten konkurs porównuje ``TeamMember.clean()`` z konkursem drużyny.
        ('accounts', '0022_competition_not_null'),
        ('competitions', '0025_onsite_logistics'),
        # Drużyna ma **własną** kolumnę konkursu (jest bytem konkursu, nie rocznika), więc tabela
        # konkursów musi już stać. Zależność celowo wskazuje migrację zakładającą tabelę, a nie
        # ostatnią migrację ``tenancy``: kolejność wydań etapu 2 nie jest tu żadnym warunkiem.
        ('tenancy', '0002_competition_from_site'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TeamMember',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_captain', models.BooleanField(default=False, verbose_name='kapitan')),
            ],
            options={
                'verbose_name': 'członek drużyny',
                'verbose_name_plural': 'członkowie drużyny',
                'ordering': ('team', '-is_captain', 'id'),
            },
        ),
        migrations.AlterField(
            model_name='stageentry',
            name='participant',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='stage_entries', to='accounts.participant'),
        ),
        migrations.CreateModel(
            name='Team',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120, verbose_name='nazwa')),
                ('public_code', models.CharField(default=apps.accounts.models.generate_public_code, max_length=16, verbose_name='kod publiczny')),
                ('school', models.CharField(blank=True, max_length=255, verbose_name='szkoła')),
                ('supervisor_email', models.EmailField(blank=True, max_length=254, verbose_name='opiekun')),
                ('competition', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='teams', to='tenancy.competition', verbose_name='konkurs')),
                ('edition', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='teams', to='competitions.edition', verbose_name='edycja')),
            ],
            options={
                'verbose_name': 'drużyna',
                'verbose_name_plural': 'drużyny',
                'ordering': ('edition', 'name', 'id'),
            },
        ),
        migrations.AddField(
            model_name='stageentry',
            name='team',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='stage_entries', to='competitions.team', verbose_name='drużyna'),
        ),
        migrations.AddConstraint(
            model_name='stageentry',
            constraint=models.UniqueConstraint(condition=models.Q(('team__isnull', False)), fields=('team', 'stage'), name='competitions_stageentry_unique_team'),
        ),
        migrations.AddConstraint(
            model_name='stageentry',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('participant__isnull', False), ('team__isnull', True)), models.Q(('participant__isnull', True), ('team__isnull', False)), _connector='OR'), name='competitions_stageentry_single_owner'),
        ),
        migrations.AddField(
            model_name='teammember',
            name='participant',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='team_memberships', to='accounts.participant', verbose_name='uczestnik'),
        ),
        migrations.AddField(
            model_name='teammember',
            name='team',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='members', to='competitions.team', verbose_name='drużyna'),
        ),
        migrations.AddConstraint(
            model_name='team',
            constraint=models.UniqueConstraint(fields=('competition', 'public_code'), name='competitions_team_public_code_per_competition'),
        ),
        migrations.AddConstraint(
            model_name='team',
            constraint=models.UniqueConstraint(fields=('edition', 'name'), name='competitions_team_unique_name_per_edition'),
        ),
        migrations.AddConstraint(
            model_name='teammember',
            constraint=models.UniqueConstraint(fields=('team', 'participant'), name='competitions_teammember_unique'),
        ),
        migrations.AddConstraint(
            model_name='teammember',
            constraint=models.UniqueConstraint(condition=models.Q(('is_captain', True)), fields=('team',), name='competitions_teammember_single_captain'),
        ),
    ]
