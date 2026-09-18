"""Wpisowe: cennik konkursu i rejestr należności uczestników (§ 1.5.1).

**Migracja nie zmienia ani jednego wiersza.** Konkurs #1 jest bezpłatny: obie tabele powstają
puste, flaga ``fees`` jest domyślnie wyłączona (``apps/tenancy/models.py``, ``FEATURE_DEFAULTS``),
a żadne istniejące zapytanie do nich nie sięga. Rejestracja, oddanie pracy, przeliczenie wyników
i tabela publiczna wyglądają dokładnie tak, jak przed etapem 2 – to jest warunek z
``docs/UNIWERSALNY-ETAP-2.md`` § 0.1.

Nie ma tu ``RunPython``, backfillu ani wartości domyślnej, bo nie ma czego wypełnić: cennik jest
danymi, które organizator wpisuje sam, a „bezpłatnie” jest stanem docelowym, a nie brakiem do
uzupełnienia. Domknięcia na ``NOT NULL`` nie będzie nigdy.

Obie operacje są odwracalne z definicji (``CreateModel`` → ``DROP TABLE``, ``AddConstraint`` →
``DROP CONSTRAINT``), więc ``reverse_code`` nie ma czego opisywać (§ 0.7).

Zależności: ``competitions.0022_category`` (cennik wisi na edycji i opcjonalnie na kategorii)
oraz ``accounts.0022_competition_not_null`` (należność wisi na uczestniku, który od wydania D ma
konkurs w kolumnie ``NOT NULL``). Tabela wpisów do etapów dostaje skrót do należności osobną
migracją ``competitions.0027_stageentry_fee`` – zależną od tej, a nie odwrotnie.
"""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

import apps.tenancy.fees


class Migration(migrations.Migration):
    dependencies = [
        # Uczestnik z kolumną konkursu ``NOT NULL`` – to nią biegnie zakresowanie należności
        # (``competition_scoped_manager("participant__competition")``).
        ("accounts", "0022_competition_not_null"),
        # Edycja i kategoria: cennik wisi na roczniku, a opcjonalnie także na kategorii startowej.
        ("competitions", "0022_category"),
        ("tenancy", "0005_document_templates"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="FeeSchedule",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("name", models.CharField(max_length=120, verbose_name="nazwa")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=10, verbose_name="kwota")),
                (
                    "currency",
                    models.CharField(
                        default="PLN",
                        max_length=3,
                        validators=[apps.tenancy.fees.validate_currency],
                        verbose_name="waluta",
                    ),
                ),
                (
                    "vat_rate",
                    models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="stawka VAT (%)"),
                ),
                (
                    "due_days",
                    models.PositiveSmallIntegerField(default=14, verbose_name="termin płatności (dni)"),
                ),
                (
                    "blocks_submission",
                    models.BooleanField(default=False, verbose_name="brak wpłaty blokuje oddanie pracy"),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="aktywny")),
                (
                    "created_at",
                    models.DateTimeField(default=django.utils.timezone.now, verbose_name="utworzony"),
                ),
                (
                    "category",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fee_schedules",
                        to="competitions.category",
                        verbose_name="kategoria",
                    ),
                ),
                (
                    "competition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="fee_schedules",
                        to="tenancy.competition",
                        verbose_name="konkurs",
                    ),
                ),
                (
                    "edition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fee_schedules",
                        to="competitions.edition",
                        verbose_name="edycja",
                    ),
                ),
            ],
            options={
                "verbose_name": "cennik wpisowego",
                "verbose_name_plural": "cenniki wpisowego",
                "ordering": ("competition", "edition", "category", "id"),
            },
        ),
        migrations.CreateModel(
            name="ParticipantFee",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("amount", models.DecimalField(decimal_places=2, max_digits=10, verbose_name="kwota")),
                (
                    "currency",
                    models.CharField(
                        default="PLN",
                        max_length=3,
                        validators=[apps.tenancy.fees.validate_currency],
                        verbose_name="waluta",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("DUE", "do zapłaty"),
                            ("PAID", "zapłacone"),
                            ("EXEMPT", "zwolniony"),
                            ("WAIVED", "umorzone"),
                            ("REFUNDED", "zwrócone"),
                        ],
                        default="DUE",
                        max_length=16,
                        verbose_name="status",
                    ),
                ),
                (
                    "exemption_reason",
                    models.CharField(blank=True, max_length=200, verbose_name="powód zwolnienia"),
                ),
                ("due_on", models.DateField(blank=True, null=True, verbose_name="termin płatności")),
                ("paid_at", models.DateTimeField(blank=True, null=True, verbose_name="wpłynęło")),
                (
                    "external_reference",
                    models.CharField(blank=True, max_length=120, verbose_name="identyfikator wpłaty"),
                ),
                (
                    "document_version",
                    models.CharField(blank=True, max_length=100, verbose_name="wersja dokumentu"),
                ),
                (
                    "document_issued_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="dokument wydano"),
                ),
                (
                    "created_at",
                    models.DateTimeField(default=django.utils.timezone.now, verbose_name="naliczono"),
                ),
                (
                    "participant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="fees",
                        to="accounts.participant",
                        verbose_name="uczestnik",
                    ),
                ),
                (
                    "recorded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="fees_recorded",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="zapisał",
                    ),
                ),
                (
                    "schedule",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="fees",
                        to="tenancy.feeschedule",
                        verbose_name="cennik",
                    ),
                ),
            ],
            options={
                "verbose_name": "należność uczestnika",
                "verbose_name_plural": "należności uczestników",
                "ordering": ("-created_at", "id"),
            },
        ),
        migrations.AddConstraint(
            model_name="feeschedule",
            constraint=models.CheckConstraint(
                condition=models.Q(("amount__gte", 0)), name="tenancy_feeschedule_amount_not_negative"
            ),
        ),
        migrations.AddConstraint(
            model_name="feeschedule",
            constraint=models.CheckConstraint(
                condition=models.Q(("vat_rate__isnull", True), ("vat_rate__lte", 100), _connector="OR"),
                name="tenancy_feeschedule_vat_rate_percent",
            ),
        ),
        migrations.AddConstraint(
            model_name="feeschedule",
            constraint=models.UniqueConstraint(
                fields=("edition", "category"), name="tenancy_feeschedule_unique_per_category"
            ),
        ),
        migrations.AddConstraint(
            model_name="feeschedule",
            constraint=models.UniqueConstraint(
                condition=models.Q(("category__isnull", True)),
                fields=("edition",),
                name="tenancy_feeschedule_unique_base",
            ),
        ),
        migrations.AddConstraint(
            model_name="participantfee",
            constraint=models.UniqueConstraint(
                fields=("schedule", "participant"), name="tenancy_participantfee_unique"
            ),
        ),
        migrations.AddConstraint(
            model_name="participantfee",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("status", "PAID"), _negated=True), ("paid_at__isnull", False), _connector="OR"
                ),
                name="tenancy_participantfee_paid_has_date",
            ),
        ),
        migrations.AddConstraint(
            model_name="participantfee",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("status", "EXEMPT"), _negated=True),
                    models.Q(("exemption_reason", ""), _negated=True),
                    _connector="OR",
                ),
                name="tenancy_participantfee_exempt_has_reason",
            ),
        ),
        migrations.AddConstraint(
            model_name="participantfee",
            constraint=models.CheckConstraint(
                condition=models.Q(("amount__gte", 0)), name="tenancy_participantfee_amount_not_negative"
            ),
        ),
    ]
