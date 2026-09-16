"""Komunikaty organizatora w banerze pod nagłówkiem serwisu.

Constraint ``cms_announcement_window_ordered`` odrzuca okno o niedodatniej długości: komunikat
z takim oknem nigdy nie jest otwarty, czyli byłby ogłoszeniem, którego nikt nie zobaczy.
"""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cms", "0017_faq_page"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Announcement",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("text", models.TextField(max_length=500, verbose_name="treść")),
                ("link_url", models.URLField(blank=True, max_length=300, verbose_name="adres odnośnika")),
                (
                    "link_label",
                    models.CharField(
                        blank=True,
                        help_text="Puste = odnośnik się nie pokazuje, nawet gdy adres jest wpisany.",
                        max_length=80,
                        verbose_name="etykieta odnośnika",
                    ),
                ),
                (
                    "level",
                    models.CharField(
                        choices=[("info", "informacja"), ("warning", "ostrzeżenie"), ("danger", "awaria")],
                        default="info",
                        max_length=16,
                        verbose_name="waga",
                    ),
                ),
                ("starts_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="od")),
                (
                    "ends_at",
                    models.DateTimeField(
                        blank=True,
                        help_text="Puste = komunikat wisi do wyłączenia.",
                        null=True,
                        verbose_name="do",
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        help_text="Wyłącznik awaryjny. Odznaczenie zdejmuje komunikat natychmiast, niezależnie od dat.",
                        verbose_name="włączony",
                    ),
                ),
                (
                    "dismissible",
                    models.BooleanField(
                        default=True,
                        help_text="Czytelnik może schować komunikat w swojej przeglądarce. Odznacz dla komunikatów o awarii i terminach – te mają zostać na ekranie.",
                        verbose_name="można zamknąć",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(default=django.utils.timezone.now, verbose_name="utworzony"),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="announcements",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="utworzył",
                    ),
                ),
            ],
            options={
                "verbose_name": "komunikat",
                "verbose_name_plural": "komunikaty",
                "ordering": ("-starts_at", "-id"),
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            ("ends_at__isnull", True), ("starts_at__lt", models.F("ends_at")), _connector="OR"
                        ),
                        name="cms_announcement_window_ordered",
                    )
                ],
            },
        ),
    ]
