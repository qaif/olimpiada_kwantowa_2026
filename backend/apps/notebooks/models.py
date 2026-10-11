"""Modele notatników kwantowych: konfiguracja zadania i przebiegi oceny (QC-01 § 4)."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.competitions.scoping import competition_scoped_manager


class NotebookMode(models.TextChoices):
    FREE = "FREE", _("Notatnik swobodny (ocenia komisja)")
    AUTOGRADED = "AUTOGRADED", _("Sprawdzanie automatyczne (testy)")


class ResultsVisibility(models.TextChoices):
    #: Domyślne: wynik testów ukrytych widzi wyłącznie personel – uczeń nie ma wyroczni (§ 9).
    STAFF = "STAFF", _("Tylko organizator i komisja")
    AFTER_CLOSE = "AFTER_CLOSE", _("Uczestnik po zamknięciu etapu")
    IMMEDIATE = "IMMEDIATE", _("Uczestnik od razu (zalecane tylko na etapach treningowych)")


class ContentLanguage(models.TextChoices):
    """Język **treści** notatnika (szablon startowy, komunikaty testów), nie interfejsu."""

    PL = "pl", "polski"
    EN = "en", "English"


class NotebookTask(models.Model):
    """Ustawienia notatnika jednego zadania. Brak wiersza = zadanie bez notatnika (jak dotąd)."""

    problem = models.OneToOneField(
        "competitions.Problem",
        on_delete=models.CASCADE,
        related_name="notebook_task",
        verbose_name=_("zadanie"),
    )
    mode = models.CharField(_("tryb"), max_length=12, choices=NotebookMode.choices, default=NotebookMode.FREE)
    language = models.CharField(
        _("język treści notatnika"), max_length=2, choices=ContentLanguage.choices, default=ContentLanguage.PL
    )
    #: Notatnik startowy (nbformat 4) w bazie, a nie w storage'u: to kilka kilobajtów, które serwer
    #: i tak przerabia przy każdym podaniu (dokleja komórkę testów widocznych). Pusty = generowany.
    starter_notebook = models.JSONField(_("notatnik startowy"), null=True, blank=True)
    visible_tests = models.JSONField(_("testy widoczne"), default=list, blank=True)
    #: Testy ukryte – **nigdy** nie trafiają do przeglądarki ucznia ani do piaskownicy (§ 9).
    hidden_tests = models.JSONField(_("testy ukryte"), default=list, blank=True)
    #: Rozwiązanie wzorcowe koordynatora – wyłącznie do sprawdzenia, czy testy przechodzą.
    reference_notebook = models.JSONField(_("notatnik wzorcowy"), null=True, blank=True)
    time_limit_seconds = models.PositiveSmallIntegerField(_("limit czasu (s)"), default=20)
    memory_limit_mb = models.PositiveSmallIntegerField(_("limit pamięci (MB)"), default=512)
    results_visibility = models.CharField(
        _("widoczność wyników testów ukrytych"),
        max_length=12,
        choices=ResultsVisibility.choices,
        default=ResultsVisibility.STAFF,
    )
    updated_at = models.DateTimeField(_("zmieniono"), auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    objects = competition_scoped_manager("problem__stage__edition__competition")

    class Meta:
        verbose_name = _("notatnik zadania")
        verbose_name_plural = _("notatniki zadań")
        constraints = [
            models.CheckConstraint(
                condition=Q(time_limit_seconds__gte=5) & Q(time_limit_seconds__lte=60),
                name="notebooks_task_time_limit_range",
            ),
            models.CheckConstraint(
                condition=Q(memory_limit_mb__gte=256) & Q(memory_limit_mb__lte=1024),
                name="notebooks_task_memory_limit_range",
            ),
        ]

    def __str__(self) -> str:
        return f"Notatnik: {self.problem}"

    @property
    def is_autograded(self) -> bool:
        return self.mode == NotebookMode.AUTOGRADED


class RunStatus(models.TextChoices):
    PENDING = "PENDING", _("w kolejce")
    RUNNING = "RUNNING", _("sprawdzanie")
    DONE = "DONE", _("sprawdzono")
    ERROR = "ERROR", _("błąd sprawdzania")
    SUPERSEDED = "SUPERSEDED", _("zastąpiony nowszą wersją")


class NotebookRun(models.Model):
    """Jeden przebieg oceny: plik pracy (albo notatnik wzorcowy) przez piaskownicę i testy."""

    competition = models.ForeignKey("tenancy.Competition", on_delete=models.CASCADE, related_name="+")
    task = models.ForeignKey(NotebookTask, on_delete=models.CASCADE, related_name="runs")
    submission = models.ForeignKey(
        "submissions.Submission",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="notebook_runs",
    )
    submission_file = models.ForeignKey(
        "submissions.SubmissionFile", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    is_reference = models.BooleanField(default=False)
    job_id = models.CharField(max_length=32, blank=True, db_index=True)
    status = models.CharField(
        max_length=12, choices=RunStatus.choices, default=RunStatus.PENDING, db_index=True
    )
    #: Wyniki testów ukrytych i widocznych: ``[{id, name, points, max_points, passed, message}]``.
    results = models.JSONField(default=list, blank=True)
    visible_results = models.JSONField(default=list, blank=True)
    score = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    max_score = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    error_code = models.CharField(max_length=40, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    cell_errors = models.JSONField(default=list, blank=True)
    #: Początek wyjścia programu ucznia – dla personelu, do diagnozy („czemu 0 punktów”).
    output_tail = models.TextField(blank=True)
    #: Skrót testów, z którymi liczono – inny niż bieżący znaczy „wynik nieaktualny, przelicz”.
    tests_hash = models.CharField(max_length=64, blank=True)
    requested_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = _("przebieg oceny notatnika")
        verbose_name_plural = _("przebiegi oceny notatników")
        ordering = ("-requested_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=["submission_file"],
                condition=Q(is_reference=False),
                name="notebooks_run_unique_file",
            ),
        ]
        indexes = [models.Index(fields=["task", "status"], name="notebooks_run_task_status")]

    def __str__(self) -> str:
        return f"Przebieg {self.pk} ({self.status})"

    @property
    def is_finished(self) -> bool:
        return self.status in (RunStatus.DONE, RunStatus.ERROR, RunStatus.SUPERSEDED)
