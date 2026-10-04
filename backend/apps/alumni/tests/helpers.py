"""Wspólne kawałki testów sieci absolwentów.

Absolwent powstaje tak, jak w życiu: zakończona (nie-bieżąca) edycja, etap finałowy z ogłoszonymi
wynikami i wpis uczestnika w tym etapie – osiągnięcie nie jest wpisywane do profilu, tylko wynika
z wyników (``apps.alumni.achievements``).
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from apps.alumni import services
from apps.alumni.models import AlumniSettings, Level
from apps.chat.models import AgePolicy, ChatSettings, PeerMode
from apps.chat.tests.helpers import coordinator_of, participant_of
from apps.competitions.models import StageEntryStatus, StageKind
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    EditionFactory,
    StageEntryFactory,
    StageFactory,
)

__all__ = ["coordinator_of", "participant_of"]


def enable(competition, **settings) -> AlumniSettings:
    competition.feature_flags = {**(competition.feature_flags or {}), "alumni": True}
    competition.save(update_fields=["feature_flags"])
    defaults = {"eligibility": Level.FINALIST, "mentoring_enabled": True, "public_wall": True, **settings}
    row, _created = AlumniSettings.objects.update_or_create(competition=competition, defaults=defaults)
    return row


def chat(
    competition, *, peer_mode=PeerMode.NONE, age_policy=AgePolicy.SAME_GROUP, enabled=True
) -> ChatSettings:
    row, _created = ChatSettings.objects.update_or_create(
        competition=competition,
        defaults={"peer_mode": peer_mode, "age_policy": age_policy, "enabled": enabled},
    )
    return row


def past_final(competition, *, published: bool = True, year_label: str | None = None):
    """Etap finałowy zakończonej edycji (z ogłoszonymi wynikami albo bez)."""
    past = timezone.now() - timedelta(days=400)
    kwargs = {"year_label": year_label} if year_label else {}
    edition = EditionFactory(competition=competition, is_current=False, **kwargs)
    return StageFactory(
        competition=competition,
        edition=edition,
        kind=StageKind.FINAL,
        opens_at=past,
        deadline_at=past + timedelta(days=1),
        results_published_at=past + timedelta(days=60) if published else None,
    )


def current_stage(competition):
    """Etap bieżącej edycji, który **już się zamknął** (nie wymusza premoderacji czatu)."""
    from apps.competitions.models import Edition

    edition = Edition.objects.filter(competition=competition, is_current=True).first()
    edition = edition or CurrentEditionFactory(competition=competition)
    existing = edition.stages.filter(kind=StageKind.ELIM).first()
    if existing is not None:
        return existing
    past = timezone.now() - timedelta(days=30)
    return StageFactory(
        competition=competition, edition=edition, opens_at=past, deadline_at=past + timedelta(days=1)
    )


def alumnus(competition, first_name="Ola", last_name="Absolwentka", *, laureate=True, stage=None, **kwargs):
    """Pełnoletni uczestnik z wpisem w opublikowanym finale zakończonej edycji (laureat albo finalista)."""
    kwargs.setdefault("birth_year", 1998)
    participant = participant_of(competition, first_name, last_name, **kwargs)
    StageEntryFactory(
        competition=competition,
        participant=participant,
        stage=stage or past_final(competition),
        status=StageEntryStatus.QUALIFIED if laureate else StageEntryStatus.NOT_QUALIFIED,
    )
    return participant


def joined(participant, **fields):
    profile = services.join(user=participant.user, competition=participant.competition, consent=True)
    if fields:
        for name, value in fields.items():
            setattr(profile, name, value)
        profile.save()
    return profile


def mentor(competition, first_name="Ola", **fields):
    defaults = {"mentor_available": True, "mentor_capacity": 2, "mentor_topics": ["physics"]}
    return joined(alumnus(competition, first_name), **{**defaults, **fields})


def mentee(competition, first_name="Tymek", *, minor=True, stage=None):
    """Uczestnik bieżącej edycji (wpis w etapie bieżącej edycji) – małoletni albo pełnoletni."""
    participant = participant_of(competition, first_name, "Uczeń", birth_year=2010 if minor else 1999)
    StageEntryFactory(
        competition=competition, participant=participant, stage=stage or current_stage(competition)
    )
    return participant
