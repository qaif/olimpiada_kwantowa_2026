"""Skład komisji przez model pośredniczący z ``PROTECT`` + status ``IN_REVIEW`` poza wyborem.

Django nie potrafi „doszyć” ``through=`` do istniejącego pola M2M (``AlterField`` na M2M z auto
utworzonej tabeli na jawny model kończy się ``ValueError``), więc pole jest tu zdejmowane i dodawane
z powrotem. Kolejność operacji jest dobrana tak, żeby **nie zgubić danych**: nowa tabela powstaje
i przejmuje wiersze *zanim* stara zostanie skasowana.

Migracja w tył odtwarza automatyczną tabelę M2M, ale już pustą – przenoszenie danych w drugą stronę
nie ma sensu, skoro zaraz potem znika model pośredniczący. To świadoma jednokierunkowość.
"""

import django.db.models.deletion
from django.db import migrations, models


def copy_committee_to_through(apps, schema_editor):
    """Przepisuje istniejące składy komisji z automatycznej tabeli M2M do modelu pośredniczącego."""
    AppealDecision = apps.get_model("appeals", "AppealDecision")
    Seat = apps.get_model("appeals", "AppealDecisionCommitteeMember")
    seats = [
        Seat(decision_id=decision_id, member_id=member_id)
        for decision_id, member_id in AppealDecision.committee.through.objects.values_list(
            "appealdecision_id", "committeemember_id"
        )
    ]
    Seat.objects.bulk_create(seats, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_committeemember_district_verified_and_more"),
        ("appeals", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="appeal",
            name="status",
            field=models.CharField(
                choices=[
                    ("OPEN", "złożona"),
                    ("REJECTED", "odrzucona"),
                    ("ACCEPTED", "uwzględniona"),
                    ("PARTIALLY_ACCEPTED", "częściowo uwzględniona"),
                ],
                default="OPEN",
                max_length=24,
                verbose_name="status",
            ),
        ),
        migrations.CreateModel(
            name="AppealDecisionCommitteeMember",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "decision",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="committee_seats",
                        to="appeals.appealdecision",
                        verbose_name="decyzja",
                    ),
                ),
                (
                    "member",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="appeal_decision_seats",
                        to="accounts.committeemember",
                        verbose_name="członek komisji",
                    ),
                ),
            ],
            options={
                "verbose_name": "członek składu decyzji",
                "verbose_name_plural": "skład decyzji",
                "ordering": ("decision", "member"),
            },
        ),
        migrations.AddConstraint(
            model_name="appealdecisioncommitteemember",
            constraint=models.UniqueConstraint(
                fields=("decision", "member"), name="appeals_decision_member_unique"
            ),
        ),
        migrations.RunPython(copy_committee_to_through, migrations.RunPython.noop),
        migrations.RemoveField(model_name="appealdecision", name="committee"),
        migrations.AddField(
            model_name="appealdecision",
            name="committee",
            field=models.ManyToManyField(
                blank=True,
                related_name="appeal_decisions",
                through="appeals.AppealDecisionCommitteeMember",
                to="accounts.committeemember",
                verbose_name="skład komisji",
            ),
        ),
    ]
