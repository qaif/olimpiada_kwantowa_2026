# Automatyczne przypomnienia (MSG-AUTO-01) – dwie nowe tabele, bez zmian w innych aplikacjach.

import datetime

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("accounts", "0042_user_blocked_at"),
        ("competitions", "0034_video_provider_livekit"),
        ("tenancy", "0017_document_kind_choices_merged"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MessageRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, verbose_name="nazwa")),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("WORKSHOP", "przypomnienie o warsztacie"),
                            ("STAGE_DEADLINE", "przypomnienie przed terminem etapu"),
                        ],
                        max_length=16,
                        verbose_name="rodzaj",
                    ),
                ),
                ("enabled", models.BooleanField(default=True, verbose_name="włączona")),
                ("group", models.CharField(max_length=32, verbose_name="grupa odbiorców")),
                ("parameters", models.JSONField(blank=True, default=dict, verbose_name="parametry grupy")),
                ("target", models.JSONField(blank=True, default=dict, verbose_name="opis grupy")),
                ("subject_template", models.CharField(max_length=200, verbose_name="szablon tematu")),
                ("body_template", models.TextField(verbose_name="szablon treści")),
                ("link", models.URLField(blank=True, max_length=500, verbose_name="link")),
                (
                    "timing",
                    models.CharField(
                        choices=[
                            ("DAY_AT", "N dni przed, o wybranej godzinie"),
                            ("HOURS_BEFORE", "N godzin przed początkiem"),
                        ],
                        default="DAY_AT",
                        max_length=16,
                        verbose_name="moment wysyłki",
                    ),
                ),
                ("days_before", models.JSONField(blank=True, default=list, verbose_name="dni przed")),
                ("send_time", models.TimeField(default=datetime.time(6, 0), verbose_name="godzina wysyłki")),
                ("hours_before", models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="godzin przed")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="utworzona")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="zmieniona")),
                ("last_run_at", models.DateTimeField(blank=True, null=True, verbose_name="ostatni przebieg")),
                ("last_error", models.CharField(blank=True, max_length=500, verbose_name="ostatni błąd")),
                (
                    "competition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="message_rules",
                        to="tenancy.competition",
                        verbose_name="konkurs",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="autor",
                    ),
                ),
                (
                    "stage",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="message_rules",
                        to="competitions.stage",
                        verbose_name="etap (termin)",
                    ),
                ),
            ],
            options={
                "verbose_name": "reguła przypomnienia",
                "verbose_name_plural": "reguły przypomnień",
                "ordering": ("name", "id"),
            },
        ),
        migrations.CreateModel(
            name="MessageRuleOccurrence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=200, verbose_name="klucz wystąpienia")),
                ("send_at", models.DateTimeField(verbose_name="termin wysyłki")),
                ("fingerprint", models.CharField(max_length=64, verbose_name="odcisk")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="wygenerowano")),
                (
                    "broadcast",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="accounts.messagebroadcast",
                        verbose_name="komunikat",
                    ),
                ),
                (
                    "rule",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="occurrences",
                        to="message_rules.messagerule",
                    ),
                ),
            ],
            options={
                "verbose_name": "wystąpienie reguły",
                "verbose_name_plural": "wystąpienia reguł",
                "constraints": [
                    models.UniqueConstraint(fields=("rule", "key"), name="message_rule_occurrence_unique_key")
                ],
            },
        ),
    ]
