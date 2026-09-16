"""Zgłoszenia do organizatora: sprawa (``SupportTicket``) i wypowiedzi w jej wątku.

Constraint ``support_ticket_has_reply_address`` jest ostatnią linią obrony przed sprawą, na którą
nie da się odpowiedzieć: zgłoszenie musi mieć konto **albo** wpisany adres e-mail. Formularz
pilnuje tego samego, ale zapis z pominięciem serwisu (komenda, skrypt migracyjny) nie ma prawa
zostawić w kolejce wiersza bez adresu zwrotnego.
"""

import apps.support.models
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SupportTicket",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "email",
                    models.EmailField(
                        blank=True, max_length=254, verbose_name="adres e-mail (zgłoszenie bez konta)"
                    ),
                ),
                (
                    "role_snapshot",
                    models.CharField(blank=True, max_length=40, verbose_name="rola w chwili zgłoszenia"),
                ),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("ACCOUNT", "konto i logowanie"),
                            ("SUBMISSION", "wysyłka pracy"),
                            ("RESULTS", "wyniki"),
                            ("REGISTRATION", "rejestracja"),
                            ("OTHER", "inne"),
                        ],
                        default="OTHER",
                        max_length=16,
                        verbose_name="kategoria",
                    ),
                ),
                ("subject", models.CharField(max_length=200, verbose_name="temat")),
                (
                    "context",
                    models.JSONField(
                        blank=True,
                        default=apps.support.models.default_context,
                        verbose_name="kontekst techniczny",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("OPEN", "otwarte"), ("ANSWERED", "odpowiedziane"), ("CLOSED", "zamknięte")],
                        default="OPEN",
                        max_length=16,
                        verbose_name="status",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(default=django.utils.timezone.now, verbose_name="zgłoszone"),
                ),
                ("answered_at", models.DateTimeField(blank=True, null=True, verbose_name="odpowiedziano")),
                ("closed_at", models.DateTimeField(blank=True, null=True, verbose_name="zamknięte")),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="support_tickets",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="zgłaszający",
                    ),
                ),
            ],
            options={
                "verbose_name": "zgłoszenie",
                "verbose_name_plural": "zgłoszenia",
                "ordering": ("-created_at", "-id"),
            },
        ),
        migrations.CreateModel(
            name="SupportMessage",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "from_coordinator",
                    models.BooleanField(default=False, verbose_name="odpowiedź organizatora"),
                ),
                ("body", models.TextField(max_length=10000, verbose_name="treść")),
                (
                    "created_at",
                    models.DateTimeField(default=django.utils.timezone.now, verbose_name="dodana"),
                ),
                (
                    "author",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="support_messages",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="autor",
                    ),
                ),
                (
                    "ticket",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="messages",
                        to="support.supportticket",
                        verbose_name="zgłoszenie",
                    ),
                ),
            ],
            options={
                "verbose_name": "wiadomość zgłoszenia",
                "verbose_name_plural": "wiadomości zgłoszeń",
                "ordering": ("created_at", "id"),
            },
        ),
        migrations.AddIndex(
            model_name="supportticket",
            index=models.Index(fields=["status", "-created_at"], name="support_ticket_status_idx"),
        ),
        migrations.AddIndex(
            model_name="supportticket",
            index=models.Index(fields=["user", "-created_at"], name="support_ticket_user_idx"),
        ),
        migrations.AddConstraint(
            model_name="supportticket",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("user__isnull", False), models.Q(("email", ""), _negated=True), _connector="OR"
                ),
                name="support_ticket_has_reply_address",
            ),
        ),
    ]
