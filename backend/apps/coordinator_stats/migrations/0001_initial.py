"""Liczniki wysyłki komunikatów po grupach domen i odbicia przypisane komunikatom (STATS-02 § 2).

Dwie nowe tabele bez danych – wiersze dopisuje porcja wysyłki i odbiornik odbić. Odwracalna.
"""

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

DOMAIN_GROUPS = [
    ("gmail", "Gmail"),
    ("portals", "WP / O2 / Onet / Interia"),
    ("microsoft", "Microsoft (Outlook, Hotmail, Live)"),
    ("school", "szkolne (edu)"),
    ("other", "inne"),
]


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("accounts", "0042_user_blocked_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="BroadcastDomainTally",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "domain_group",
                    models.CharField(choices=DOMAIN_GROUPS, max_length=16, verbose_name="grupa domen"),
                ),
                ("queued", models.PositiveIntegerField(default=0, verbose_name="zakolejkowane")),
                (
                    "suppressed",
                    models.PositiveIntegerField(default=0, verbose_name="pominięte (adres niedoręczalny)"),
                ),
                ("bounced_hard", models.PositiveIntegerField(default=0, verbose_name="odbite – adres nie istnieje")),
                ("bounced_policy", models.PositiveIntegerField(default=0, verbose_name="odbite – polityka serwera")),
                ("bounced_soft", models.PositiveIntegerField(default=0, verbose_name="odbite – tymczasowe i inne")),
                ("bounced_s3150", models.PositiveIntegerField(default=0, verbose_name="w tym Microsoft S3150")),
                (
                    "broadcast",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="domain_tallies",
                        to="accounts.messagebroadcast",
                        verbose_name="komunikat",
                    ),
                ),
            ],
            options={
                "verbose_name": "liczniki komunikatu w grupie domen",
                "verbose_name_plural": "liczniki komunikatów w grupach domen",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("broadcast", "domain_group"), name="coordinator_stats_tally_unique"
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="BroadcastBounce",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.CharField(max_length=254, verbose_name="adres e-mail (małe litery)")),
                (
                    "domain_group",
                    models.CharField(choices=DOMAIN_GROUPS, max_length=16, verbose_name="grupa domen"),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("hard", "adres nie istnieje"),
                            ("policy", "odrzucone przez politykę serwera odbiorcy"),
                            ("soft", "tymczasowe i inne"),
                        ],
                        max_length=8,
                        verbose_name="rodzaj",
                    ),
                ),
                ("status_code", models.CharField(blank=True, max_length=16, verbose_name="kod stanu (RFC 3463)")),
                ("reason", models.CharField(blank=True, max_length=300, verbose_name="powód (skrócony)")),
                ("microsoft_s3150", models.BooleanField(default=False, verbose_name="Microsoft S3150")),
                ("source", models.CharField(blank=True, max_length=8, verbose_name="źródło")),
                (
                    "created_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now, verbose_name="pierwsze odbicie"
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="ostatnie odbicie")),
                (
                    "broadcast",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reported_bounces",
                        to="accounts.messagebroadcast",
                        verbose_name="komunikat",
                    ),
                ),
            ],
            options={
                "verbose_name": "odbicie listu komunikatu",
                "verbose_name_plural": "odbicia listów komunikatów",
                "ordering": ("kind", "email"),
                "indexes": [models.Index(fields=["email"], name="coordinator_stats_bounce_email")],
                "constraints": [
                    models.UniqueConstraint(fields=("broadcast", "email"), name="coordinator_stats_bounce_unique")
                ],
            },
        ),
    ]
