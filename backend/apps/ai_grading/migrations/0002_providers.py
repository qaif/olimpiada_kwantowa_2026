# Dostawcy AI (prośba organizatora z 24.09.2026) i tryb testowy.
#
# Kolejność operacji jest kolejnością danych, nie wygody:
#
# 1. konto dostawcy powstaje **przed** usunięciem kolumn klucza z ustawień, żeby klucz Anthropic
#    z v0.34.0 przeszedł do ``AiProviderAccount(provider="anthropic")`` bez odszyfrowywania
#    (to ten sam token Fernet z tą samą etykietą – ``crypto._fernet_for``),
# 2. ``AiAssessment.competition`` dostaje wartość z pracy (kolumna denormalizacyjna
#    ``Submission.competition_id``), zanim stanie się obowiązkowa,
# 3. ``requested_model`` istniejących ocen to ich ``model`` – przed tą zmianą serwis nie odróżniał
#    modelu zamówionego od tego, który odpowiedział, a jedyną różnicą bywało przełączenie
#    ``fallbacks`` przy odmowie (ocena w stanie błędu, bez znaczenia dla idempotencji).
#
# **Potwierdzenia umowy powierzenia migracja nie wpisuje** – także dla Anthropic, którego flaga
# była włączona. To jest oświadczenie prawne organizatora, a nie stan techniczny do wywiedzenia:
# wpisuje je człowiek w panelu albo operator komendą ``confirm_ai_provider_dpa`` po wdrożeniu
# (``docs/OPERACJE.md`` § 17.6).
#
# Zależności jak w ``0001``: ``submissions.0001_initial`` (klucz obcy do ``Submission.pk``),
# a nie najnowsza migracja prac – uzasadnienie w ``0001_initial``.

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

PROVIDER_CHOICES = [
    ("anthropic", "Anthropic (Claude)"),
    ("openai", "OpenAI (GPT)"),
    ("google", "Google (Gemini)"),
    ("meta", "Meta (Muse Spark)"),
]
KEY_FIELDS = (
    "api_key_encrypted",
    "api_key_last4",
    "api_key_set_at",
    "api_key_set_by_id",
    "api_key_checked_at",
    "api_key_check_ok",
    "api_key_check_message",
)


def move_keys_to_accounts(apps, schema_editor):
    Settings = apps.get_model("ai_grading", "AiGradingSettings")
    Account = apps.get_model("ai_grading", "AiProviderAccount")
    for row in Settings.objects.exclude(api_key_encrypted=""):
        Account.objects.update_or_create(
            competition_id=row.competition_id,
            provider="anthropic",
            defaults={name: getattr(row, name) for name in KEY_FIELDS},
        )


def move_keys_back(apps, schema_editor):
    Settings = apps.get_model("ai_grading", "AiGradingSettings")
    Account = apps.get_model("ai_grading", "AiProviderAccount")
    for account in Account.objects.filter(provider="anthropic").exclude(api_key_encrypted=""):
        Settings.objects.filter(competition_id=account.competition_id).update(
            **{name: getattr(account, name) for name in KEY_FIELDS}
        )


def fill_assessments(apps, schema_editor):
    # Konkurs przez wpis na etap, a nie przez ``Submission.competition`` – ta kolumna powstaje
    # w ``submissions.0005``, a zależymy celowo od ``submissions.0001`` (patrz nagłówek pliku).
    Assessment = apps.get_model("ai_grading", "AiAssessment")
    Settings = apps.get_model("ai_grading", "AiGradingSettings")
    defaults = dict(Settings.objects.values_list("competition_id", "model"))
    rows = Assessment.objects.values_list("pk", "model", "submission__entry__stage__edition__competition_id")
    for pk, model, competition_id in rows:
        Assessment.objects.filter(pk=pk).update(
            competition_id=competition_id,
            requested_model=model or defaults.get(competition_id, "claude-opus-5"),
        )


class Migration(migrations.Migration):
    dependencies = [
        ("ai_grading", "0001_initial"),
        ("competitions", "0031_interview_score"),
        ("submissions", "0001_initial"),
        ("tenancy", "0008_competition_submission_forward_emails"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # --- 1. klucze: ustawienia → konta dostawców ---------------------------------------------
        migrations.CreateModel(
            name="AiProviderAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider", models.CharField(choices=PROVIDER_CHOICES, max_length=16, verbose_name="dostawca")),
                ("api_key_encrypted", models.TextField(blank=True, verbose_name="klucz API (zaszyfrowany)")),
                ("api_key_last4", models.CharField(blank=True, max_length=4, verbose_name="ostatnie znaki klucza")),
                ("api_key_set_at", models.DateTimeField(blank=True, null=True, verbose_name="klucz ustawiony")),
                ("api_key_checked_at", models.DateTimeField(blank=True, null=True, verbose_name="klucz sprawdzony")),
                ("api_key_check_ok", models.BooleanField(blank=True, null=True, verbose_name="klucz działa")),
                (
                    "api_key_check_message",
                    models.CharField(blank=True, max_length=300, verbose_name="wynik sprawdzenia"),
                ),
                (
                    "dpa_confirmed_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="umowa powierzenia potwierdzona"),
                ),
                ("dpa_note", models.CharField(blank=True, max_length=300, verbose_name="uwaga do potwierdzenia")),
                (
                    "api_key_set_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="klucz ustawił",
                    ),
                ),
                (
                    "competition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ai_provider_accounts",
                        to="tenancy.competition",
                        verbose_name="konkurs",
                    ),
                ),
                (
                    "dpa_confirmed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="umowę potwierdził",
                    ),
                ),
            ],
            options={
                "verbose_name": "konto dostawcy AI",
                "verbose_name_plural": "konta dostawców AI",
                "ordering": ("competition_id", "provider"),
            },
        ),
        migrations.AddConstraint(
            model_name="aiprovideraccount",
            constraint=models.UniqueConstraint(fields=("competition", "provider"), name="ai_provider_account_unique"),
        ),
        migrations.RunPython(move_keys_to_accounts, move_keys_back),
        migrations.RemoveField(model_name="aigradingsettings", name="api_key_check_message"),
        migrations.RemoveField(model_name="aigradingsettings", name="api_key_check_ok"),
        migrations.RemoveField(model_name="aigradingsettings", name="api_key_checked_at"),
        migrations.RemoveField(model_name="aigradingsettings", name="api_key_encrypted"),
        migrations.RemoveField(model_name="aigradingsettings", name="api_key_last4"),
        migrations.RemoveField(model_name="aigradingsettings", name="api_key_set_at"),
        migrations.RemoveField(model_name="aigradingsettings", name="api_key_set_by"),
        # --- ustawienia: dostawca domyślny, ceny, wywołania bez ceny -----------------------------
        migrations.AddField(
            model_name="aigradingsettings",
            name="provider",
            field=models.CharField(
                choices=PROVIDER_CHOICES, default="anthropic", max_length=16, verbose_name="dostawca domyślny"
            ),
        ),
        migrations.AlterField(
            model_name="aigradingsettings",
            name="model",
            field=models.CharField(default="claude-opus-5", max_length=100, verbose_name="model domyślny"),
        ),
        migrations.AddField(
            model_name="aigradingsettings",
            name="price_overrides",
            field=models.JSONField(blank=True, default=dict, verbose_name="ceny modeli (nadpisane)"),
        ),
        migrations.AddField(
            model_name="aigradingsettings",
            name="total_unpriced_calls",
            field=models.PositiveIntegerField(default=0, verbose_name="wywołań bez znanej ceny"),
        ),
        # --- 2–3. oceny: konkurs wprost, dostawca, model zamówiony -------------------------------
        migrations.AddField(
            model_name="aiassessment",
            name="competition",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
        migrations.AddField(
            model_name="aiassessment",
            name="provider",
            field=models.CharField(choices=PROVIDER_CHOICES, default="anthropic", max_length=16, verbose_name="dostawca"),
        ),
        migrations.AddField(
            model_name="aiassessment",
            name="requested_model",
            field=models.CharField(blank=True, max_length=100, verbose_name="model zamówiony"),
        ),
        migrations.AddField(
            model_name="aiassessment",
            name="cost_known",
            field=models.BooleanField(default=True, verbose_name="koszt znany"),
        ),
        migrations.AlterField(
            model_name="aiassessment",
            name="model",
            field=models.CharField(blank=True, max_length=100, verbose_name="model"),
        ),
        migrations.AlterField(
            model_name="aiassessment",
            name="sent_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="przekazana do dostawcy"),
        ),
        migrations.AlterField(
            model_name="aiassessment",
            name="submission",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="ai_assessments",
                to="submissions.submission",
                verbose_name="praca",
            ),
        ),
        migrations.RunPython(fill_assessments, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="aiassessment",
            name="competition",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
        # --- tryb testowy -------------------------------------------------------------------------
        migrations.CreateModel(
            name="AiTestWork",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("label", models.CharField(blank=True, max_length=120, verbose_name="opis")),
                ("object_key", models.CharField(blank=True, max_length=255, verbose_name="klucz obiektu")),
                ("sha256", models.CharField(max_length=64, verbose_name="skrót SHA-256")),
                ("mime", models.CharField(max_length=60, verbose_name="typ")),
                ("size_bytes", models.PositiveBigIntegerField(verbose_name="rozmiar (B)")),
                ("page_count", models.PositiveIntegerField(blank=True, null=True, verbose_name="liczba stron")),
                (
                    "av_status",
                    models.CharField(
                        choices=[
                            ("PENDING", "oczekuje na skan"),
                            ("CLEAN", "czysty"),
                            ("INFECTED", "zainfekowany"),
                            ("ERROR", "błąd skanu"),
                        ],
                        default="PENDING",
                        max_length=16,
                        verbose_name="skan antywirusowy",
                    ),
                ),
                ("uploaded_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="wgrana")),
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
                    "problem",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ai_test_works",
                        to="competitions.problem",
                        verbose_name="zadanie",
                    ),
                ),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="wgrał",
                    ),
                ),
            ],
            options={
                "verbose_name": "praca testowa AI",
                "verbose_name_plural": "prace testowe AI",
                "ordering": ("-uploaded_at", "-id"),
            },
        ),
        migrations.AddField(
            model_name="aiassessment",
            name="test_work",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="assessments",
                to="ai_grading.aitestwork",
                verbose_name="praca testowa",
            ),
        ),
        migrations.AlterModelOptions(
            name="aiassessment",
            options={
                "ordering": ("submission_id", "-requested_at", "-id"),
                "verbose_name": "ocena AI",
                "verbose_name_plural": "oceny AI",
            },
        ),
        migrations.AddConstraint(
            model_name="aiassessment",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("submission__isnull", False), ("test_work__isnull", True)),
                    models.Q(("submission__isnull", True), ("test_work__isnull", False)),
                    _connector="OR",
                ),
                name="ai_assessment_one_source",
            ),
        ),
        migrations.AddConstraint(
            model_name="aiassessment",
            constraint=models.UniqueConstraint(
                condition=models.Q(("submission__isnull", False)),
                fields=("submission", "provider", "requested_model"),
                name="ai_assessment_unique_submission_model",
            ),
        ),
        migrations.AddConstraint(
            model_name="aiassessment",
            constraint=models.UniqueConstraint(
                condition=models.Q(("test_work__isnull", False)),
                fields=("test_work", "provider", "requested_model"),
                name="ai_assessment_unique_test_model",
            ),
        ),
    ]
