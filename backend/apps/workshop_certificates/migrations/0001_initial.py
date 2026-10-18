"""Reguła automatycznych zaświadczeń z warsztatów i rejestr wystawionych przez nią (WS-CERT-02).

Dwie nowe, puste tabele – nic nie jest przenoszone ani wypełniane. Brak wiersza reguły znaczy
„wyłączone”, więc do pierwszego zapisu na ekranie koordynatora serwis działa dokładnie jak dotąd.
"""

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("competitions", "0034_video_provider_livekit"),
        ("results", "0008_certificate_award_kinds"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AutoIssuePolicy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "enabled",
                    models.BooleanField(default=False, verbose_name="wystawiaj zaświadczenie automatycznie"),
                ),
                (
                    "min_workshops",
                    models.PositiveSmallIntegerField(
                        default=1,
                        help_text="Liczą się różne warsztaty z harmonogramu na stronie „Warsztaty”.",
                        validators=[
                            django.core.validators.MinValueValidator(1),
                            django.core.validators.MaxValueValidator(100),
                        ],
                        verbose_name="próg: co najmniej tyle warsztatów",
                    ),
                ),
                (
                    "notify_participants",
                    models.BooleanField(default=False, verbose_name="wyślij list „Twoje zaświadczenie jest gotowe”"),
                ),
                ("updated_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="zmieniono")),
                (
                    "edition",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="workshop_certificate_policy",
                        to="competitions.edition",
                        verbose_name="edycja",
                    ),
                ),
                (
                    "template",
                    models.ForeignKey(
                        blank=True,
                        help_text="Puste = szablon dopasowany jak dla każdego dokumentu (rodzaj „warsztaty”, edycja).",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="results.certificatetemplate",
                        verbose_name="szablon graficzny",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="zmienił",
                    ),
                ),
            ],
            options={
                "verbose_name": "reguła zaświadczeń z warsztatów",
                "verbose_name_plural": "reguły zaświadczeń z warsztatów",
            },
        ),
        migrations.CreateModel(
            name="AutoIssuedCertificate",
            fields=[
                (
                    "certificate",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="workshop_auto_issue",
                        serialize=False,
                        to="results.certificate",
                        verbose_name="dokument",
                    ),
                ),
                (
                    "trigger",
                    models.CharField(
                        choices=[("attendance", "zapis obecności"), ("recalculate", "przelicz teraz")],
                        max_length=16,
                        verbose_name="wyzwalacz",
                    ),
                ),
                ("threshold", models.PositiveSmallIntegerField(verbose_name="próg w chwili wystawienia")),
                ("issued_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="wystawiono")),
                ("notified_at", models.DateTimeField(blank=True, null=True, verbose_name="list wysłany")),
                (
                    "edition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="competitions.edition",
                        verbose_name="edycja",
                    ),
                ),
            ],
            options={
                "verbose_name": "zaświadczenie z warsztatów wystawione automatycznie",
                "verbose_name_plural": "zaświadczenia z warsztatów wystawione automatycznie",
                "ordering": ("-issued_at",),
            },
        ),
    ]
