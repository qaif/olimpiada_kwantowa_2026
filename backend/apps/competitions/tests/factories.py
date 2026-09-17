"""Fabryki factory_boy dla domeny zawodów. Używane wyłącznie w testach.

Fabryki są jawne: ``StageFactory`` nie tworzy skali ani progu kwalifikacji – od tego jest
serwis ``create_stage`` (kryterium 5). Dzięki temu test widzi dokładnie to, co zadeklarował.

Od zadania T7 każda z nich przyjmuje ``competition`` (domyślnie: konkurs kontekstu). Własny klucz
obcy dostaje wyłącznie ``Edition`` – i dopiero w T3; reszta modeli tej aplikacji dochodzi do
konkursu przez edycję (§ 3.4), więc argument służy im do **propagacji**: ``StageEntryFactory(
competition=inny)`` ma założyć uczestnika i etap tego samego, wskazanego konkursu. Bez propagacji
test krzyżowy budowałby wpis, którego połowa należy do jednego konkursu, a połowa do drugiego.
"""

from datetime import timedelta

import factory
from django.utils import timezone

from apps.accounts.tests.factories import ParticipantFactory
from apps.competitions.models import (
    Edition,
    InterviewBooking,
    InterviewSlot,
    Problem,
    QualificationMode,
    QualificationRule,
    ScoringScale,
    Stage,
    StageEntry,
    StageEntryStatus,
    StageFormat,
    StageKind,
    default_allowed_formats,
    default_scoring_values,
)
from apps.tenancy.tests.factories import SAME_COMPETITION, CompetitionScopedFactory


class EditionFactory(CompetitionScopedFactory):
    """Edycja – korzeń domeny zawodów, więc jedyny model tej aplikacji z własnym FK (§ 3.2)."""

    class Meta:
        model = Edition

    year_label = factory.Sequence(lambda n: f"Edycja testowa {n}")
    is_current = False


class CurrentEditionFactory(EditionFactory):
    """Edycja bieżąca. W jednym teście może istnieć tylko jedna (constraint w bazie)."""

    is_current = True


class StageFactory(CompetitionScopedFactory):
    """Etap otwarty „teraz”: otwarty dobę temu, deadline za 14 dni."""

    class Meta:
        model = Stage

    edition = factory.SubFactory(EditionFactory, competition=SAME_COMPETITION)
    kind = StageKind.ELIM
    name = ""
    format = StageFormat.SUBMISSIONS
    opens_at = factory.LazyFunction(lambda: timezone.now() - timedelta(days=1))
    deadline_at = factory.LazyAttribute(lambda obj: obj.opens_at + timedelta(days=14))
    grace_seconds = 0
    review_deadline_at = factory.LazyAttribute(lambda obj: obj.deadline_at + timedelta(days=14))
    appeal_window_opens_at = factory.LazyAttribute(lambda obj: obj.review_deadline_at + timedelta(days=2))
    appeal_window_closes_at = factory.LazyAttribute(
        lambda obj: obj.appeal_window_opens_at + timedelta(days=7)
    )


class ScoringScaleFactory(CompetitionScopedFactory):
    class Meta:
        model = ScoringScale

    stage = factory.SubFactory(StageFactory, competition=SAME_COMPETITION)
    values = factory.LazyFunction(default_scoring_values)
    max_value = 6


class QualificationRuleFactory(CompetitionScopedFactory):
    class Meta:
        model = QualificationRule

    stage = factory.SubFactory(StageFactory, competition=SAME_COMPETITION)
    mode = QualificationMode.MIN_POINTS
    min_points = 0
    top_n = None


class ProblemFactory(CompetitionScopedFactory):
    class Meta:
        model = Problem

    stage = factory.SubFactory(StageFactory, competition=SAME_COMPETITION)
    number = factory.Sequence(lambda n: n + 1)
    title = factory.Sequence(lambda n: f"Zadanie testowe {n}")
    allowed_formats = factory.LazyFunction(default_allowed_formats)
    max_file_mb = 20


class StageEntryFactory(CompetitionScopedFactory):
    """Wpis do etapu: uczestnik i etap muszą być z **tego samego** konkursu.

    Spójności pilnuje ``clean()`` modelu (§ 3.4), więc fabryka propaguje konkurs na obie
    podfabryki naraz – inaczej każdy test krzyżowy zaczynałby się od wywrócenia walidacji.
    """

    class Meta:
        model = StageEntry

    participant = factory.SubFactory(ParticipantFactory, competition=SAME_COMPETITION)
    stage = factory.SubFactory(StageFactory, competition=SAME_COMPETITION)
    status = StageEntryStatus.REGISTERED


class InterviewStageFactory(StageFactory):
    """Etap okręgowy w formie rozmowy: otwarty od wczoraj, zapisy do deadline'u za 14 dni."""

    kind = StageKind.DISTRICT
    format = StageFormat.INTERVIEW


class InterviewSlotFactory(CompetitionScopedFactory):
    """Termin jutro o tej samej porze – domyślnie w przyszłości, żeby dało się na niego zapisać."""

    class Meta:
        model = InterviewSlot

    stage = factory.SubFactory(InterviewStageFactory, competition=SAME_COMPETITION)
    starts_at = factory.LazyFunction(lambda: timezone.now() + timedelta(days=1))
    ends_at = factory.LazyAttribute(lambda obj: obj.starts_at + timedelta(minutes=20))
    capacity = 1
    meeting_url = ""
    note = ""


class InterviewBookingFactory(CompetitionScopedFactory):
    class Meta:
        model = InterviewBooking

    slot = factory.SubFactory(InterviewSlotFactory, competition=SAME_COMPETITION)
    entry = factory.SubFactory(StageEntryFactory, competition=SAME_COMPETITION)
