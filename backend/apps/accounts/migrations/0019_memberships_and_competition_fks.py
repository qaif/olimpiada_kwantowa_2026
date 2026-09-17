"""Schemat wydania B: właściciel konkursu przy profilach kont oraz tabela członkostw.

Sam schemat, bez jednego wiersza danych – backfill jest osobną migracją (``0020``). Podział jest
celowy: kolumny da się dołożyć bez blokowania tabeli (``ADD COLUMN ... NULL`` w PostgreSQL nie
przepisuje pliku), a przepisanie danych ma być operacją, którą widać w logu wdrożenia pod własną
nazwą i którą da się przetestować osobno, na bazie pełnej i na pustej.

**Wszystkie kolumny ``competition`` są nullowalne i to jest stan przejściowy, nie projekt.**
Wydanie B kładzie schemat **przed** kodem, który go wymaga, żeby stara i nowa wersja aplikacji
mogły przez chwilę stać obok siebie – to jest warunek wdrożenia bez przestoju
(``docs/UNIWERSALNY-ETAP-1.md`` § 4.1). Domknięcie na ``NOT NULL`` wchodzi w wydaniu D, po
zapytaniu kontrolnym, które przerywa wdrożenie, gdy znajdzie choć jeden wiersz bez właściciela
(§ 4.4).

Czego ta migracja **nie** robi: nie rusza ``Participant.user`` (nadal ``OneToOne``), nie zdejmuje
globalnego ``unique`` z ``public_code``, nie kasuje grup RBAC ani uprawnień grupy ``coordinator``
w ``/cms/``. Każda z tych zmian jest widoczna dla użytkownika i należy do wydania D.
"""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0018_twofactor_device"),
        # Konkurs musi istnieć jako tabela, zanim wskażą go klucze obce. ``0002`` (a nie ``0001``),
        # bo backfill w ``0020`` liczy na to, że Konkurs #1 jest już w bazie.
        ("tenancy", "0002_competition_from_site"),
    ]

    operations = [
        migrations.AddField(
            model_name="committeemember",
            name="competition",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="committee_members",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
        migrations.AddField(
            model_name="invitationcode",
            name="competition",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="invitation_codes",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
        migrations.AddField(
            model_name="messagebroadcast",
            name="competition",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="broadcasts",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
        migrations.AddField(
            model_name="participant",
            name="competition",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="participants",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
        migrations.AddField(
            model_name="schoolsupervisor",
            name="competition",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="school_supervisors",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
        migrations.CreateModel(
            name="Membership",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("participant", "uczestnik"),
                            ("reviewer", "recenzent"),
                            ("appeals", "komisja odwoławcza"),
                            ("coordinator", "koordynator"),
                            ("supervisor", "opiekun szkolny"),
                        ],
                        max_length=16,
                        verbose_name="rola",
                    ),
                ),
                (
                    "granted_at",
                    models.DateTimeField(default=django.utils.timezone.now, verbose_name="nadana"),
                ),
                (
                    "competition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="memberships",
                        to="tenancy.competition",
                        verbose_name="konkurs",
                    ),
                ),
                (
                    "granted_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="memberships_granted",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="nadał",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="memberships",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "członkostwo",
                "verbose_name_plural": "członkostwa",
                "ordering": ("competition", "user", "role"),
                "indexes": [models.Index(fields=["user", "competition"], name="accounts_membership_uc_idx")],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("user", "competition", "role"), name="accounts_membership_unique"
                    )
                ],
            },
        ),
    ]
