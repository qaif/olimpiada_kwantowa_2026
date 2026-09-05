"""Modele procedury odwoławczej: reklamacja uczestnika i decyzja komisji (PROJEKT.md 2.2, 2.4).

Zasady:
- na jedno rozwiązanie przypada dokładnie jedna reklamacja – pilnują tego dwa ograniczenia w bazie
  (``unique_together`` z PROJEKT.md oraz unikalność samego ``submission``), a serwis zamienia
  naruszenie na czytelne 409 zamiast błędu bazy,
- ``AppealDecision`` powstaje wyłącznie w ``apps.appeals.services`` pod blokadą ``select_for_update``
  na ``Submission`` – relacja jeden-do-jednego jest ostatnią linią obrony przed dwiema decyzjami
  z wyścigu,
- reklamacja nie trzyma żadnych danych osobowych: uczestnik jest tu wyłącznie przez ``Participant``,
  a komisja widzi go jako ``public_code`` (serializery),
- czas zawsze przez ``django.utils.timezone.now()``.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.accounts.models import CommitteeMember, Participant
from apps.submissions.models import Submission

#: Minimalna długość uzasadnienia reklamacji (T-06). Krótsze zgłoszenie nie jest odwołaniem.
MIN_ARGUMENT_LENGTH = 50


class AppealStatus(models.TextChoices):
    OPEN = "OPEN", "złożona"
    IN_REVIEW = "IN_REVIEW", "rozpatrywana"
    REJECTED = "REJECTED", "odrzucona"
    ACCEPTED = "ACCEPTED", "uwzględniona"
    PARTIALLY_ACCEPTED = "PARTIALLY_ACCEPTED", "częściowo uwzględniona"


#: Statusy reklamacji czekającej na rozstrzygnięcie – to one trafiają do kolejki komisji.
PENDING_STATUSES = (AppealStatus.OPEN, AppealStatus.IN_REVIEW)
#: Statusy, którymi wolno zamknąć reklamację.
DECIDABLE_STATUSES = (AppealStatus.REJECTED, AppealStatus.ACCEPTED, AppealStatus.PARTIALLY_ACCEPTED)
#: Rozstrzygnięcia wymagające zmiany punktacji.
SCORE_CHANGING_STATUSES = (AppealStatus.ACCEPTED, AppealStatus.PARTIALLY_ACCEPTED)


class AppealQuerySet(models.QuerySet):
    def pending(self):
        return self.filter(status__in=PENDING_STATUSES)

    def without_conflict_for(self, member: CommitteeMember | None):
        """Reklamacje, które ten członek komisji może rozpatrywać.

        Konflikt interesów: autor ``grading.Review`` (dowolnej rundy) tego rozwiązania nie może ani
        decydować, ani widzieć reklamacji na liście. Wykluczenie idzie po nazwie relacji odwrotnej
        (``submission__reviews``), więc ``apps.appeals`` nie musi importować modeli oceniania
        w zapytaniu. ``None`` (brak profilu) nie widzi niczego.
        """
        if member is None:
            return self.none()
        return self.exclude(submission__reviews__reviewer=member)


class Appeal(models.Model):
    """Reklamacja uczestnika na ocenę wstępną jednego rozwiązania."""

    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="appeals")
    filed_by = models.ForeignKey(Participant, on_delete=models.CASCADE, related_name="appeals")
    filed_at = models.DateTimeField("złożona", default=timezone.now)
    argument = models.TextField("uzasadnienie")
    status = models.CharField(
        "status", max_length=24, choices=AppealStatus.choices, default=AppealStatus.OPEN
    )

    objects = AppealQuerySet.as_manager()

    class Meta:
        verbose_name = "reklamacja"
        verbose_name_plural = "reklamacje"
        ordering = ("filed_at", "id")
        constraints = [
            # PROJEKT.md 2.2: jedna reklamacja uczestnika na rozwiązanie...
            models.UniqueConstraint(
                fields=["submission", "filed_by"], name="appeals_appeal_unique_participant"
            ),
            # ...a ponieważ rozwiązanie ma dokładnie jednego uczestnika, druga reklamacja na to samo
            # rozwiązanie nie może powstać nawet z cudzego konta (obrona przed pomyłką w serwisie).
            models.UniqueConstraint(fields=["submission"], name="appeals_appeal_unique_submission"),
        ]

    def __str__(self) -> str:
        return f"reklamacja {self.pk} (zgł. {self.submission_id}, {self.status})"

    @property
    def participant_public_code(self) -> str:
        """Jedyny identyfikator uczestnika, jaki wolno pokazać komisji odwoławczej."""
        return self.filed_by.public_code

    @property
    def is_pending(self) -> bool:
        return self.status in PENDING_STATUSES


class AppealDecision(models.Model):
    """Rozstrzygnięcie reklamacji. Jedno na reklamację – relacja pilnuje tego w bazie."""

    appeal = models.OneToOneField(Appeal, on_delete=models.CASCADE, related_name="decision")
    committee = models.ManyToManyField(
        CommitteeMember, related_name="appeal_decisions", verbose_name="skład komisji", blank=True
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="appeal_decisions",
        verbose_name="rozstrzygnął",
    )
    # ``null`` = decyzja bez zmiany punktacji (reklamacja odrzucona).
    new_score = models.PositiveSmallIntegerField("nowa punktacja", null=True, blank=True)
    justification = models.TextField("uzasadnienie")
    decided_at = models.DateTimeField("rozstrzygnięta", default=timezone.now)

    class Meta:
        verbose_name = "decyzja o reklamacji"
        verbose_name_plural = "decyzje o reklamacjach"
        ordering = ("-decided_at", "-id")

    def __str__(self) -> str:
        return f"decyzja dla reklamacji {self.appeal_id} (nowa ocena: {self.new_score})"
