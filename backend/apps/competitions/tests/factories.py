"""Fabryki factory_boy dla domeny zawodów. Używane wyłącznie w testach.

Fabryki są jawne: ``StageFactory`` nie tworzy skali ani progu kwalifikacji – od tego jest
serwis ``create_stage`` (kryterium 5). Dzięki temu test widzi dokładnie to, co zadeklarował.
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


class EditionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Edition

    year_label = factory.Sequence(lambda n: f"Edycja testowa {n}")
    is_current = False


class CurrentEditionFactory(EditionFactory):
    """Edycja bieżąca. W jednym teście może istnieć tylko jedna (constraint w bazie)."""

    is_current = True


class StageFactory(factory.django.DjangoModelFactory):
    """Etap otwarty „teraz”: otwarty dobę temu, deadline za 14 dni."""

    class Meta:
        model = Stage

    edition = factory.SubFactory(EditionFactory)
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


class ScoringScaleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ScoringScale

    stage = factory.SubFactory(StageFactory)
    values = factory.LazyFunction(default_scoring_values)
    max_value = 6


class QualificationRuleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = QualificationRule

    stage = factory.SubFactory(StageFactory)
    mode = QualificationMode.MIN_POINTS
    min_points = 0
    top_n = None


class ProblemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Problem

    stage = factory.SubFactory(StageFactory)
    number = factory.Sequence(lambda n: n + 1)
    title = factory.Sequence(lambda n: f"Zadanie testowe {n}")
    allowed_formats = factory.LazyFunction(default_allowed_formats)
    max_file_mb = 20


class StageEntryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = StageEntry

    participant = factory.SubFactory(ParticipantFactory)
    stage = factory.SubFactory(StageFactory)
    status = StageEntryStatus.REGISTERED


class InterviewStageFactory(StageFactory):
    """Etap okręgowy w formie rozmowy: otwarty od wczoraj, zapisy do deadline'u za 14 dni."""

    kind = StageKind.DISTRICT
    format = StageFormat.INTERVIEW


class InterviewSlotFactory(factory.django.DjangoModelFactory):
    """Termin jutro o tej samej porze – domyślnie w przyszłości, żeby dało się na niego zapisać."""

    class Meta:
        model = InterviewSlot

    stage = factory.SubFactory(InterviewStageFactory)
    starts_at = factory.LazyFunction(lambda: timezone.now() + timedelta(days=1))
    ends_at = factory.LazyAttribute(lambda obj: obj.starts_at + timedelta(minutes=20))
    capacity = 1
    meeting_url = ""
    note = ""


class InterviewBookingFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = InterviewBooking

    slot = factory.SubFactory(InterviewSlotFactory)
    entry = factory.SubFactory(StageEntryFactory)
