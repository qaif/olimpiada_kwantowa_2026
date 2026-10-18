"""Zadania Celery reguły zaświadczeń z warsztatów: po zapisie obecności i „Przelicz teraz”.

Kolejka ``default`` – to praca domenowa (numer, wiersz, audyt), a nie wysyłka ani skan. Listy
„gotowe” zadanie oddaje ``send_mail_task`` na kolejce ``mail``, więc trzysta zaświadczeń nie zatrzymuje
poczty serwisu. Oba zadania są idempotentne (reguła wystawia tylko brakujące dokumenty), więc
ponowne dostarczenie niczego nie dubluje.
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="apps.workshop_certificates.tasks.issue_for_participants")
def issue_for_participants(participant_ids: list[int]) -> int:
    """Po zapisie obecności: ocenia tylko tych uczestników, w edycji bieżącej **ich** konkursu.

    Uczestnicy jednego zapisu należą zwykle do jednego konkursu, ale zadanie tego nie zakłada:
    grupuje ich po konkursie profilu i każdą grupę liczy w kontekście jej konkursu (audyt, adresy
    w listach, nadawca), żeby obecność z olimpiady B nie dotknęła reguły olimpiady A.
    """
    from apps.accounts.models import Participant
    from apps.competitions.services import current_edition
    from apps.tenancy.context import competition_context
    from apps.tenancy.models import Competition

    from .models import AutoIssueTrigger
    from .rules import issue_due

    by_competition: dict[int, list[int]] = {}
    for participant_id, competition_id in Participant.objects.filter(pk__in=participant_ids).values_list(
        "pk", "competition_id"
    ):
        by_competition.setdefault(competition_id, []).append(participant_id)
    issued = 0
    for competition in Competition.objects.filter(pk__in=list(by_competition)):
        with competition_context(competition):
            edition = current_edition(competition)
            if edition is None:
                continue
            result = issue_due(
                edition, trigger=AutoIssueTrigger.ATTENDANCE, participant_ids=by_competition[competition.pk]
            )
            issued += len(result.issued)
    if issued:
        logger.info("Reguła warsztatów wystawiła %s zaświadczeń po zapisie obecności.", issued)
    return issued


@shared_task(name="apps.workshop_certificates.tasks.recalculate_edition")
def recalculate_edition(edition_id: int, actor_id: int | None = None) -> int:
    """„Przelicz teraz”: cała edycja – np. po włączeniu reguły albo obniżeniu progu."""
    from django.contrib.auth import get_user_model

    from apps.competitions.models import Edition
    from apps.tenancy.context import competition_context

    from .models import AutoIssueTrigger
    from .rules import issue_due

    edition = Edition.objects.filter(pk=edition_id).select_related("competition").first()
    if edition is None:
        return 0
    actor = get_user_model().objects.filter(pk=actor_id).first() if actor_id else None
    with competition_context(edition.competition):
        result = issue_due(edition, trigger=AutoIssueTrigger.RECALCULATE, actor=actor)
    return len(result.issued)
