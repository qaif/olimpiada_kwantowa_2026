"""Reguły przypomnień: zapis z audytem i zamiana reguły w komunikaty planowane (MSG-AUTO-01).

**Jedna droga generowania** – :func:`sync_rule`. Woła ją beat co kilka minut (``tasks``) i ekran
zaraz po zapisie, przełączeniu albo usunięciu reguły, więc koordynator widzi skutek od razu
w „Komunikaty → Zaplanowane”, a nie po pięciu minutach.

Przebieg dla jednej reguły (wiersz reguły zablokowany ``select_for_update(skip_locked=True)`` –
drugi worker z tym samym przebiegiem pomija regułę zamiast generować drugi raz):

1. wystąpienia reguły liczy ``planning`` (czyste funkcje: terminy w czasie polskim, wypełniony tekst),
2. czekające komunikaty reguły, które **przestały być aktualne** (warsztat odwołany albo usunięty,
   reguła wyłączona, zmieniony tekst lub termin) są wycofywane warunkowym
   ``UPDATE … WHERE status = SCHEDULED`` – tak samo jak „Anuluj”, więc wycofanie przegrywa czysto
   z równoległą wysyłką – a ich wiersz wystąpienia znika, żeby nowa wersja mogła powstać,
3. wystąpienia w oknie (``planning.in_window``) bez wiersza wystąpienia stają się komunikatami
   przez ``schedule_broadcast`` – jedyną drogę planowania, z jej walidacją, rolą aktora (autor
   reguły) i audytem ``broadcast.scheduled``.

**Idempotencja i „Anuluj”.** Wiersz ``MessageRuleOccurrence`` zostaje po ręcznym anulowaniu
komunikatu, więc anulowane przypomnienie nie wraca w następnym przebiegu. Usuwa go wyłącznie krok 2
– czyli wycofanie, które zrobiło samo generowanie.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext

from apps.accounts.messaging import schedule_broadcast
from apps.accounts.models import BroadcastGroup, BroadcastStatus, CompetitionRole, MessageBroadcast
from apps.core.models import audit

from . import planning
from .models import MessageRule, MessageRuleOccurrence, RuleKind

logger = logging.getLogger(__name__)

#: Pola reguły, które zapisuje ekran (i powłoka). Do audytu ``message_rule.updated`` trafiają
#: **nazwy** zmienionych pól – nigdy szablony ani link, tak jak ``broadcast.*`` nie niesie treści.
RULE_FIELDS = (
    "name",
    "enabled",
    "group",
    "parameters",
    "target",
    "stage",
    "subject_template",
    "body_template",
    "link",
    "timing",
    "days_before",
    "send_time",
    "hours_before",
)


@dataclass
class SyncResult:
    """Wynik przebiegu dla jednej reguły – do logu, komunikatu na ekranie i testów."""

    created: int = 0
    withdrawn: int = 0
    error: str = ""
    locked: bool = False


# --- zapis reguły ----------------------------------------------------------------------------------


def _require_coordinator(actor, competition) -> None:
    """Rola w serwisie, nie tylko w widoku – do serwisu prowadzi też ``manage.py shell``."""
    from apps.accounts.services import has_role

    if competition is None or not has_role(actor, competition, CompetitionRole.COORDINATOR):
        raise PermissionDenied(gettext("Reguły przypomnień zmienia wyłącznie koordynator tego konkursu."))


def validate_rule(rule: MessageRule) -> None:
    """Kształt reguły niezależnie od formularza: grupa, szablon, etap konkursu, moment wysyłki."""
    if rule.group == BroadcastGroup.CUSTOM or rule.group not in BroadcastGroup.values:
        raise ValidationError(gettext("Ta grupa odbiorców nie nadaje się do przypomnień automatycznych."))
    errors = planning.template_errors(
        rule.kind, subject=rule.subject_template, body=rule.body_template, link=rule.link
    )
    if errors:
        raise ValidationError(errors)
    if rule.kind == RuleKind.STAGE_DEADLINE:
        if rule.stage is None or rule.stage.edition.competition_id != rule.competition_id:
            raise ValidationError(gettext("Wybierz etap tego konkursu."))
    if rule.timing == "HOURS_BEFORE":
        if rule.kind != RuleKind.WORKSHOP or not rule.hours_before:
            raise ValidationError(
                gettext("„N godzin przed” działa wyłącznie dla warsztatów i wymaga liczby godzin.")
            )
    elif not rule.days_before:
        raise ValidationError(gettext("Podaj przynajmniej jedną liczbę dni przed wydarzeniem."))


def _rule_audit(rule: MessageRule, extra: dict | None = None) -> dict:
    """Opis reguły do audytu – rodzaj, grupa, moment wysyłki; bez szablonów i linku."""
    return {
        "name": rule.name,
        "kind": rule.kind,
        "group": rule.group,
        "target": rule.target,
        "stage": rule.stage_id,
        "timing": rule.timing,
        "days_before": rule.days_before,
        "send_time": rule.send_time.strftime("%H:%M") if rule.send_time else None,
        "hours_before": rule.hours_before,
        "enabled": rule.enabled,
        **(extra or {}),
    }


def _sync_now(rule: MessageRule) -> SyncResult:
    """Przebieg generowania zaraz po zmianie reguły – w tej samej transakcji, ale nigdy kosztem zapisu.

    Wiersz reguły jest już zablokowany przez tę transakcję (zapis), więc ``select_for_update`` w
    ``sync_rule`` dostaje go od razu. Nieoczekiwany wyjątek generowania nie cofa zapisu reguły:
    ``sync_rule`` biegnie w punkcie zapisu (``atomic``), a beat i tak powtórzy przebieg za kilka minut.
    """
    try:
        return sync_rule(rule.pk)
    except Exception:
        logger.exception("Reguła %s: przebieg po zmianie nieudany – zrobi go beat.", rule.pk)
        return SyncResult(error=gettext("Generowanie nie powiodło się – ponowimy za kilka minut."))


@transaction.atomic
def create_rule(*, competition, actor, kind: str, request=None, **fields) -> tuple[MessageRule, SyncResult]:
    """Nowa reguła + natychmiastowy przebieg generowania (``sync_rule``)."""
    _require_coordinator(actor, competition)
    rule = MessageRule(competition=competition, kind=kind, created_by=actor)
    for name, value in fields.items():
        if name not in RULE_FIELDS:
            raise ValidationError(gettext("Nieznane pole reguły: %(name)s.") % {"name": name})
        setattr(rule, name, value)
    validate_rule(rule)
    rule.save()
    audit(actor, "message_rule.created", rule, _rule_audit(rule), request=request)
    return rule, _sync_now(rule)


@transaction.atomic
def update_rule(rule: MessageRule, *, actor, request=None, **fields) -> tuple[list[str], SyncResult]:
    """Zmiana reguły; zwraca nazwy zmienionych pól i wynik przebiegu, który odświeżył czekające komunikaty.

    Autor reguły (aktor przyszłych komunikatów) **zostaje** – zmiana treści przez drugiego
    koordynatora nie przenosi na niego odpowiedzialności za listy, ale trafia do audytu z jego kontem.
    """
    _require_coordinator(actor, rule.competition)
    changed = []
    for name, value in fields.items():
        if name not in RULE_FIELDS:
            raise ValidationError(gettext("Nieznane pole reguły: %(name)s.") % {"name": name})
        if getattr(rule, name) != value:
            setattr(rule, name, value)
            changed.append(name)
    if not changed:
        return [], SyncResult()
    validate_rule(rule)
    rule.save()
    audit(actor, "message_rule.updated", rule, _rule_audit(rule, {"changed": changed}), request=request)
    return changed, _sync_now(rule)


@transaction.atomic
def set_enabled(rule: MessageRule, enabled: bool, *, actor, request=None) -> SyncResult:
    """Włączenie albo wyłączenie. Wyłączenie wycofuje czekające komunikaty reguły (``sync_rule``)."""
    _require_coordinator(actor, rule.competition)
    if rule.enabled == enabled:
        return SyncResult()
    rule.enabled = enabled
    rule.save(update_fields=["enabled", "updated_at"])
    action = "message_rule.enabled" if enabled else "message_rule.disabled"
    audit(actor, action, rule, _rule_audit(rule), request=request)
    return _sync_now(rule)


@transaction.atomic
def delete_rule(rule: MessageRule, *, actor, request=None) -> int:
    """Usuwa regułę i wycofuje jej czekające komunikaty. Zwraca liczbę wycofanych.

    Komunikaty już wysłane (i anulowane ręcznie) zostają w historii – wiersze wystąpień znikają
    razem z regułą, a komunikat jest zwykłym wierszem rejestru wysyłek.
    """
    _require_coordinator(actor, rule.competition)
    withdrawn = 0
    for occurrence in rule.occurrences.select_related("broadcast"):
        if _withdraw(rule, occurrence, "rule_deleted"):
            withdrawn += 1
    audit(actor, "message_rule.deleted", rule, _rule_audit(rule, {"withdrawn": withdrawn}), request=request)
    rule.delete()
    return withdrawn


# --- generowanie -----------------------------------------------------------------------------------


def rule_plan(rule: MessageRule, *, rows: list[dict] | None = None) -> planning.Plan:
    """Wszystkie wystąpienia reguły – harmonogram warsztatów **jej** konkursu albo termin jej etapu."""
    if rule.kind == RuleKind.WORKSHOP:
        if rows is None:
            from apps.cms.workshops import workshops_page

            rows = planning.schedule_rows(workshops_page(rule.competition))
        return planning.plan_workshops(rule, rows)
    stage = None
    if rule.stage_id:
        from apps.competitions.models import Stage

        stage = Stage.objects.filter(pk=rule.stage_id).select_related("edition").first()
    return planning.plan_stage(rule, stage)


def _broadcast_target(rule: MessageRule) -> dict:
    """Opis grupy wygenerowanego komunikatu: opis z reguły + skąd się wziął (historia, audyt)."""
    target = dict(rule.target or {})
    origin = gettext("przypomnienie automatyczne „%(name)s”") % {"name": rule.name}
    label = target.get("label")
    target["label"] = f"{label} · {origin}" if label else origin
    target["message_rule"] = rule.pk
    return target


def _withdraw(rule: MessageRule, occurrence: MessageRuleOccurrence, reason: str) -> bool:
    """Wycofuje czekający komunikat wystąpienia i usuwa wiersz wystąpienia. ``False`` – już nie czeka.

    Warunkowy ``UPDATE`` zamiast ``cancel_scheduled_broadcast``: tamta funkcja sprawdza rolę
    **klikającego** koordynatora, a tu decyzję podejmuje system (zmiana harmonogramu, reguła
    wyłączona) – aktor audytu jest systemowy, a powód stoi w ``reason``.
    """
    broadcast = occurrence.broadcast
    if broadcast is None:
        return False
    updated = MessageBroadcast.objects.filter(pk=broadcast.pk, status=BroadcastStatus.SCHEDULED).update(
        status=BroadcastStatus.CANCELLED
    )
    if not updated:
        return False
    from apps.tenancy.context import competition_context

    with competition_context(rule.competition):
        audit(
            None,
            "broadcast.cancelled",
            broadcast,
            {
                "group": broadcast.group,
                "target": broadcast.target,
                "scheduled_for": broadcast.scheduled_for.isoformat() if broadcast.scheduled_for else None,
                "reason": reason,
                "message_rule": rule.pk,
            },
        )
    occurrence.delete()
    logger.info("Reguła %s: wycofano komunikat %s (%s).", rule.pk, broadcast.pk, reason)
    return True


def sync_rule(rule_id: int, *, now: dt.datetime | None = None) -> SyncResult:
    """Jeden przebieg generowania dla jednej reguły – patrz docstring modułu."""
    now = now or timezone.now()
    with transaction.atomic():
        rule = MessageRule.objects.select_for_update(skip_locked=True).filter(pk=rule_id).first()
        if rule is None:
            return SyncResult(locked=True)
        from apps.tenancy.context import competition_context

        with competition_context(rule.competition):
            return _sync_locked(rule, now)


def _sync_locked(rule: MessageRule, now: dt.datetime) -> SyncResult:
    result = SyncResult()
    plan = rule_plan(rule) if rule.enabled else planning.Plan()
    wanted = {occurrence.key: occurrence for occurrence in plan.occurrences}
    existing = {row.key: row for row in rule.occurrences.select_related("broadcast")}

    for key, row in list(existing.items()):
        if row.broadcast is None or row.broadcast.status != BroadcastStatus.SCHEDULED:
            continue
        want = wanted.get(key)
        if want is None:
            reason = "rule_disabled" if not rule.enabled else "source_gone"
        elif planning.fingerprint(rule, want) != row.fingerprint and planning.in_window(want.send_at, now):
            # Nowa wersja da się jeszcze zaplanować – stara ustępuje. Zmiana na mniej niż 5 minut
            # przed wysyłką zostawia starą wersję: lepiej list z literówką niż żaden.
            reason = "rule_changed"
        else:
            continue
        if _withdraw(rule, row, reason):
            del existing[key]
            result.withdrawn += 1

    errors: list[str] = []
    for occurrence in plan.occurrences:
        if occurrence.key in existing or not planning.in_window(occurrence.send_at, now):
            continue
        if result.created >= planning.MAX_PER_RUN:
            break
        try:
            with transaction.atomic():
                broadcast = schedule_broadcast(
                    group=rule.group,
                    subject=occurrence.subject,
                    body=occurrence.body,
                    scheduled_for=occurrence.send_at,
                    competition=rule.competition,
                    actor=rule.created_by,
                    parameters=rule.parameters or {},
                    target=_broadcast_target(rule),
                )
                MessageRuleOccurrence.objects.create(
                    rule=rule,
                    key=occurrence.key,
                    broadcast=broadcast,
                    send_at=occurrence.send_at,
                    fingerprint=planning.fingerprint(rule, occurrence),
                )
        except (ValidationError, IntegrityError) as error:
            message = "; ".join(getattr(error, "messages", None) or [str(error)])
            errors.append(message)
            logger.warning(
                "Reguła %s, wystąpienie %s: nie zaplanowano – %s", rule.pk, occurrence.key, message
            )
            continue
        result.created += 1

    result.error = errors[0][:500] if errors else ""
    rule.last_run_at = now
    rule.last_error = result.error
    # ``update()``, a nie ``save()``: przebieg beatu nie jest zmianą reguły i nie rusza ``updated_at``.
    MessageRule.objects.filter(pk=rule.pk).update(last_run_at=now, last_error=result.error)
    if result.created or result.withdrawn:
        logger.info("Reguła %s: zaplanowano %s, wycofano %s.", rule.pk, result.created, result.withdrawn)
    return result


def generate_all(*, now: dt.datetime | None = None) -> int:
    """Przebieg beatu: reguły włączone i wyłączone z czekającymi komunikatami. Zwraca liczbę nowych.

    Wyjątek przy jednej regule nie zatrzymuje pozostałych – trafia do logu, a reguła wraca
    w następnym przebiegu (ta sama zasada, co w ``dispatch_due_broadcasts``).
    """
    rule_ids = list(
        MessageRule.objects.filter(
            Q(enabled=True) | Q(occurrences__broadcast__status=BroadcastStatus.SCHEDULED)
        )
        .order_by("pk")
        .values_list("pk", flat=True)
        .distinct()
    )
    created = 0
    for rule_id in rule_ids:
        try:
            created += sync_rule(rule_id, now=now).created
        except Exception:
            logger.exception("Reguła przypomnień %s: przebieg nieudany – ponowienie w następnym.", rule_id)
    return created


# --- podgląd ---------------------------------------------------------------------------------------


def upcoming(rule: MessageRule, *, now: dt.datetime | None = None, limit: int = 3, rows=None) -> dict:
    """Najbliższe wysyłki reguły z wypełnionym tekstem i stanem – do podglądu na ekranie.

    Działa także dla reguły **niezapisanej** (przycisk „Podgląd” przed zapisem): stanem jest wtedy
    tylko to, kiedy wystąpienie zostałoby zaplanowane.
    """
    now = now or timezone.now()
    plan = rule_plan(rule, rows=rows)
    existing = {}
    if rule.pk is not None:
        existing = {row.key: row for row in rule.occurrences.select_related("broadcast")}
    items = []
    for occurrence in plan.occurrences:
        if occurrence.send_at <= now:
            continue
        items.append(
            {"occurrence": occurrence, "state": _state(rule, occurrence, existing.get(occurrence.key), now)}
        )
        if len(items) >= limit:
            break
    return {"items": items, "skipped": plan.skipped}


def _state(rule: MessageRule, occurrence: planning.Occurrence, row, now: dt.datetime) -> str:
    """Stan wystąpienia słowami – co koordynator zobaczy w „Zaplanowanych” i kiedy."""
    from django.utils.formats import date_format

    if row is not None:
        broadcast = row.broadcast
        if broadcast is None:
            return gettext("obsłużone")
        if broadcast.status == BroadcastStatus.SCHEDULED:
            return gettext("zaplanowany – widać go w „Komunikaty → Zaplanowane”")
        if broadcast.status == BroadcastStatus.CANCELLED:
            return gettext("anulowany – nie zostanie wygenerowany ponownie")
        return broadcast.get_status_display()
    if not rule.enabled:
        return gettext("reguła wyłączona – nie zostanie zaplanowany")
    if occurrence.send_at < now + planning.SCHEDULE_MIN_LEAD:
        return gettext("za blisko terminu – nie zostanie wysłany")
    if occurrence.send_at > now + planning.GENERATION_HORIZON:
        when = timezone.localtime(occurrence.send_at - planning.GENERATION_HORIZON)
        return gettext("zostanie zaplanowany %(when)s") % {"when": date_format(when, "j E Y, H:i")}
    return gettext("zostanie zaplanowany w ciągu kilku minut")
