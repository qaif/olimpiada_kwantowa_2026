"""Definicje zgód per konkurs: nowa tabela (``docs/UNIWERSALNY-ETAP-2.md`` § 1.1.2).

**Migracja nie zmienia ani jednego istniejącego wiersza i nie dotyka ``ConsentRecord``.** Powstaje
pusta tabela ``accounts_consentdefinition``; wiersze wpisuje dopiero migracja danych ``0024``,
a czyta je wyłącznie konkurs z włączoną flagą ``per_competition_consents`` – Konkurs #1 jej nie
włącza (decyzja organizatora D8), więc formularz rejestracji, ``GET /api/auth/consents/`` i panel
uczestnika dalej biorą zestaw ze stałej ``apps.accounts.consents.DEFAULT_CONSENTS``.

Rozdział na dwie migracje (schemat osobno, dane osobno) jest regułą etapu 1 § 4.2 i ma tu bardzo
konkretny powód: ``RunPython`` wpisujący treść czterech zgód wolno **powtórzyć** i wolno **cofnąć**
niezależnie od tego, czy tabela ma zostać.

Trzy więzy, każdy z powodem:

- ``accounts_consentdef_unique_kind`` – jeden konkurs ma najwyżej jedną zgodę danego rodzaju.
  Dwie znaczyłyby, że ``ConsentRecord.kind`` nie identyfikuje już treści, pod którą ktoś się
  podpisał;
- ``accounts_consentdef_unique_field`` – nazwa pola jest kluczem w formularzu i w serializerze;
  zdublowana dałaby zgodę zapisaną pod niewłaściwym rodzajem;
- ``accounts_consentdef_version_not_empty`` – zgoda bez wersji nie jest dowodem.

``CreateModel`` jest odwracalne z definicji (``DROP TABLE``), więc ``reverse_code`` nie ma czego
opisywać.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0022_competition_not_null"),
        # Definicja zgody ma **własną** kolumnę konkursu (jest konfiguracją konkursu, nie edycji),
        # więc tabela konkursów musi już stać.
        ("tenancy", "0004_competition_site_alias"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConsentDefinition",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("TERMS", "akceptacja regulaminu"),
                            ("PRIVACY", "przetwarzanie danych osobowych"),
                            ("GUARDIAN", "zgoda rodzica lub opiekuna prawnego"),
                            ("PUBLISH_NAME", "publikacja imienia i nazwiska"),
                        ],
                        max_length=24,
                        verbose_name="rodzaj",
                    ),
                ),
                ("field_name", models.CharField(max_length=40, verbose_name="nazwa pola")),
                ("text", models.TextField(verbose_name="treść oświadczenia")),
                (
                    "link_text",
                    models.CharField(blank=True, max_length=200, verbose_name="tekst odnośnika"),
                ),
                (
                    "document_slug",
                    models.SlugField(blank=True, max_length=60, verbose_name="dokument"),
                ),
                ("version", models.CharField(max_length=100, verbose_name="wersja dokumentu")),
                ("required", models.BooleanField(default=False, verbose_name="wymagana zawsze")),
                (
                    "required_for_minor",
                    models.BooleanField(default=False, verbose_name="wymagana dla niepełnoletnich"),
                ),
                (
                    "help_text",
                    models.CharField(blank=True, max_length=200, verbose_name="podpowiedź"),
                ),
                (
                    "missing_message",
                    models.CharField(blank=True, max_length=300, verbose_name="komunikat o braku"),
                ),
                (
                    "ordering",
                    models.PositiveSmallIntegerField(default=0, verbose_name="kolejność"),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="aktywna")),
                (
                    "competition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="consent_definitions",
                        to="tenancy.competition",
                        verbose_name="konkurs",
                    ),
                ),
            ],
            options={
                "verbose_name": "definicja zgody",
                "verbose_name_plural": "definicje zgód",
                "ordering": ("competition", "ordering", "id"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("competition", "kind"), name="accounts_consentdef_unique_kind"
                    ),
                    models.UniqueConstraint(
                        fields=("competition", "field_name"),
                        name="accounts_consentdef_unique_field",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("version", ""), _negated=True),
                        name="accounts_consentdef_version_not_empty",
                    ),
                ],
            },
        ),
    ]
