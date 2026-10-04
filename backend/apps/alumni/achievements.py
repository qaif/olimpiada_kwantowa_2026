"""Osiągnięcia absolwenta – **wyłącznie** z ogłoszonych wyników i wystawionych dyplomów.

Liczone na żywo, jednym przebiegiem dla całej listy profili (katalog, ściana, zaproszenia), a nie
przechowywane: cofnięta publikacja wyników ma zdjąć osiągnięcie z profilu od razu, bez zadania
porządkowego (``docs/tasks/ALUM-01.md`` § 1).

Źródła (kolejność bez znaczenia – na edycję zostaje najwyższy poziom):

- wpis do etapu nietreningowego z ``Stage.results_published_at``, niezdyskwalifikowany:
  w etapie finałowym ``QUALIFIED`` = laureat (w finale „awans” jest tytułem laureata – ta sama
  umowa, co ``apps.results.models.Anonymization.FULL``), każdy inny wpis w finale = finalista,
  ``QUALIFIED`` w etapie niefinałowym = awans, reszta = uczestnik,
- dyplom ``LAUREAT``/``FINALISTA`` (``apps.results.models.Certificate``) – dokument wystawiony przez
  komitet jest ogłoszeniem tytułu sam w sobie, także gdy tabela finału była ogłoszona „po kodach”,
- źródła dopisane z zewnątrz (:func:`register_source`) – np. medale, gdy moduł medali powstanie.
  Źródło dostaje listę profili ``Participant`` i oddaje :class:`Achievement`-y; odpowiada samo za
  to, żeby oddawać wyłącznie fakty **ogłoszone**.

Wpisy drużynowe (``StageEntry.participant`` puste) nie wchodzą: drużyna nie wyraża zgody.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .models import LEVEL_RANK, Level


@dataclass(frozen=True)
class Achievement:
    """Jedno osiągnięcie w jednej edycji. ``label`` nadpisuje podpis poziomu (np. „złoty medal”)."""

    participant_id: int
    edition_id: int
    edition_label: str
    level: Level
    finished: bool
    label: str = ""

    @property
    def rank(self) -> int:
        return LEVEL_RANK[Level(self.level)]

    @property
    def title(self) -> str:
        return self.label or str(Level(self.level).label)


_SOURCES: list[Callable[[list], Iterable[Achievement]]] = []


def register_source(source: Callable[[list], Iterable[Achievement]]) -> None:
    """Dopisuje źródło osiągnięć (woła się z ``AppConfig.ready`` modułu, który je zna)."""
    if source not in _SOURCES:
        _SOURCES.append(source)


def _finished_editions(edition_ids: set[int]) -> set[int]:
    """Edycje zakończone: nie-bieżące albo z ogłoszonymi wynikami **wszystkich** etapów zawodów."""
    from apps.competitions.models import Edition, Stage, StageKind

    finished = set(Edition.objects.filter(pk__in=edition_ids, is_current=False).values_list("pk", flat=True))
    current = edition_ids - finished
    if current:
        stages = Stage.objects.filter(edition_id__in=current).exclude(kind=StageKind.TRAINING)
        open_editions = set(
            stages.filter(results_published_at__isnull=True).values_list("edition_id", flat=True)
        )
        with_stages = set(stages.values_list("edition_id", flat=True))
        finished |= with_stages - open_editions
    return finished


def _final_stage_ids(edition_ids: set[int]) -> set[int]:
    """Etap finałowy każdej edycji: rodzaj ``FINAL``, a bez niego – etap zawodów otwierany najpóźniej."""
    from apps.competitions.models import Stage, StageKind

    finals: dict[int, int] = {}
    latest: dict[int, tuple] = {}
    stages = (
        Stage.objects.filter(edition_id__in=edition_ids)
        .exclude(kind=StageKind.TRAINING)
        .values_list("pk", "edition_id", "kind", "opens_at")
    )
    for pk, edition_id, kind, opens_at in stages:
        if kind == StageKind.FINAL:
            finals[edition_id] = pk
        key = (opens_at, pk)
        if edition_id not in latest or key > latest[edition_id]:
            latest[edition_id] = key
    return {finals.get(edition_id, key[1]) for edition_id, key in latest.items()}


def _from_results(participants: list) -> list[Achievement]:
    from apps.competitions.models import StageEntry, StageEntryStatus, StageKind
    from apps.results.models import Certificate, CertificateKind

    ids = [participant.pk for participant in participants]
    entries = list(
        StageEntry.objects.filter(participant_id__in=ids, stage__results_published_at__isnull=False)
        .exclude(stage__kind=StageKind.TRAINING)
        .exclude(status=StageEntryStatus.DISQUALIFIED)
        .values_list(
            "participant_id", "stage_id", "status", "stage__edition_id", "stage__edition__year_label"
        )
    )
    certificates = list(
        # Dyplom liczy się tylko przy wpisie z **ogłoszonymi** wynikami i bez dyskwalifikacji (M3):
        # dokument wystawiony przed ogłoszeniem albo przed dyskwalifikacją nie jest tytułem.
        Certificate.objects.filter(
            entry__participant_id__in=ids,
            kind__in=[CertificateKind.LAUREAT, CertificateKind.FINALISTA],
            entry__stage__results_published_at__isnull=False,
        )
        .exclude(entry__status=StageEntryStatus.DISQUALIFIED)
        .exclude(entry__stage__kind=StageKind.TRAINING)
        .values_list("entry__participant_id", "kind", "edition_id", "edition__year_label")
    )
    edition_ids = {row[3] for row in entries} | {row[2] for row in certificates}
    if not edition_ids:
        return []
    finals = _final_stage_ids(edition_ids)
    finished = _finished_editions(edition_ids)
    found: list[Achievement] = []
    for participant_id, stage_id, status, edition_id, label in entries:
        qualified = status == StageEntryStatus.QUALIFIED
        if stage_id in finals:
            level = Level.LAUREATE if qualified else Level.FINALIST
        else:
            level = Level.QUALIFIED if qualified else Level.ANY
        found.append(Achievement(participant_id, edition_id, label, level, edition_id in finished))
    for participant_id, kind, edition_id, label in certificates:
        level = Level.LAUREATE if kind == CertificateKind.LAUREAT else Level.FINALIST
        found.append(Achievement(participant_id, edition_id, label, level, edition_id in finished))
    return found


def medal_source(participants: list) -> list[Achievement]:
    """Medale i wyróżnienia (MED-01, ``apps.medals``) – wyłącznie z **ogłoszonych** (zamrożonych)
    schematów etapów z ogłoszonymi wynikami, bez wpisów zdyskwalifikowanych.

    Medal to poziom laureata z podpisem nagrody („złoty medal”), wyróżnienie – poziom finalisty
    z podpisem „wyróżnienie”. Konkurs bez flagi ``medals`` nie ma zamrożonych schematów, więc źródło
    odpowiada pustą listą jednym zapytaniem; bez wpisów w etapach z ogłoszonymi wynikami – zerem.
    """
    from apps.competitions.models import StageEntry, StageEntryStatus, StageKind
    from apps.medals.models import MEDALS, Award, MedalScheme

    entries = list(
        StageEntry.objects.filter(
            participant_id__in=[participant.pk for participant in participants],
            stage__results_published_at__isnull=False,
        )
        .exclude(stage__kind=StageKind.TRAINING)
        .exclude(status=StageEntryStatus.DISQUALIFIED)
        .values_list("pk", "participant_id", "stage_id", "stage__edition_id", "stage__edition__year_label")
    )
    if not entries:
        return []
    schemes = dict(
        MedalScheme.objects.filter(
            stage_id__in={row[2] for row in entries}, frozen_at__isnull=False
        ).values_list("stage_id", "awards")
    )
    if not schemes:
        return []
    finished = _finished_editions({row[3] for row in entries})
    found = []
    for entry_id, participant_id, stage_id, edition_id, label in entries:
        award = ((schemes.get(stage_id) or {}).get(str(entry_id)) or {}).get("award")
        if award in MEDALS:
            level = Level.LAUREATE
        elif award == Award.HONOURABLE:
            level = Level.FINALIST
        else:
            continue
        title = str(Award(award).label)
        found.append(Achievement(participant_id, edition_id, label, level, edition_id in finished, title))
    return found


def achievements_for(participants: Iterable) -> dict[int, list[Achievement]]:
    """``{participant_id: [osiągnięcie na edycję, najnowsza edycja pierwsza]}`` – po jednym na edycję.

    Na edycję zostaje najwyższy poziom; równy poziom z własnym podpisem (medal) wygrywa z gołym.
    """
    people = [participant for participant in participants if participant is not None]
    if not people:
        return {}
    raw = _from_results(people)
    for source in _SOURCES:
        raw.extend(source(people))
    best: dict[tuple[int, int], Achievement] = {}
    for item in raw:
        key = (item.participant_id, item.edition_id)
        current = best.get(key)
        if current is None or (item.rank, bool(item.label)) > (current.rank, bool(current.label)):
            best[key] = item
    result: dict[int, list[Achievement]] = defaultdict(list)
    for item in sorted(best.values(), key=lambda a: (-a.edition_id, -a.rank)):
        result[item.participant_id].append(item)
    return dict(result)


def best_finished_rank(achievements: list[Achievement]) -> int:
    """Najwyższa ranga z **zakończonych** edycji (0 = brak) – podstawa kwalifikowalności."""
    return max((item.rank for item in achievements if item.finished), default=0)
