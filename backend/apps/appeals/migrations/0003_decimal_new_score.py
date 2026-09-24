"""Nowa punktacja z reklamacji jako ``numeric(7, 2)`` (wydanie 0.35.0).

Komisja odwoławcza wpisuje punkty do tej samej ``FinalGrade``, co recenzent, więc w etapie
z dowolnymi wartościami może przyznać 4,25 – kolumna decyzji musi to umieć zapisać. Rzutowanie
z ``smallint`` jest bezstratne; więz „nie mniej niż zero” (wcześniej z typu ``Positive*``) stoi
jawnie. Zależność wyłącznie od poprzedniej migracji tej aplikacji (patrz ``competitions.0032``).
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("appeals", "0002_appeal_decision_committee_through"),
    ]

    operations = [
        migrations.AlterField(
            model_name="appealdecision",
            name="new_score",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=7, null=True, verbose_name="nowa punktacja"
            ),
        ),
        migrations.AddConstraint(
            model_name="appealdecision",
            constraint=models.CheckConstraint(
                condition=models.Q(("new_score__isnull", True), ("new_score__gte", 0), _connector="OR"),
                name="appeals_appealdecision_new_score_non_negative",
            ),
        ),
    ]
