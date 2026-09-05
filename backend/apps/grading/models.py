"""Modele oceniania: niezależna recenzja i ocena uzgodniona (PROJEKT.md 2.2).

Zasady:
- ocenianie jest ślepe: ``Review`` nie trzyma żadnych danych osobowych uczestnika, a serializery
  pokazują recenzentowi wyłącznie ``participant_public_code``,
- ``FinalGrade`` powstaje wyłącznie w serwisach (``apps.grading.services``) pod blokadą
  ``select_for_update`` na ``Submission`` – relacja jeden-do-jednego jest ostatnią linią obrony
  przed dwoma ocenami z wyścigu,
- czas zawsze przez ``django.utils.timezone.now()``.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import CommitteeMember
from apps.submissions.models import Submission

#: Runda 1 to ocena ślepa (dwóch niezależnych recenzentów), runda 2 – rozjemcza (trzeci recenzent).
ROUND_BLIND = 1
ROUND_TIEBREAK = 2


def default_annotations() -> list:
    """``default`` JSONField musi być wywoływalny i zwracać nowy obiekt."""
    return []


class ReviewStatus(models.TextChoices):
    ASSIGNED = "ASSIGNED", "przydzielona"
    DRAFT = "DRAFT", "szkic"
    SUBMITTED = "SUBMITTED", "wystawiona"


class GradeMethod(models.TextChoices):
    CONSENSUS = "CONSENSUS", "zgodne oceny"
    THIRD_REVIEW = "THIRD_REVIEW", "trzeci recenzent"
    MODERATION = "MODERATION", "posiedzenie komisji"
    APPEAL = "APPEAL", "po reklamacji"


class ReviewQuerySet(models.QuerySet):
    def for_reviewer(self, member: CommitteeMember | None):
        """Recenzje jednego recenzenta. ``None`` (brak profilu) nie widzi niczego."""
        if member is None:
            return self.none()
        return self.filter(reviewer=member)


class Review(models.Model):
    """Niezależna ocena jednego recenzenta dla jednego rozwiązania."""

    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="reviews")
    reviewer = models.ForeignKey(CommitteeMember, on_delete=models.PROTECT, related_name="reviews")
    round = models.PositiveSmallIntegerField("runda", default=ROUND_BLIND)
    score = models.PositiveSmallIntegerField("punkty", null=True, blank=True)
    comment_internal = models.TextField("komentarz wewnętrzny", blank=True)
    comment_for_participant = models.TextField("komentarz dla uczestnika", blank=True)
    annotations = models.JSONField("adnotacje", default=default_annotations, blank=True)
    status = models.CharField(
        "status", max_length=16, choices=ReviewStatus.choices, default=ReviewStatus.ASSIGNED
    )
    assigned_at = models.DateTimeField("przydzielona", default=timezone.now)
    submitted_at = models.DateTimeField("wystawiona", null=True, blank=True)

    objects = ReviewQuerySet.as_manager()

    class Meta:
        verbose_name = "recenzja"
        verbose_name_plural = "recenzje"
        ordering = ("submission", "round", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["submission", "reviewer", "round"], name="grading_review_unique_assignment"
            ),
            models.CheckConstraint(condition=Q(round__gte=1), name="grading_review_round_positive"),
            # Wystawiona recenzja musi mieć punkty. Bez tego rozjazd/konsensus liczyłby się z None.
            models.CheckConstraint(
                condition=~Q(status=ReviewStatus.SUBMITTED) | Q(score__isnull=False),
                name="grading_review_submitted_has_score",
            ),
        ]

    def __str__(self) -> str:
        return f"recenzja {self.pk} (zgł. {self.submission_id}, runda {self.round}, {self.status})"

    @property
    def is_submitted(self) -> bool:
        return self.status == ReviewStatus.SUBMITTED

    @property
    def participant_public_code(self) -> str:
        """Jedyny identyfikator uczestnika, jaki wolno pokazać recenzentowi (ocenianie ślepe)."""
        return self.submission.entry.participant.public_code

    def public_annotations(self) -> list:
        """Adnotacje oznaczone ``public`` – tylko te trafiają kiedyś do uczestnika (T-07)."""
        return [item for item in (self.annotations or []) if isinstance(item, dict) and item.get("public")]


class FinalGrade(models.Model):
    """Ocena uzgodniona rozwiązania. Jedna na ``Submission`` – relacja pilnuje tego w bazie."""

    submission = models.OneToOneField(Submission, on_delete=models.CASCADE, related_name="final_grade")
    score = models.PositiveSmallIntegerField("punkty")
    method = models.CharField("tryb ustalenia", max_length=16, choices=GradeMethod.choices)
    # CONSENSUS nie ma człowieka podejmującego decyzję – zgodność dwóch ocen wynika z reguły,
    # a nie z czyjegoś rozstrzygnięcia. Stąd pole jest nullable.
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="final_grades",
        verbose_name="rozstrzygnął",
    )
    decided_at = models.DateTimeField("rozstrzygnięta", default=timezone.now)
    rationale = models.TextField("uzasadnienie", blank=True)

    class Meta:
        verbose_name = "ocena uzgodniona"
        verbose_name_plural = "oceny uzgodnione"
        ordering = ("-decided_at", "-id")

    def __str__(self) -> str:
        return f"{self.score} pkt ({self.method}) dla zgł. {self.submission_id}"
