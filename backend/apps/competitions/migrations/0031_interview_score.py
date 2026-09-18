"""Punkty z rozmowy jako tabela (``docs/UNIWERSALNY-ETAP-2.md`` § 1.2.3, decyzja organizatora D9).

Sama tabela, **pusta i bez ani jednego wiersza dla Konkursu #1**. Migracja nie przepisuje niczego
z ``/admin/`` i niczego nie wylicza: dzisiejsze punkty z rozmów Olimpiady Kwantowej leżą tam, gdzie
leżały, a etap bez komponentu ``INTERVIEW`` liczy się dokładnie tak, jak liczył przed etapem 2
(§ 0.1, § 5.2). Czytelnikiem tabeli jest ``results.services._component_sources``, a ten pyta o nią
dopiero wtedy, gdy konkurs ma flagę ``process_editor``, a etap – komponent rozmowy.

Odwracalna z definicji – cofnięcie usuwa pustą tabelę i nie ma czego stracić. Nie zmienia ani
jednego pola ``Stage``, ``StageEntry``, ``StageComponent``, ``InterviewSlot`` ani
``InterviewBooking`` i nie włącza żadnej flagi.
"""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("competitions", "0030_tie_break"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="InterviewScore",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("points", models.PositiveSmallIntegerField(verbose_name="punkty")),
                ("max_points", models.PositiveSmallIntegerField(verbose_name="maksimum")),
                ("note", models.CharField(blank=True, max_length=200, verbose_name="uwaga komisji")),
                (
                    "recorded_at",
                    models.DateTimeField(default=django.utils.timezone.now, verbose_name="wpisane"),
                ),
                (
                    "component",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="interview_scores",
                        to="competitions.stagecomponent",
                        verbose_name="komponent",
                    ),
                ),
                (
                    "entry",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="interview_scores",
                        to="competitions.stageentry",
                        verbose_name="wpis do etapu",
                    ),
                ),
                (
                    "recorded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="interview_scores",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="wpisał",
                    ),
                ),
            ],
            options={
                "verbose_name": "punkty z rozmowy",
                "verbose_name_plural": "punkty z rozmów",
                "ordering": ("component", "entry", "id"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("entry", "component"), name="competitions_interviewscore_unique"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("points__lte", models.F("max_points"))),
                        name="competitions_interviewscore_points_within_max",
                    ),
                ],
            },
        ),
    ]
