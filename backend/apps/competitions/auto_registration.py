"""Automatyczny zapis nowych kont do etapu treningowego (REG-UX-01 § 3).

Prośba organizatora: trening jest piaskownicą „spróbuj, jak działa wysyłka i recenzja”, a nowi
uczestnicy nie wiedzą, że trzeba się do niego zapisać osobnym kliknięciem. Przełącznik
``Stage.auto_register_new_participants`` (tylko etap ``TRAINING``) zapisuje do niego każde nowe konto,
a akcja „Zapisz wszystkich obecnych uczestników” – tych, którzy już są.

**Kiedy konto jest „nowe”.** W chwili **pierwszej zgody RODO na profilu**, a nie w chwili założenia
profilu: import listy i zgłoszenie z delegacji zakładają profil **bez** zgód, a taki profil zapisać
się nie może (``register_for_stage`` → ``CONSENTS_MISSING``, audyt 10.10.2026, S6). Jedno zdarzenie
obejmuje więc wszystkie drogi: rejestrację formularzem i przez Google (profil powstaje ze zgodą),
przyjęcie zaproszenia z importu i zgłoszenie delegacji (``record_consents`` ustawia zgodę po raz
pierwszy). Ponowna zgoda na nową wersję dokumentu (``apps.consent_gate``) nie jest nowym kontem –
zgoda już była, więc zapisu nie ma.

**Dlaczego sygnał, a nie wywołanie w serwisach kont.** Dróg ustawienia zgody jest kilka i każda
mieszka w innym module (``accounts.services``, ``bulk_registration``, ``delegation_services``);
dopisanie wywołania w każdej znaczyłoby, że następna droga o nim zapomni. Sygnał patrzy na sam fakt
„zgoda RODO pojawiła się na profilu” i nie zmienia ani linijki w aplikacji kont.

**Błąd nie blokuje rejestracji.** Zapis idzie po zatwierdzeniu transakcji rejestracji
(``on_commit``, ``robust=True``), każdy etap osobno. Odmowa domenowa (etap zamknięty, już zapisany)
to wpis ``debug``; każdy inny wyjątek – ``logger.exception`` i nic więcej. Konto powstaje zawsze.
Idempotentnie: drugi zapis tego samego profilu kończy się ``ALREADY_REGISTERED`` i jest pomijany.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
from rest_framework import status

from apps.accounts.models import Participant
from apps.core.api import DomainError
from apps.core.models import audit

from .models import Stage, StageEntry, StageKind
from .services import register_for_stage

logger = logging.getLogger(__name__)

#: Znacznik na instancji między ``pre_save`` a ``post_save``: „ten zapis ustawia zgodę RODO po raz
#: pierwszy”. Atrybut instancji, a nie stan modułu – dwa równoległe zapisy nie mają go jak pomylić.
_PENDING_ATTR = "_reg_ux_auto_register_pending"


def _not_training() -> DomainError:
    return DomainError(
        "Automatyczny i zbiorczy zapis działa wyłącznie dla etapu treningowego.",
        "STAGE_NOT_TRAINING",
        status.HTTP_409_CONFLICT,
    )


def auto_stages(competition_id: int) -> list[Stage]:
    """Etapy treningowe **bieżącej** edycji konkursu z włączonym przełącznikiem."""
    return list(
        Stage.objects.filter(
            edition__competition_id=competition_id,
            edition__is_current=True,
            kind=StageKind.TRAINING,
            auto_register_new_participants=True,
        ).select_related("edition")
    )


def register_new_participant(participant_id: int) -> list[StageEntry]:
    """Zapis jednego profilu do etapów z przełącznikiem. Nigdy nie rzuca – patrz docstring modułu."""
    try:
        participant = (
            Participant.objects.select_related("user")
            .filter(pk=participant_id, competition__isnull=False)
            .first()
        )
        if participant is None or participant.gdpr_consent_at is None:
            return []
        stages = auto_stages(participant.competition_id)
    except Exception:
        logger.exception("Automatyczny zapis do treningu: nie udało się odczytać profilu %s.", participant_id)
        return []
    created: list[StageEntry] = []
    for stage in stages:
        try:
            created.append(register_for_stage(participant, stage))
        except DomainError as exc:
            logger.debug(
                "Automatyczny zapis do treningu pominięty (profil %s, etap %s): %s.",
                participant_id,
                stage.pk,
                exc.machine_code,
            )
        except Exception:
            logger.exception(
                "Automatyczny zapis do treningu nie powiódł się (profil %s, etap %s).",
                participant_id,
                stage.pk,
            )
    return created


@receiver(pre_save, sender=Participant, dispatch_uid="competitions.auto_registration.pre_save")
def _remember_first_consent(sender, instance: Participant, raw: bool = False, update_fields=None, **kwargs):
    """Czy ten zapis ustawia zgodę RODO po raz pierwszy? Pytanie do bazy wyłącznie, gdy może.

    Nowy profil ze zgodą – tak, bez pytania. Zapis istniejącego profilu – tylko gdy ``update_fields``
    zawiera ``gdpr_consent_at`` (tak zapisują ją ``record_consents`` i ``consent_gate``); pełny
    zapis profilu (edycja danych) nie kosztuje dodatkowego zapytania.
    """
    if raw or instance.gdpr_consent_at is None:
        return
    if instance._state.adding:
        setattr(instance, _PENDING_ATTR, True)
        return
    if update_fields is None or "gdpr_consent_at" not in update_fields:
        return
    if Participant.objects.filter(pk=instance.pk, gdpr_consent_at__isnull=True).exists():
        setattr(instance, _PENDING_ATTR, True)


@receiver(post_save, sender=Participant, dispatch_uid="competitions.auto_registration.post_save")
def _schedule_auto_registration(sender, instance: Participant, raw: bool = False, **kwargs):
    if raw or not getattr(instance, _PENDING_ATTR, False):
        return
    setattr(instance, _PENDING_ATTR, False)
    participant_id = instance.pk
    transaction.on_commit(lambda: register_new_participant(participant_id), robust=True)


def set_auto_registration(stage: Stage, enabled: bool, *, actor=None, request=None) -> Stage:
    """Przełącznik na etapie treningowym. Audyt ``stage.auto_registration_changed`` przy zmianie."""
    if stage.kind != StageKind.TRAINING:
        raise _not_training()
    enabled = bool(enabled)
    if stage.auto_register_new_participants == enabled:
        return stage
    stage.auto_register_new_participants = enabled
    stage.save(update_fields=["auto_register_new_participants"])
    audit(
        actor,
        "stage.auto_registration_changed",
        stage,
        {"auto_register_new_participants": enabled},
        request=request,
    )
    return stage


def register_all_current(stage: Stage, *, actor=None, request=None) -> dict:
    """„Zapisz wszystkich obecnych uczestników” – raport ``{registered, already, skipped}``.

    Kandydat: profil **tego konkursu**, bez anonimizacji, z aktywnym kontem i zgodą RODO, jeszcze
    bez wpisu w etapie. Każdy przez ``register_for_stage`` – ta sama reguła, co kliknięcie ucznia,
    więc profil, którego reguła nie wpuszcza, zostaje policzony jako pominięty, a nie zapisany obok
    niej. „Pominięci” to profile bez zgody albo z kontem nieaktywnym (np. zaproszenie z importu,
    którego uczeń jeszcze nie przyjął) – dostaną wpis automatycznie, gdy przełącznik jest włączony.

    Bez jednej wielkiej transakcji: wpisy powstają po jednym (każdy w savepoincie
    ``register_for_stage``), więc przerwane żądanie zostawia część zapisaną – a powtórzenie akcji
    dokończy resztę, bo już zapisani są pomijani.
    """
    from .services import _registration_closed

    if stage.kind != StageKind.TRAINING:
        raise _not_training()
    now = timezone.now()
    if not stage.edition.is_current or not stage.is_open_for_submissions(now):
        raise _registration_closed()
    profiles = Participant.objects.filter(competition_id=stage.edition.competition_id).exclude_anonymised()
    entered = StageEntry.objects.filter(stage=stage, participant__isnull=False).values("participant_id")
    total = profiles.count()
    already = profiles.filter(pk__in=entered).count()
    candidates = (
        profiles.filter(gdpr_consent_at__isnull=False, user__is_active=True)
        .exclude(pk__in=entered)
        .select_related("user")
        .order_by("pk")
    )
    registered = 0
    for participant in candidates.iterator():
        try:
            register_for_stage(participant, stage, now=now)
        except DomainError:
            continue
        registered += 1
    summary = {"registered": registered, "already": already, "skipped": total - already - registered}
    audit(actor, "stage.bulk_registered", stage, summary, request=request)
    logger.info(
        "Zbiorczy zapis do etapu %s: zapisano %s, już zapisanych %s, pominięto %s.",
        stage.pk,
        registered,
        already,
        summary["skipped"],
    )
    return summary
