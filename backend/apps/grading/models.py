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
from apps.competitions.models import Problem
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
    # Recenzja bezprzedmiotowa: rozjazd rozstrzygnął ktoś inny (koordynator na posiedzeniu), więc
    # wiszący przydział rundy 2 nie może zostać w ASSIGNED – recenzent widziałby zadanie do zrobienia,
    # którego już nie da się wykonać. Kasowanie odpadło: ślad po przydziale ma zostać.
    CANCELLED = "CANCELLED", "anulowana"


class ReviewCancelReason(models.TextChoices):
    """Dlaczego recenzja przestała być aktualna – powód widoczny dla recenzenta, nie tylko w audycie.

    Anulowanie wygląda z panelu recenzenta tak samo w każdym przypadku: zadanie znika z listy.
    Powody są jednak zupełnie różne i recenzent ma prawo je rozróżnić – „koordynator odebrał Ci tę
    pracę” jest informacją o decyzji organizatora, a „uczestnik wysłał nową wersję” o tym, że jego
    praca nie zniknęła, tylko czeka na ocenę od nowa. Pusta wartość znaczy „recenzja anulowana przed
    wprowadzeniem tego pola” (wiersze sprzed migracji) – wtedy panel pokazuje treść ogólną.
    """

    COORDINATOR = "COORDINATOR", "koordynator odebrał pracę"
    SUPERSEDED = "SUPERSEDED", "nowa wersja rozwiązania"
    OVERRIDE = "OVERRIDE", "korekta oceny przez koordynatora"
    MODERATION_RESOLVED = "MODERATION_RESOLVED", "rozjazd rozstrzygnięty"
    REVISED = "REVISED", "recenzent poprawił ocenę"


class GradeMethod(models.TextChoices):
    CONSENSUS = "CONSENSUS", "zgodne oceny"
    THIRD_REVIEW = "THIRD_REVIEW", "trzeci recenzent"
    MODERATION = "MODERATION", "posiedzenie komisji"
    APPEAL = "APPEAL", "po reklamacji"
    # Korekta koordynatora ma **własny** tryb, a nie MODERATION: posiedzenie komisji rozstrzyga
    # rozjazd dwóch ocen, a to jest jednoosobowa decyzja organizatora – czasem dla pracy, której
    # nikt nie recenzował. Rozróżnienie jest widoczne w tabeli wyników i w aktach odwoławczych,
    # więc nie wolno go schować pod istniejącą wartością.
    COORDINATOR_OVERRIDE = "OVERRIDE", "korekta koordynatora"


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
    # Powód anulowania jest osobnym polem, a nie kolejną wartością ``status``: stan recenzji
    # („anulowana”) i przyczyna („bo przyszła nowa wersja pracy”) to dwie niezależne informacje,
    # a rozbicie statusu na pięć wartości zmusiłoby każdy filtr po CANCELLED do wyliczania ich listy.
    cancel_reason = models.CharField(
        "powód anulowania",
        max_length=24,
        choices=ReviewCancelReason.choices,
        blank=True,
        default="",
    )
    assigned_at = models.DateTimeField("przydzielona", default=timezone.now)
    submitted_at = models.DateTimeField("wystawiona", null=True, blank=True)
    # Osobne pole, a nie nadpisanie ``submitted_at``: chwila pierwszego wystawienia oceny jest
    # faktem procesowym (czy recenzent zdążył przed terminem recenzji) i poprawka nie może jej
    # zacierać. ``None`` znaczy „nie poprawiano”.
    revised_at = models.DateTimeField("poprawiona", null=True, blank=True)

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


class ProblemReviewerRule(models.Model):
    """Reguła „to zadanie recenzuje ta osoba” – przydział z góry, na poziomie całego zadania.

    Powód istnienia: automat równoważy obciążenie, ale nie zna podziału kompetencji w komitecie.
    Organizator chce móc powiedzieć „zadanie 3 sprawdza Kowalski” raz, zamiast klikać przy każdej
    pracy z osobna. Reguła jest więc **deklaracją**, a nie jednorazową akcją: obowiązuje zarówno
    prace już zablokowane (serwis dopisuje recenzje w chwili utworzenia reguły), jak i te, które
    wejdą do oceniania później (uwzględnia je najbliższy przebieg ``assign_reviewers``).

    Reguła nie unieważnia konfliktu interesów: recenzent w konflikcie z konkretnym uczestnikiem
    jest dla tej jednej pracy pomijany (``RULE_REVIEWER_CONFLICT``), a nie dopisywany „bo tak
    kazał organizator”. Kasowanie reguły nie rusza recenzji, które już powstały – przydział, który
    ktoś zaczął wykonywać, znika wyłącznie przez świadome cofnięcie (``unassign_reviewer``).

    ``on_delete=CASCADE`` po obu stronach: reguła bez zadania albo bez recenzenta nie ma sensu,
    a ślad po samej deklaracji zostaje w audycie (``review.rule_added`` / ``review.rule_removed``).
    """

    problem = models.ForeignKey(Problem, on_delete=models.CASCADE, related_name="reviewer_rules")
    reviewer = models.ForeignKey(CommitteeMember, on_delete=models.CASCADE, related_name="problem_rules")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="problem_reviewer_rules",
        verbose_name="utworzył",
    )
    created_at = models.DateTimeField("utworzona", default=timezone.now)

    class Meta:
        verbose_name = "reguła przydziału zadania"
        verbose_name_plural = "reguły przydziału zadań"
        # Kolejność tworzenia jest częścią semantyki: recenzenci z reguł wchodzą na miejsca
        # ``per_submission`` w tej właśnie kolejności, więc sortowanie nie może być dowolne.
        ordering = ("problem", "created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["problem", "reviewer"], name="grading_problem_rule_unique_reviewer"
            )
        ]

    def __str__(self) -> str:
        return f"zadanie {self.problem_id} → recenzent {self.reviewer_id}"
