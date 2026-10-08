# Napisana ręcznie 2026-10-08 (zadanie CZ-ANN-01) – w kształcie, który wypisuje ``makemigrations``.

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0002_reply_templates_status_age_limit"),
        ("tenancy", "0010_path_prefix_routing_on_platform"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="OrganizerAnnouncement",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("title", models.CharField(max_length=200, verbose_name="tytuł")),
                ("body", models.TextField(max_length=4000, verbose_name="treść")),
                ("is_published", models.BooleanField(default=False, verbose_name="opublikowane")),
                ("published_at", models.DateTimeField(blank=True, null=True, verbose_name="opublikowane o")),
                (
                    "published_from",
                    models.DateTimeField(
                        blank=True,
                        help_text="Puste = od chwili publikacji.",
                        null=True,
                        verbose_name="widoczne od",
                    ),
                ),
                (
                    "published_until",
                    models.DateTimeField(
                        blank=True, help_text="Puste = do wyłączenia.", null=True, verbose_name="widoczne do"
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(default=django.utils.timezone.now, verbose_name="utworzone"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(default=django.utils.timezone.now, verbose_name="zmienione"),
                ),
                (
                    "competition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chat_announcements",
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
            ],
            options={
                "verbose_name": "ogłoszenie organizatora",
                "verbose_name_plural": "ogłoszenia organizatora",
                "ordering": ("-created_at", "-id"),
                "indexes": [
                    models.Index(fields=["competition", "is_published"], name="chat_announcement_live_idx")
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            ("published_from__isnull", True),
                            ("published_until__isnull", True),
                            ("published_until__gt", models.F("published_from")),
                            _connector="OR",
                        ),
                        name="chat_announcement_window",
                    )
                ],
            },
        ),
    ]
