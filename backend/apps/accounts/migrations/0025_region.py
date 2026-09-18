"""Podział terytorialny konkursu: nowa tabela i trzy nullowalne kolumny (§ 1.4.2).

**Migracja nie zmienia ani jednego istniejącego wiersza i nie dotyka ``district``.** Powstaje pusta
tabela ``accounts_region`` oraz kolumny ``region_id`` w ``accounts_participant``,
``accounts_committeemember`` i ``accounts_invitationcode`` – wszystkie trzy **nullowalne**, zgodnie
z regułą „nullowalne → backfill → osobne wydanie z ``NOT NULL``” (§ 0.7). Tu żadne ``NOT NULL`` nie
wchodzi ani teraz, ani później: konkurs bez własnego podziału terytorialnego ma te kolumny puste
i to jest stan poprawny.

``Region.competition`` i ``Region.parent`` są ``CASCADE`` (słownik i jego drzewo są konfiguracją
samego konkursu), a trzy kolumny ``region`` w profilach są ``PROTECT`` – konkurs z profilami
zatrzyma się więc na nich, czyli tam, gdzie stoją cudze dane, a nie na własnym słowniku.

``Voivodeship``, ``normalize_voivodeship``, ``Participant.district`` i ``School.voivodeship``
zostają nietknięte. Wiersze do nowej tabeli wpisuje dopiero migracja danych ``0026``, a czyta je
wyłącznie konkurs z włączoną flagą ``custom_regions`` – Konkurs #1 jej nie włącza, więc formularze,
filtry, eksporty i reguła konfliktu interesów dalej patrzą na ``district``.

Rozdział na dwie migracje (schemat osobno, dane osobno) jest regułą etapu 1 § 4.2: ``RunPython``
zakładający regiony i wypełniający kolumny wolno **powtórzyć** i wolno **cofnąć** niezależnie od
tego, czy tabela ma zostać.

``CreateModel`` i ``AddField`` kolumny nullowalnej są odwracalne z definicji, więc ``reverse_code``
nie ma czego opisywać.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0024_consent_definitions_from_the_constant"),
        # Region ma **własną** kolumnę konkursu (podział jest konfiguracją konkursu, nie edycji),
        # więc tabela konkursów musi już stać. Wskazujemy najstarszą migrację, która ją ma – nie
        # czoło aplikacji ``tenancy``, żeby nie wiązać tego wydania z niczym, czego nie potrzebuje.
        ("tenancy", "0004_competition_site_alias"),
    ]

    operations = [
        migrations.CreateModel(
            name="Region",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("code", models.SlugField(max_length=40, verbose_name="kod")),
                ("name", models.CharField(max_length=120, verbose_name="nazwa")),
                (
                    "level",
                    models.CharField(
                        choices=[
                            ("COUNTRY", "kraj"),
                            ("REGION", "region"),
                            ("COUNTY", "podregion"),
                        ],
                        default="REGION",
                        max_length=16,
                        verbose_name="poziom",
                    ),
                ),
                ("position", models.PositiveSmallIntegerField(default=0, verbose_name="kolejność")),
                ("is_active", models.BooleanField(default=True, verbose_name="aktywny")),
                (
                    "counts_for_conflict",
                    models.BooleanField(default=True, verbose_name="liczy się do konfliktu"),
                ),
                (
                    "competition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="regions",
                        to="tenancy.competition",
                        verbose_name="konkurs",
                    ),
                ),
                (
                    "parent",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="children",
                        to="accounts.region",
                        verbose_name="nadrzędny",
                    ),
                ),
            ],
            options={
                "verbose_name": "region",
                "verbose_name_plural": "regiony",
                "ordering": ("competition", "position", "name", "id"),
            },
        ),
        migrations.AddField(
            model_name="committeemember",
            name="region",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="committee_members",
                to="accounts.region",
                verbose_name="region",
            ),
        ),
        migrations.AddField(
            model_name="invitationcode",
            name="region",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="invitation_codes",
                to="accounts.region",
                verbose_name="region",
            ),
        ),
        migrations.AddField(
            model_name="participant",
            name="region",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="participants",
                to="accounts.region",
                verbose_name="region",
            ),
        ),
        migrations.AddConstraint(
            model_name="region",
            constraint=models.UniqueConstraint(
                fields=("competition", "code"), name="accounts_region_unique_code"
            ),
        ),
    ]
