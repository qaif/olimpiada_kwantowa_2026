# Napisana ręcznie (UX-PANEL-01): powiadomienia w panelu uczestnika i znacznik rozesłania ogłoszeń.

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("chat", "0003_organizer_announcement"),
        ("tenancy", "0017_document_kind_choices_merged"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Notification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("chat_organizer", "Wiadomość od organizatora"),
                            ("chat_peer", "Wiadomość od uczestnika"),
                            ("announcement", "Ogłoszenie organizatora"),
                            ("student_status_accepted", "Zaświadczenie zaakceptowane"),
                            ("student_status_rejected", "Zaświadczenie odrzucone"),
                            ("forum_reply", "Odpowiedź na forum"),
                            ("forum_decision", "Decyzja moderatora forum"),
                            ("results_published", "Ogłoszone wyniki"),
                            ("appeal_decided", "Decyzja w sprawie reklamacji"),
                        ],
                        max_length=32,
                        verbose_name="rodzaj",
                    ),
                ),
                ("params", models.JSONField(blank=True, default=dict, verbose_name="parametry")),
                ("target", models.CharField(max_length=80, verbose_name="adres (nazwa)")),
                ("target_args", models.JSONField(blank=True, default=list, verbose_name="argumenty adresu")),
                ("target_suffix", models.CharField(blank=True, max_length=80, verbose_name="dopisek adresu")),
                ("dedupe_key", models.CharField(blank=True, max_length=64, verbose_name="klucz scalania")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="utworzono")),
                ("read_at", models.DateTimeField(blank=True, null=True, verbose_name="przeczytano")),
                (
                    "competition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="tenancy.competition",
                        verbose_name="konkurs",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="panel_notifications",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="konto",
                    ),
                ),
            ],
            options={
                "verbose_name": "powiadomienie",
                "verbose_name_plural": "powiadomienia",
                "ordering": ("-created_at", "-pk"),
                "indexes": [
                    models.Index(fields=["user", "competition", "read_at"], name="notif_user_unread"),
                    models.Index(fields=["user", "competition", "-created_at"], name="notif_user_recent"),
                    models.Index(fields=["created_at"], name="notif_created"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("read_at__isnull", True), models.Q(("dedupe_key", ""), _negated=True)),
                        fields=("user", "competition", "dedupe_key"),
                        name="notif_one_unread_per_key",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="AnnouncementDelivery",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("published_at", models.DateTimeField(verbose_name="opublikowano")),
                ("delivered_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="rozesłano")),
                ("recipients", models.PositiveIntegerField(default=0, verbose_name="odbiorców")),
                (
                    "announcement",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="chat.organizerannouncement",
                        verbose_name="ogłoszenie",
                    ),
                ),
            ],
            options={
                "verbose_name": "rozesłanie ogłoszenia",
                "verbose_name_plural": "rozesłania ogłoszeń",
                "constraints": [
                    models.UniqueConstraint(fields=("announcement", "published_at"), name="notif_announcement_once"),
                ],
            },
        ),
    ]
