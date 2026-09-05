"""Model publikacji wyników etapu (PROJEKT.md 2.2, 2.4).

Zasady:
- ``snapshot`` jest **zamrożoną** tabelą wyników. Publiczny endpoint czyta wyłącznie ten JSON,
  nigdy bazy: po publikacji zmiana ``FinalGrade`` (np. późna decyzja komisji) nie może po cichu
  przepisać ogłoszonych wyników. Nowa tabela wymaga nowej publikacji, a ta zostawia ślad w audycie,
- ``snapshot`` jest zanonimizowany zgodnie z ``anonymization`` i **nigdy** nie zawiera e-maila,
  roku urodzenia ani identyfikatora użytkownika. Imię i nazwisko wolno w nim umieścić wyłącznie
  przy ``FULL`` i wyłącznie dla uczestnika, który wyraził zgodę (``publish_full_name``),
- czas zawsze przez ``django.utils.timezone.now()``.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.competitions.models import Stage


def default_snapshot() -> list:
    """``default`` JSONField musi być wywoływalny i zwracać nowy obiekt."""
    return []


def default_entry_totals() -> dict:
    """``default`` JSONField musi być wywoływalny i zwracać nowy obiekt."""
    return {}


class Anonymization(models.TextChoices):
    CODE = "CODE", "kod uczestnika"
    INITIALS_SCHOOL = "INITIALS_SCHOOL", "inicjały i szkoła"
    FULL = "FULL", "pełne dane – finał, za zgodą"


class ResultsPublication(models.Model):
    """Ogłoszona tabela wyników jednego etapu. Jedna na etap – ponowna publikacja ją nadpisuje."""

    # PROTECT, nie CASCADE: ogłoszona tabela wyników jest dokumentem, a nie szczegółem etapu.
    # Skasowanie etapu ma się wywrócić na ProtectedError i wymusić świadomą decyzję (najpierw
    # zdejmij publikację), zamiast po cichu zabrać jedyny ślad tego, co ogłoszono.
    stage = models.OneToOneField(Stage, on_delete=models.PROTECT, related_name="results_publication")
    published_at = models.DateTimeField("opublikowane", default=timezone.now)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="results_publications",
        verbose_name="opublikował",
    )
    anonymization = models.CharField(
        "anonimizacja", max_length=24, choices=Anonymization.choices, default=Anonymization.CODE
    )
    snapshot = models.JSONField("zamrożona tabela", default=default_snapshot, blank=True)
    # Mapa ``{str(StageEntry.pk): suma}`` z chwili publikacji. Nie jest częścią publicznej tabeli
    # (serializery wypisują pola jawnie) i nie zawiera danych osobowych – identyfikator wpisu plus
    # liczba. Służy jednemu: uczestnik w ``me/results/`` musi wiedzieć, czy jego bieżąca suma
    # rozjechała się z ogłoszoną. Ze snapshotu tego nie da się odczytać, bo wiersze są anonimowe.
    entry_totals = models.JSONField(
        "sumy wpisów w chwili publikacji", default=default_entry_totals, blank=True
    )

    class Meta:
        verbose_name = "publikacja wyników"
        verbose_name_plural = "publikacje wyników"
        ordering = ("-published_at", "-id")

    def __str__(self) -> str:
        return f"wyniki etapu {self.stage_id} ({self.anonymization})"

    @property
    def rows(self) -> list:
        """Wiersze tabeli. ``snapshot`` bywa pusty (etap bez wpisów) – kształt musi być stały."""
        return self.snapshot if isinstance(self.snapshot, list) else []
