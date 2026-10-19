"""Zadania Celery: przełączniki etapu do webTEX – udostępnianie linków (``POST /api/integration/sharing``)
i asystent AI (``POST /api/integration/ai``).

Stan bierzemy **z bazy** w chwili wykonania, a nie z argumentu: dwa szybkie przełączenia koordynatora
kończą się w webTEX tym, co jest zapisane, niezależnie od kolejności, w jakiej zadania dojdą.
Niedostępny webTEX to ponowienie z rosnącym odstępem; wyczerpane ponowienia – błąd w logu
(koordynator może zapisać przełącznik jeszcze raz, a każde otwarcie edytora i tak niesie ``share``
i ``ai`` w tokenie).
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)

MAX_RETRIES = 8
RETRY_BASE_SECONDS = 15
RETRY_MAX_SECONDS = 30 * 60


def _push(task, stage_id: int, option: str, label: str) -> str:
    from apps.competitions.models import Stage

    from . import client, keys, services

    stage = Stage.objects.select_related("edition__competition").filter(pk=stage_id).first()
    if stage is None:
        return "SKIPPED"
    if not keys.configured():
        logger.warning(
            "Edytor LaTeX nie jest skonfigurowany – %s etapu %s nie poszło do webTEX.", label, stage_id
        )
        return "NOT_CONFIGURED"
    scope = services.scope_key(stage.edition.competition, stage)
    enabled = services.stage_options(stage)[option]
    send = client.set_ai if option == "ai_enabled" else client.set_sharing
    try:
        changed = send(scope, enabled)
    except client.WebtexError as exc:
        if task.request.retries >= task.max_retries:
            logger.error(
                "%s %s=%s nie doszło do webTEX po %s próbach: %s", label, scope, enabled, MAX_RETRIES, exc
            )
            return "FAILED"
        countdown = min(RETRY_BASE_SECONDS * (2**task.request.retries), RETRY_MAX_SECONDS)
        logger.warning("webTEX nie przyjął zmiany (%s) %s=%s: %s", label, scope, enabled, exc)
        raise task.retry(exc=exc, countdown=countdown) from exc
    logger.info("%s %s=%s w webTEX (zmienione projekty: %s).", label, scope, enabled, changed)
    return "OK"


@shared_task(bind=True, max_retries=MAX_RETRIES)
def push_stage_sharing(self, stage_id: int) -> str:
    return _push(self, stage_id, "sharing_enabled", "Udostępnianie linków")


@shared_task(bind=True, max_retries=MAX_RETRIES)
def push_stage_ai(self, stage_id: int) -> str:
    return _push(self, stage_id, "ai_enabled", "Asystent AI")
