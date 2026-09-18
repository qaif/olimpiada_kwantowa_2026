"""Wydanie K: poświadczenie dostawcy płatności i dziennik doręczeń przychodzących (§ 1.5.1).

Dwie **nowe, puste** tabele i ani jednej zmiany w tabelach istniejących. Konkurs #1 nie włącza flagi
``fees``, więc nie założy poświadczenia żadnemu dostawcy, a webhook odpowiada mu 404 – migracja jest
dla niego przyrostem dwóch pustych relacji w katalogu bazy i niczym więcej (§ 0.1).

Odwracalna z definicji (``CreateModel``), bez danych do przepisania i bez ``NOT NULL`` na kolumnie
wypełnianej w tym samym wydaniu (§ 0.7). Zależność od ``tenancy.0006_fees`` jest twarda:
``PaymentEvent.fee`` wskazuje na ``ParticipantFee``, czyli na rejestr, do którego ten dziennik
dopisuje potwierdzenia.
"""

import apps.integrations.models
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("integrations", "0002_competition_on_keys_and_endpoints"),
        ("tenancy", "0006_fees"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PaymentEndpoint",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "provider",
                    models.SlugField(
                        help_text="Identyfikator w adresie webhooka.", max_length=40, verbose_name="dostawca"
                    ),
                ),
                (
                    "secret",
                    models.CharField(
                        default=apps.integrations.models.generate_secret,
                        max_length=64,
                        verbose_name="sekret podpisu",
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="aktywny")),
                (
                    "created_at",
                    models.DateTimeField(default=django.utils.timezone.now, verbose_name="dodany"),
                ),
                ("rotated_at", models.DateTimeField(blank=True, null=True, verbose_name="sekret wymieniony")),
                (
                    "last_event_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="ostatnie doręczenie"),
                ),
                (
                    "competition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="payment_endpoints",
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
                        related_name="payment_endpoints",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="dodał",
                    ),
                ),
            ],
            options={
                "verbose_name": "webhook płatności",
                "verbose_name_plural": "webhooki płatności",
                "ordering": ("competition", "provider"),
            },
        ),
        migrations.CreateModel(
            name="PaymentEvent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("delivery_key", models.CharField(max_length=120, verbose_name="klucz doręczenia")),
                (
                    "external_reference",
                    models.CharField(blank=True, max_length=120, verbose_name="identyfikator wpłaty"),
                ),
                ("payload_hash", models.CharField(max_length=64, verbose_name="skrót ładunku")),
                ("matched", models.BooleanField(default=False, verbose_name="dopasowane")),
                (
                    "unmatched_reason",
                    models.CharField(blank=True, max_length=40, verbose_name="powód niedopasowania"),
                ),
                (
                    "received_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now, verbose_name="odebrane"
                    ),
                ),
                (
                    "endpoint",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="events",
                        to="integrations.paymentendpoint",
                        verbose_name="webhook",
                    ),
                ),
                (
                    "fee",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="payment_events",
                        to="tenancy.participantfee",
                        verbose_name="należność",
                    ),
                ),
            ],
            options={
                "verbose_name": "doręczenie płatności",
                "verbose_name_plural": "doręczenia płatności",
                "ordering": ("-received_at", "-id"),
            },
        ),
        migrations.AddConstraint(
            model_name="paymentendpoint",
            constraint=models.UniqueConstraint(
                fields=("competition", "provider"), name="integrations_paymentendpoint_unique"
            ),
        ),
        migrations.AddIndex(
            model_name="paymentevent",
            index=models.Index(fields=["endpoint", "-received_at"], name="integrations_payevent_idx"),
        ),
        migrations.AddConstraint(
            model_name="paymentevent",
            constraint=models.UniqueConstraint(
                fields=("endpoint", "delivery_key"), name="integrations_paymentevent_unique_delivery"
            ),
        ),
    ]
