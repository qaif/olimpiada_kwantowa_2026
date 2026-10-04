"""Poprawki po przeglądzie DEL-01: odwołanie opiekuna jako znacznik i ślad wypisania ucznia.

``DelegationLeader.removed_at`` – odwołany opiekun zostaje wierszem (dowody jego zgód wiszą na nim);
więz „jedna delegacja na osobę w edycji” obejmuje wyłącznie wiersze czynne.
``Participant.former_delegation``/``delegation_unlinked_at`` – uczeń z uruchomionym kontem wypisany
przez opiekuna zostaje uczestnikiem, a koordynator widzi, skąd wypadł. Nowe kolumny są nullowalne.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0037_team_leader_rbac_group'),
        ('competitions', '0033_video_room'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='delegationleader',
            name='accounts_delegation_leader_one_per_edition',
        ),
        migrations.AddField(
            model_name='delegationleader',
            name='removed_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='odwołany'),
        ),
        migrations.AddField(
            model_name='participant',
            name='delegation_unlinked_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='kiedy wypisany z delegacji'),
        ),
        migrations.AddField(
            model_name='participant',
            name='former_delegation',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='unlinked_students', to='accounts.delegation', verbose_name='wypisany z delegacji'),
        ),
        migrations.AddConstraint(
            model_name='delegationleader',
            constraint=models.UniqueConstraint(condition=models.Q(('removed_at__isnull', True)), fields=('user', 'edition'), name='accounts_delegation_leader_one_active_per_edition'),
        ),
    ]
