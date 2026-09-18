"""Nazwane role recenzenckie etapu (``docs/UNIWERSALNY-ETAP-2.md`` § 1.2.7).

**Migracja nie zmienia ani jednego wiersza.** ``grading_reviewerrole`` powstaje pusta i pusta
zostaje: Konkurs #1 nie włącza flagi ``reviewer_roles``, a migracja nie wpisuje ról **żadnemu**
etapowi – także w konkursie, który flagę włączy. Rolę wpisuje organizator, bo to on wie, czy jego
etap ma „pierwszego i drugiego”, czy „dwóch recenzentów i arbitra”.

``grading_review.role_id`` dostaje ``NULL`` wszędzie i tak zostaje. ``Review.round`` **nie zmienia
się ani o jotę** i pozostaje autorytatywne: rola jest etykietą nad rundą, a nie zamiast niej, więc
maszyna stanów oceniania (rozjazd, runda rozjemcza, moderacja) czyta dokładnie to samo pole, co
przed wdrożeniem.

``ADD COLUMN ... NULL`` bez ``DEFAULT`` w PostgreSQL 16 nie przepisuje pliku tabeli, więc wdrożenie
nie blokuje tabeli recenzji nawet przy komplecie prac w ocenianiu. Obie operacje są odwracalne
z definicji (``CreateModel`` → ``DROP TABLE``, ``AddField`` → ``DROP COLUMN``), a cofnięcie nie
traci żadnej informacji, bo przed pierwszym wpisem organizatora nie ma tu czego stracić.

Zależność wskazuje migrację zakładającą ``competitions.Stage``, a nie czoło tamtej aplikacji –
z tego samego powodu, co w ``grading.0003``: testy migracji przewijają sąsiednie aplikacje wstecz.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("competitions", "0001_initial"),
        ("grading", "0009_comment_snippet_competition"),
    ]

    operations = [
        migrations.CreateModel(
            name="ReviewerRole",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("code", models.SlugField(max_length=24, verbose_name="kod")),
                ("name", models.CharField(max_length=80, verbose_name="nazwa")),
                ("round", models.PositiveSmallIntegerField(default=1, verbose_name="runda")),
                ("count", models.PositiveSmallIntegerField(default=1, verbose_name="liczba recenzentów")),
                (
                    "counts_towards_consensus",
                    models.BooleanField(default=True, verbose_name="liczy się do zgodności"),
                ),
                ("position", models.PositiveSmallIntegerField(default=0, verbose_name="kolejność")),
                (
                    "stage",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reviewer_roles",
                        to="competitions.stage",
                        verbose_name="etap",
                    ),
                ),
            ],
            options={
                "verbose_name": "rola recenzencka",
                "verbose_name_plural": "role recenzenckie",
                "ordering": ("stage", "position", "id"),
            },
        ),
        migrations.AddConstraint(
            model_name="reviewerrole",
            constraint=models.UniqueConstraint(
                fields=("stage", "code"), name="grading_reviewerrole_unique_code"
            ),
        ),
        migrations.AddConstraint(
            model_name="reviewerrole",
            constraint=models.CheckConstraint(
                condition=models.Q(round__gte=1), name="grading_reviewerrole_round_positive"
            ),
        ),
        migrations.AddConstraint(
            model_name="reviewerrole",
            constraint=models.CheckConstraint(
                condition=models.Q(count__gte=1), name="grading_reviewerrole_count_positive"
            ),
        ),
        migrations.AddField(
            model_name="review",
            name="role",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="reviews",
                to="grading.reviewerrole",
                verbose_name="rola",
            ),
        ),
    ]
