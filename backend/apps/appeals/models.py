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
- konflikt interesów ma **jedną** definicję: ``CONFLICTING_ROUNDS`` niżej. Queryset kolejki, serwis
  decyzji i widoczność rozwiązań (``submissions.Submission.objects.for_user``) czytają tę samą stałą,
  żeby nie dało się rozjechać listy „nie widzi” z listą „nie może rozstrzygnąć”,
- czas zawsze przez ``django.utils.timezone.now()``.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.accounts.models import CommitteeMember, Participant
from apps.grading.models import ROUND_BLIND, ROUND_TIEBREAK, Review
from apps.submissions.models import Submission

#: Minimalna długość uzasadnienia reklamacji (T-06). Krótsze zgłoszenie nie jest odwołaniem.
MIN_ARGUMENT_LENGTH = 50
#: Twardy limit na teksty od użytkownika (uzasadnienie reklamacji, uzasadnienie decyzji).
#: Dłuższy tekst jest odrzucany (400), a nie po cichu obcinany – obcięcie gubiłoby treść odwołania.
MAX_TEXT_LENGTH = 20000
#: Rundy, których autorzy są w konflikcie interesów przy reklamacji (PROJEKT.md 2.4).
CONFLICTING_ROUNDS = (ROUND_BLIND, ROUND_TIEBREAK)


class AppealStatus(models.TextChoices):
    OPEN = "OPEN", "złożona"
    REJECTED = "REJECTED", "odrzucona"
    ACCEPTED = "ACCEPTED", "uwzględniona"
    PARTIALLY_ACCEPTED = "PARTIALLY_ACCEPTED", "częściowo uwzględniona"


#: Statusy reklamacji czekającej na rozstrzygnięcie – to one trafiają do kolejki komisji.
#: Jest tylko ``OPEN``: reklamacja czeka albo jest rozstrzygnięta. Stan „rozpatrywana” (IN_REVIEW)
#: był w modelu, ale nic go nie ustawiało – wartość statusu, której nie da się osiągnąć, to tylko
#: pułapka dla klientów API (por. przegląd Critica T-06). Wróci razem z rezerwacją sprawy, jeśli
#: komisja będzie kiedyś przypisywać reklamacje do konkretnych osób.
PENDING_STATUSES = (AppealStatus.OPEN,)
#: Statusy, którymi wolno zamknąć reklamację.
DECIDABLE_STATUSES = (AppealStatus.REJECTED, AppealStatus.ACCEPTED, AppealStatus.PARTIALLY_ACCEPTED)
#: Rozstrzygnięcia wymagające zmiany punktacji.
SCORE_CHANGING_STATUSES = (AppealStatus.ACCEPTED, AppealStatus.PARTIALLY_ACCEPTED)


class AppealQuerySet(models.QuerySet):
    def pending(self):
        return self.filter(status__in=PENDING_STATUSES)

    def without_conflict_for(self, member: CommitteeMember | None):
        """Reklamacje, które ten członek komisji może rozpatrywać.

        Konflikt interesów: autor ``grading.Review`` z ``CONFLICTING_ROUNDS`` (runda 1 lub 2) tego
        rozwiązania nie może ani decydować, ani widzieć reklamacji na liście. Ta sama stała rządzi
        ``services.has_conflict_of_interest``, więc „nie widzę” i „nie mogę rozstrzygnąć” nie mogą
        się rozjechać. ``None`` (brak profilu) nie widzi niczego.

        Wykluczenie idzie przez podzapytanie po ``submission_id``, a nie przez ``exclude`` po
        złączeniu ``submission__reviews``: negacja na złączeniu wielowartościowym potrafi
        skorelować się z pojedynczym wierszem recenzji (wystarczy jedna cudza recenzja, żeby
        rozwiązanie i tak przeszło filtr).
        """
        if member is None:
            return self.none()
        conflicted = Review.objects.filter(reviewer=member, round__in=CONFLICTING_ROUNDS).values(
            "submission_id"
        )
        return self.exclude(submission_id__in=conflicted)


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
    # Przez jawny model pośredniczący, bo automatyczna tabela M2M kasuje wiersz razem z członkiem
    # komisji: skład, który podjął decyzję, znikałby po usunięciu konta i nie dałoby się odtworzyć,
    # kto rozstrzygał sprawę. ``AppealDecisionCommitteeMember.member`` ma ``PROTECT``, więc bazy
    # nie da się doprowadzić do stanu „decyzja bez składu”.
    committee = models.ManyToManyField(
        CommitteeMember,
        through="AppealDecisionCommitteeMember",
        related_name="appeal_decisions",
        verbose_name="skład komisji",
        blank=True,
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


class AppealDecisionCommitteeMember(models.Model):
    """Skład komisji przy jednej decyzji – model pośredniczący ``AppealDecision.committee``.

    ``PROTECT`` na członku komisji jest tu celem, a nie ostrożnością: decyzja odwoławcza jest
    dokumentem procedury i musi dać się odtworzyć, kto ją podjął. Usunięcie konta członka komisji
    ma się odbić o bazę (``ProtectedError``), a nie po cichu wyczyścić skład.
    """

    decision = models.ForeignKey(
        AppealDecision, on_delete=models.CASCADE, related_name="committee_seats", verbose_name="decyzja"
    )
    member = models.ForeignKey(
        CommitteeMember,
        on_delete=models.PROTECT,
        related_name="appeal_decision_seats",
        verbose_name="członek komisji",
    )

    class Meta:
        verbose_name = "członek składu decyzji"
        verbose_name_plural = "skład decyzji"
        ordering = ("decision", "member")
        constraints = [
            models.UniqueConstraint(fields=["decision", "member"], name="appeals_decision_member_unique"),
        ]

    def __str__(self) -> str:
        return f"decyzja {self.decision_id} / członek {self.member_id}"
