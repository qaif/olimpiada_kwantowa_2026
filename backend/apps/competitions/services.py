"""Logika domenowa zawodów.

Widoki tylko orkiestrują: walidacja reguł biznesowych, tworzenie obiektów zależnych i błędy
domenowe (``DomainError``) żyją tutaj. Czas zawsze przez ``timezone.now()``.
"""

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status

from apps.accounts.models import Participant
from apps.core.api import DomainError

from .models import (
    DEFAULT_MAX_VALUE,
    Edition,
    QualificationMode,
    QualificationRule,
    ScoringScale,
    Stage,
    StageEntry,
    StageEntryStatus,
    StageKind,
    default_scoring_values,
)


def current_edition() -> Edition | None:
    """Bieżąca edycja albo ``None``. Unikalność ``is_current`` gwarantuje constraint w bazie."""
    return Edition.objects.filter(is_current=True).first()


def current_stage(edition: Edition, now=None) -> Stage | None:
    """Etap „na teraz”: otwarty, a jeśli żaden nie jest otwarty – najbliższy przyszły, inaczej ostatni.

    Kolejność jest wyznaczana po ``opens_at``, nie po rodzaju etapu – terminy są jedynym źródłem prawdy.
    """
    now = now or timezone.now()
    stages = list(edition.stages.order_by("opens_at", "id"))
    if not stages:
        return None
    for stage in stages:
        if stage.is_open_for_submissions(now):
            return stage
    upcoming = [stage for stage in stages if stage.opens_at > now]
    if upcoming:
        return upcoming[0]
    return stages[-1]


@transaction.atomic
def create_stage(
    *,
    edition: Edition,
    kind: str,
    opens_at,
    deadline_at,
    review_deadline_at,
    appeal_window_opens_at,
    appeal_window_closes_at,
    grace_seconds: int = 0,
    location: str = "",
    scoring_values: list[dict] | None = None,
    max_value: int | None = None,
    qualification_mode: str = QualificationMode.MIN_POINTS,
    min_points: int | None = 0,
    top_n: int | None = None,
) -> Stage:
    """Tworzy etap wraz z domyślną skalą punktacji i progiem kwalifikacji.

    Obiekty zależne powstają tu, a nie w sygnale ``post_save`` – dzięki temu fabryki testowe
    tworzą dokładnie to, co deklarują, a zachowanie serwisu jest jawne i testowalne.
    """
    stage = Stage(
        edition=edition,
        kind=kind,
        location=location,
        opens_at=opens_at,
        deadline_at=deadline_at,
        grace_seconds=grace_seconds,
        review_deadline_at=review_deadline_at,
        appeal_window_opens_at=appeal_window_opens_at,
        appeal_window_closes_at=appeal_window_closes_at,
    )
    stage.full_clean()
    stage.save()

    values = scoring_values if scoring_values is not None else default_scoring_values()
    scale = ScoringScale(
        stage=stage,
        values=values,
        max_value=max_value if max_value is not None else DEFAULT_MAX_VALUE,
    )
    scale.full_clean()
    scale.save()

    rule = QualificationRule(stage=stage, mode=qualification_mode, min_points=min_points, top_n=top_n)
    rule.full_clean()
    rule.save()
    return stage


def ensure_stage_defaults(stage: Stage) -> None:
    """Dopina domyślną skalę i próg do etapu utworzonego z pominięciem ``create_stage`` (np. admin)."""
    ScoringScale.objects.get_or_create(
        stage=stage, defaults={"values": default_scoring_values(), "max_value": DEFAULT_MAX_VALUE}
    )
    QualificationRule.objects.get_or_create(
        stage=stage, defaults={"mode": QualificationMode.MIN_POINTS, "min_points": 0}
    )


def _registration_closed() -> DomainError:
    return DomainError(
        "Rejestracja do tego etapu jest zamknięta.",
        "REGISTRATION_CLOSED",
        status.HTTP_403_FORBIDDEN,
    )


def _already_registered() -> DomainError:
    return DomainError(
        "Uczestnik jest już zarejestrowany do tego etapu.",
        "ALREADY_REGISTERED",
        status.HTTP_409_CONFLICT,
    )


@transaction.atomic
def register_for_stage(participant: Participant, stage: Stage, *, now=None) -> StageEntry:
    """Samodzielna rejestracja uczestnika do etapu eliminacyjnego.

    Do etapu okręgowego i finału wpisy tworzy wyłącznie kwalifikacja (T-07) – ręczna próba
    kończy się ``STAGE_NOT_OPEN_FOR_REGISTRATION``.
    """
    now = now or timezone.now()
    if stage.kind != StageKind.ELIM:
        raise DomainError(
            "Do tego etapu wpisy tworzy kwalifikacja, nie rejestracja.",
            "STAGE_NOT_OPEN_FOR_REGISTRATION",
            status.HTTP_403_FORBIDDEN,
        )
    if not stage.edition.is_current or not stage.is_open_for_submissions(now):
        # Etap edycji archiwalnej nie przyjmuje rejestracji, nawet jeśli jego terminy są "otwarte".
        raise _registration_closed()
    if StageEntry.objects.filter(participant=participant, stage=stage).exists():
        raise _already_registered()
    try:
        # Savepoint: kolizja unikalności przy wyścigu nie może unieważnić całej transakcji żądania.
        with transaction.atomic():
            return StageEntry.objects.create(
                participant=participant, stage=stage, status=StageEntryStatus.REGISTERED
            )
    except IntegrityError as exc:
        raise _already_registered() from exc


def entries_for_user(user):
    """Wpisy widoczne dla użytkownika. Filtr jest w querysecie, nie w widoku (PROJEKT.md 2.3)."""
    return (
        StageEntry.objects.select_related("stage", "stage__edition", "participant")
        .for_user(user)
        .order_by("stage__opens_at", "id")
    )
