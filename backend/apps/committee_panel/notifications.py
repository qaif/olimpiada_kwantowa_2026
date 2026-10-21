"""Haki powiadomień komisji (UX-PANEL-03 D11–D12) – wołane jedną linijką z ``grading`` i ``appeals``.

Te same zasady, co haki panelu uczestnika (``apps.notifications.hooks``):

- **nigdy nie przerywają źródła** – przydział ma się zapisać, a reklamacja złożyć, nawet gdy powiadomienie
  się nie uda (punkt zapisu + wpis w logu),
- **biegną w transakcji źródła** – wycofany przydział nie zostawia powiadomienia o nim,
- **nie niosą danych uczestnika**: parametrem jest nazwa etapu albo liczba prac, nigdy kod publiczny,
  punkty ani argument reklamacji (ocenianie jest ślepe, PROJEKT.md 2.2),
- odbiorcami są **wyłącznie członkowie komisji tego konkursu** – aktywny profil komitetu tego konkursu
  i rola (``has_role``: członkostwo albo grupa Django, zależnie od ``memberships_enforced``), liczone
  jednym zapytaniem na rolę, a nie ``has_role`` w pętli.

Klucz scalania (D12): jedno nieprzeczytane powiadomienie na etap (``review-assigned:<etap>``), na kolejkę
reklamacji (``appeals-queue``) i na termin (``review-deadline``) – kolejny przydział w tym samym etapie
podbija istniejące zamiast dokładać następne.
"""

from __future__ import annotations

import functools
import logging

from django.db import transaction

from apps.notifications import services
from apps.notifications.kinds import NotificationKind

logger = logging.getLogger(__name__)

#: Cel odnośników: kolejka recenzenta (zakładka / filtr rundy) i kolejka komisji odwoławczej.
REVIEW_QUEUE = "web:review-list"
APPEALS_QUEUE = "web:appeals"


def _safely(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            with transaction.atomic():
                return func(*args, **kwargs)
        except Exception:  # noqa: BLE001 - powiadomienie nie może wywrócić przydziału ani reklamacji
            logger.exception("Powiadomienie komisji nie powstało (%s).", func.__name__)
            return None

    return wrapper


def committee_user_ids(competition, user_ids, *, role: str, appeals: bool = False) -> set[int]:
    """Konta z ``user_ids``, które są członkami komisji **tego** konkursu w roli ``role``.

    Ta sama definicja, co ``active_reviewer_profile`` / ``appeals_committee_profile`` (aktywny profil
    komitetu tego konkursu, konto aktywne, rola w konkursie) – ale dla wielu kont naraz.
    """
    from apps.accounts.models import CommitteeMember, CommitteeStatus, Membership, User
    from apps.accounts.services import memberships_enforced

    ids = {int(pk) for pk in user_ids if pk is not None}
    if not ids or competition is None:
        return set()
    members = CommitteeMember.objects.filter(
        competition=competition, status=CommitteeStatus.ACTIVE, user_id__in=ids, user__is_active=True
    )
    if appeals:
        members = members.filter(is_appeals_committee=True)
    candidates = set(members.values_list("user_id", flat=True))
    if not candidates:
        return set()
    if memberships_enforced(competition):
        rows = Membership.objects.filter(competition=competition, role=role, user_id__in=candidates)
        return set(rows.values_list("user_id", flat=True))
    return set(User.objects.filter(pk__in=candidates, groups__name=role).values_list("pk", flat=True))


def _competition_of(stage):
    return stage.edition.competition


@_safely
def reviews_assigned(stage, user_ids) -> int:
    """Nowe przydziały rundy 1 w etapie (automat, ręczny przydział, reguła zadania)."""
    from apps.accounts.models import CompetitionRole

    competition = _competition_of(stage)
    recipients = committee_user_ids(competition, user_ids, role=CompetitionRole.REVIEWER)
    return services.notify_many(
        recipients,
        competition,
        NotificationKind.REVIEW_ASSIGNED,
        REVIEW_QUEUE,
        suffix="?tab=todo",
        params={"stage": stage.display_name},
        key=f"review-assigned:{stage.pk}",
    )


@_safely
def tiebreak_assigned(stage, user_id: int) -> int:
    """Rozjemca (runda 2) wyznaczony przez koordynatora przy rozbieżności ocen rundy 1."""
    from apps.accounts.models import CompetitionRole

    competition = _competition_of(stage)
    recipients = committee_user_ids(competition, [user_id], role=CompetitionRole.REVIEWER)
    return services.notify_many(
        recipients,
        competition,
        NotificationKind.REVIEW_TIEBREAK,
        REVIEW_QUEUE,
        suffix="?round=2",
        params={"stage": stage.display_name},
        key=f"review-tiebreak:{stage.pk}",
    )


@_safely
def deadline_reminder(competition, user_id: int, count: int) -> int:
    """Przypomnienie o terminie oceniania – obok listu z ``grading.tasks.remind_overdue_reviews``."""
    from apps.accounts.models import CompetitionRole

    recipients = committee_user_ids(competition, [user_id], role=CompetitionRole.REVIEWER)
    return services.notify_many(
        recipients,
        competition,
        NotificationKind.REVIEW_DEADLINE,
        REVIEW_QUEUE,
        suffix="?tab=todo",
        params={"count": int(count)},
        key="review-deadline",
    )


@_safely
def appeal_filed(appeal) -> int:
    """Nowa reklamacja: członkowie komisji odwoławczej tego konkursu **bez konfliktu interesów**.

    Konflikt (autor oceny rundy 1 albo 2 tej pracy) – ta sama stała, co kolejka
    (``appeals.models.CONFLICTING_ROUNDS``): kto nie zobaczy sprawy w kolejce, nie dostaje o niej
    powiadomienia.
    """
    from apps.accounts.models import CommitteeMember, CompetitionRole
    from apps.appeals.models import CONFLICTING_ROUNDS
    from apps.grading.models import Review

    submission = appeal.submission
    stage = submission.entry.stage
    competition = _competition_of(stage)
    conflicted = Review.objects.filter(submission=submission, round__in=CONFLICTING_ROUNDS).values(
        "reviewer_id"
    )
    user_ids = (
        CommitteeMember.objects.filter(competition=competition, is_appeals_committee=True)
        .exclude(pk__in=conflicted)
        .values_list("user_id", flat=True)
    )
    recipients = committee_user_ids(competition, user_ids, role=CompetitionRole.APPEALS, appeals=True)
    return services.notify_many(
        recipients,
        competition,
        NotificationKind.APPEAL_PENDING,
        APPEALS_QUEUE,
        params={"stage": stage.display_name},
        key="appeals-queue",
    )
