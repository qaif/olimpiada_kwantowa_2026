"""Agregaty udziału i wyników po szkołach – logika STAT-01 (``docs/tasks/STAT-01.md``).

Zasady, na których stoi cały moduł:

- **nic przed publikacją.** Punkty pochodzą wyłącznie z ``ResultsPublication.entry_totals`` –
  zamrożonej mapy sum z chwili ogłoszenia – a kwalifikacja liczy się tylko w etapie, który ma
  publikację. Etap bez publikacji ma w statystyce liczbę wpisów i oddanych prac, i nic ponadto.
  Bieżące ``StageEntry.total_points`` nie jest tu czytane wcale: po późnej decyzji komisji
  statystyka twierdziłaby wtedy coś innego niż ogłoszona tabela,
- **przynależność też jest zamrożona.** Agregat etapu z publikacją liczy się z
  ``FrozenMembership`` (szkoła, region, awans i oddanie w chwili publikacji), a nie z bieżącego
  profilu – zmiana szkoły albo anonimizacja konta po ogłoszeniu nie przesuwa ogłoszonej średniej
  i nie daje opiekunowi „przed” i „po” do odjęcia,
- **lista „tylko awansujący” nie ma średnich.** Ze snapshotu samych zakwalifikowanych publiczne
  statystyki też niczego nie liczą (``apps.results.statistics.build_statistics``) – średnia szkoły
  z takiego etapu zdradzałaby wyniki, których organizator świadomie nie ogłosił,
- **k-anonimowość (próg 5) na wyjściu, nie na wejściu.** Agregaty liczymy w całości i dopiero
  widok dla konkretnej roli decyduje, co wolno pokazać (:func:`visible_for_supervisor`,
  :func:`scoped_lines`, :func:`coordinator_visibility`). Pamięć podręczna jest dzięki temu jedna dla
  wszystkich ról, a reguła progu – w jednym miejscu, a nie w szablonach. Reguła ma trzy warstwy:
  próg samej grupy, **dopełnienie** (część grupy poza uczniami znanymi czytelnikowi ma 0 albo ≥ 5
  osób) i **zagnieżdżenie** (różnica dwóch pokazanych grup, np. województwo minus szkoła, też ma
  0 albo ≥ 5 osób spoza znanych uczniów).

W pamięci podręcznej leżą wyłącznie agregaty (liczby po grupach) – żadnego identyfikatora
uczestnika ani wpisu. Część „moi uczniowie” opiekuna liczy się zawsze na żywo.
"""

from __future__ import annotations

import hashlib
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import NamedTuple

from django.core.cache import cache
from django.db.models import BooleanField, Case, Exists, OuterRef, Value, When
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from apps.accounts.anonymised import anonymised_q
from apps.competitions.models import Edition, Stage, StageEntry, StageEntryStatus, StageFormat, StageKind
from apps.core.points import round_points, to_points
from apps.results.models import ResultsPublication
from apps.results.statistics import width_class
from apps.submissions.models import Submission, SubmissionStatus

from .grouping import SCHOOL_AXIS, EntryRow, Group, GroupAxis, MemberRow

#: Przełącznik konkursu (``apps.tenancy.models.FEATURE_DEFAULTS``). Jedna stała, żeby literówka
#: wywracała się w jednym miejscu – ``Competition.has_feature`` podnosi ``KeyError`` na nieznanej.
FLAG = "school_statistics"

#: Najmniejsza grupa, której agregat wolno pokazać (STAT-01 § 3). Pięć, a nie trzy jak przy
#: inicjałach w publikacji (``MIN_SCHOOL_GROUP``): tam chodzi o podpis jednego wiersza tabeli,
#: a tu o liczby, które zestawia się ze sobą przez kolejne edycje i etapy – a każde zestawienie
#: dwóch agregatów jest okazją do odjęcia jednego od drugiego.
K_ANONYMITY = 5

#: Dokładność średniej: jedno miejsce po przecinku. Średnia z dokładnością do setnych sugerowałaby
#: precyzję, której przy kilku–kilkunastu wynikach nie ma.
MEAN_QUANTUM = Decimal("0.1")

CACHE_PREFIX = "school_stats:v2"
#: Edycja zamknięta (każdy etap ma publikację) albo nie bieżąca już się nie zmienia (ponowna
#: publikacja zmienia odcisk w kluczu) – doba wystarcza z zapasem.
CACHE_TTL_FINAL = 24 * 3600
#: Edycja w toku: liczby udziału i oddanych prac są żywe, więc pięć minut. Dłużej koordynator
#: oglądałby wczorajszą liczbę zgłoszeń w środku akcji promocyjnej.
CACHE_TTL_LIVE = 5 * 60
#: Ochrona przed lawiną: gdy agregat edycji liczy już inny proces, czekamy na jego wynik najwyżej
#: tyle sekund, zanim policzymy sami. Blokada żyje krócej niż najwolniejsze sensowne liczenie.
STAMPEDE_WAIT_SECONDS = 2.0
STAMPEDE_LOCK_SECONDS = 30

#: Metryki sprawdzane przy dopełnieniu i zagnieżdżeniu: liczba wpisów (udział, oddane, awans)
#: i liczba wpisów ocenionych (z nich liczy się średnia).
CHECKED_METRICS = ("entries", "scored")


def enabled(competition) -> bool:
    """Czy ten konkurs ma statystyki szkół. **Jedyne** wejście do flagi; brak konkursu = brak funkcji."""
    return competition is not None and competition.has_feature(FLAG)


# --- agregat ------------------------------------------------------------------------------------


class _Publication(NamedTuple):
    qualified_only: bool
    totals: dict[int, Decimal]


@dataclass
class Aggregate:
    """Liczby jednej grupy w jednym etapie.

    ``submitted`` i ``on_time`` mają sens wyłącznie w etapie z pracami pisemnymi; w teście online
    i w rozmowie zostają zerami, a szablon czyta ``StageStats.has_submissions``, zanim je pokaże.
    ``scored`` i ``points_sum`` liczą tylko wpisy z sumą w opublikowanej mapie (``entry_totals``).
    """

    entries: int = 0
    submitted: int = 0
    on_time: int = 0
    scored: int = 0
    points_sum: Decimal = Decimal(0)
    qualified: int = 0

    def add(self, row: MemberRow, publication: _Publication | None) -> None:
        self.entries += 1
        if row.has_submission:
            self.submitted += 1
            if not row.has_late:
                self.on_time += 1
        if publication is None:
            return
        if row.qualified:
            self.qualified += 1
        if publication.qualified_only:
            return
        total = publication.totals.get(row.entry_id)
        if total is not None:
            self.scored += 1
            self.points_sum += total

    @property
    def mean(self) -> Decimal | None:
        """Średnia ogłoszonych sum, ``None`` gdy nie ma z czego liczyć – zero byłoby wynikiem."""
        if not self.scored:
            return None
        return round_points(self.points_sum / self.scored, MEAN_QUANTUM)


EMPTY = Aggregate()


@dataclass
class StageStats:
    """Etap edycji z agregatami: całość, regiony, grupy i ich przecięcia (grupa × region)."""

    stage_id: int
    name: str
    kind: str
    has_submissions: bool
    published: bool
    qualified_only: bool
    all: Aggregate = field(default_factory=Aggregate)
    regions: dict[str, Aggregate] = field(default_factory=dict)
    groups: dict[str, Aggregate] = field(default_factory=dict)
    #: ``(klucz grupy, region) → agregat`` – część szkoły w danym województwie. Bez tego reguła
    #: zagnieżdżenia musiałaby zakładać, że szkoła leży w całości w jednym województwie, a uczeń
    #: z internatu albo po przeprowadzce takie założenie łamie.
    group_regions: dict[tuple[str, str], Aggregate] = field(default_factory=dict)

    @property
    def has_means(self) -> bool:
        """Czy w tym etapie w ogóle liczy się średnie (publikacja pełnej tabeli)."""
        return self.published and not self.qualified_only

    def population(self, scope: Scope) -> Aggregate:
        """Agregat całej populacji w zakresie ``scope`` (grupa, region, ich przecięcie albo całość)."""
        if scope.empty:
            return EMPTY
        if scope.group and scope.region:
            return self.group_regions.get((scope.group, scope.region), EMPTY)
        if scope.group:
            return self.groups.get(scope.group, EMPTY)
        if scope.region:
            return self.regions.get(scope.region, EMPTY)
        return self.all


@dataclass
class GroupInfo:
    """Grupa w edycji: podpis, miejscowość, region i liczba **różnych** uczestników (bez kont usuniętych)."""

    key: str
    label: str
    city: str
    school_id: int | None
    rspo: int | None
    region: str
    participants: int = 0


@dataclass
class RegionInfo:
    code: str
    label: str
    participants: int = 0
    groups: int = 0


@dataclass
class EditionSummary:
    """Wszystko, co statystyka wie o edycji – i jedyny obiekt, który trafia do pamięci podręcznej."""

    edition_id: int
    label: str
    stages: list[StageStats]
    groups: dict[str, GroupInfo]
    regions: dict[str, RegionInfo]
    participants: int

    def stage(self, stage_id: int) -> StageStats | None:
        return next((stage for stage in self.stages if stage.stage_id == stage_id), None)

    @property
    def published_stages(self) -> list[StageStats]:
        return [stage for stage in self.stages if stage.published]

    @property
    def first_stage_with_means(self) -> StageStats | None:
        """Pierwszy etap z pełną publikacją – punkt wykresu „postęp przez edycje”.

        Pierwszy, a nie ostatni: do eliminacji startują wszyscy, więc ich średnia porównuje szkołę
        z olimpiadą na najszerszej próbie. Finał ma kilkudziesięciu uczestników i średnia szkoły
        prawie zawsze wypadłaby tam pod progiem k-anonimowości.
        """
        return next((stage for stage in self.stages if stage.has_means), None)


class Scope(NamedTuple):
    """Zakres agregatu: grupa (``None`` = dowolna) i region (``None`` = dowolny).

    ``empty`` – przecięcie dwóch różnych grup albo dwóch różnych regionów (zbiór pusty).
    """

    group: str | None = None
    region: str | None = None
    empty: bool = False

    def contains(self, row: MemberRow) -> bool:
        if self.empty:
            return False
        if self.group is not None and row.group_key != self.group:
            return False
        return self.region is None or row.region == self.region

    def intersect(self, other: Scope) -> Scope:
        if self.empty or other.empty:
            return Scope(empty=True)
        if self.group and other.group and self.group != other.group:
            return Scope(empty=True)
        if self.region and other.region and self.region != other.region:
            return Scope(empty=True)
        return Scope(self.group or other.group, self.region or other.region)


EVERYONE = Scope()


# --- odczyt z bazy --------------------------------------------------------------------------------

_STAGE_FIELDS = ("id", "edition_id", "kind", "name", "format", "opens_at", "results_published_at")


def competition_stages(edition: Edition) -> list[Stage]:
    """Etapy zawodów edycji (bez treningu), w kolejności terminów – tej samej, co na osi czasu."""
    return list(
        Stage.objects.filter(edition=edition)
        .exclude(kind=StageKind.TRAINING)
        .only(*_STAGE_FIELDS)
        .order_by("opens_at", "id")
    )


def stages_by_edition(editions: list[Edition]) -> dict[int, list[Stage]]:
    """Etapy zawodów wielu edycji jednym zapytaniem – dla historii i postępu przez edycje."""
    grouped: dict[int, list[Stage]] = {edition.pk: [] for edition in editions}
    for stage in (
        Stage.objects.filter(edition__in=editions)
        .exclude(kind=StageKind.TRAINING)
        .only(*_STAGE_FIELDS)
        .order_by("opens_at", "id")
    ):
        grouped.setdefault(stage.edition_id, []).append(stage)
    return grouped


def entry_rows(stage_ids: list[int], participant_ids: list[int] | None = None) -> list[EntryRow]:
    """Wpisy etapów jako krotki :class:`EntryRow` – **jedno** zapytanie.

    Dwa ``EXISTS`` po zgłoszeniach chodzą po indeksie klucza obcego ``Submission.entry_id``,
    a złączenia z uczestnikiem i szkołą – po kluczach głównych. Zgłoszenie odrzucone przez skaner
    antywirusowy nie jest „oddaną pracą” (tak samo liczy ``apps.accounts.supervisors.student_rows``).
    Wpisy drużynowe (bez uczestnika) nie wchodzą: grupą jest szkoła ucznia, a nie skład.
    """
    if not stage_ids:
        return []
    live = Submission.objects.filter(entry_id=OuterRef("pk")).exclude(
        status=SubmissionStatus.REJECTED_INFECTED
    )
    queryset = StageEntry.objects.filter(stage_id__in=stage_ids, participant__isnull=False)
    if participant_ids is not None:
        if not participant_ids:
            return []
        queryset = queryset.filter(participant_id__in=participant_ids)
    queryset = queryset.annotate(
        _has_submission=Exists(live),
        _has_late=Exists(live.filter(is_late=True)),
        _anonymised=Case(
            When(anonymised_q("participant__user"), then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
    )
    return [
        EntryRow(*values)
        for values in queryset.values_list(
            "pk",
            "stage_id",
            "participant_id",
            "status",
            "participant__school_ref_id",
            "participant__school",
            "participant__district",
            "participant__school_ref__name",
            "participant__school_ref__city",
            "participant__school_ref__rspo",
            "_has_submission",
            "_has_late",
            "_anonymised",
        ).order_by("pk")
    ]


def live_members(rows: list[EntryRow], axis: GroupAxis = SCHOOL_AXIS) -> list[MemberRow]:
    return [axis.member(row, qualified_status=StageEntryStatus.QUALIFIED) for row in rows]


def publications(stage_ids: list[int]) -> dict[int, _Publication]:
    """Publikacje etapów: tryb listy i zamrożone sumy wpisów (klucz – identyfikator wpisu)."""
    if not stage_ids:
        return {}
    result: dict[int, _Publication] = {}
    for publication in ResultsPublication.objects.filter(stage_id__in=stage_ids).only(
        "stage_id", "qualified_only", "entry_totals"
    ):
        totals: dict[int, Decimal] = {}
        for key, value in (publication.entry_totals or {}).items():
            number = to_points(value)
            if number is not None and str(key).isdigit():
                totals[int(key)] = number
        result[publication.stage_id] = _Publication(publication.qualified_only, totals)
    return result


# --- zamrożona przynależność --------------------------------------------------------------------


def _frozen_row(item) -> MemberRow:
    group = (
        Group(item.group_key, item.group_label, item.group_city, item.school_id, item.rspo)
        if item.group_key
        else None
    )
    return MemberRow(
        entry_id=item.entry_pk,
        stage_id=item.stage_id,
        participant_id=None,
        qualified=item.qualified,
        group=group,
        region=item.region,
        has_submission=item.has_submission,
        has_late=item.has_late,
    )


def _freeze_members(stage_id: int, members: list[MemberRow], axis: GroupAxis) -> None:
    from .models import FrozenMembership

    FrozenMembership.objects.bulk_create(
        [
            FrozenMembership(
                axis=axis.kind,
                stage_id=stage_id,
                entry_pk=row.entry_id,
                group_key=row.group.key if row.group else "",
                group_label=(row.group.label if row.group else "")[:255],
                group_city=(row.group.city if row.group else "")[:120],
                school_id=row.group.school_id if row.group else None,
                rspo=row.group.rspo if row.group else None,
                region=row.region[:100],
                qualified=row.qualified,
                has_submission=row.has_submission,
                has_late=row.has_late,
            )
            for row in members
        ],
        ignore_conflicts=True,
    )


def freeze_stage(stage_id: int, axis: GroupAxis = SCHOOL_AXIS, *, replace: bool = False) -> None:
    """Zamraża przynależność wpisów etapu (woła to odbiornik publikacji, ``signals.py``)."""
    from .models import FrozenMembership

    if replace:
        FrozenMembership.objects.filter(axis=axis.kind, stage_id=stage_id).delete()
    _freeze_members(stage_id, live_members(entry_rows([stage_id]), axis), axis)


def frozen_members(
    stage_ids: list[int], axis: GroupAxis = SCHOOL_AXIS, *, entry_ids: list[int] | None = None
) -> dict[int, list[MemberRow]]:
    """Zamrożone wiersze etapów (opcjonalnie tylko wskazanych wpisów), po etapie."""
    from .models import FrozenMembership

    result: dict[int, list[MemberRow]] = defaultdict(list)
    if not stage_ids or entry_ids == []:
        return result
    queryset = FrozenMembership.objects.filter(axis=axis.kind, stage_id__in=stage_ids)
    if entry_ids is not None:
        queryset = queryset.filter(entry_pk__in=entry_ids)
    for item in queryset.order_by("entry_pk"):
        result[item.stage_id].append(_frozen_row(item))
    return result


def effective_members(
    rows: list[EntryRow], published_stage_ids: set[int], axis: GroupAxis = SCHOOL_AXIS
) -> list[MemberRow]:
    """Przynależność wskazanych wpisów taka, jaką widzi agregat: zamrożona w etapach z publikacją.

    Wpis etapu z publikacją bez wiersza zamrożonego (dopisany po publikacji, zamrożenie usunięte
    ręcznie) bierze przynależność bieżącą – lepsza odpowiedź przybliżona niż brak ucznia w tabeli.
    """
    live = live_members(rows, axis)
    frozen_ids = [row.entry_id for row in live if row.stage_id in published_stage_ids]
    frozen = {
        row.entry_id: row
        for stage_rows in frozen_members(sorted(published_stage_ids), axis, entry_ids=frozen_ids).values()
        for row in stage_rows
    }
    result = []
    for row in live:
        snapshot = frozen.get(row.entry_id)
        if snapshot is None:
            result.append(row)
        else:
            result.append(snapshot._replace(participant_id=row.participant_id))
    return result


# --- agregat edycji (z pamięcią podręczną) -----------------------------------------------------


def _fingerprint(stages: list[Stage]) -> str:
    """Odcisk stanu publikacji edycji: ponowna publikacja (albo jej zdjęcie) zmienia klucz sama."""
    raw = "|".join(
        f"{stage.pk}:{stage.results_published_at.isoformat() if stage.results_published_at else '-'}"
        for stage in stages
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def cache_key(edition: Edition, stages: list[Stage], axis: GroupAxis = SCHOOL_AXIS) -> str:
    return f"{CACHE_PREFIX}:{axis.kind}:{edition.pk}:{_fingerprint(stages)}"


def edition_summary(edition: Edition, axis: GroupAxis = SCHOOL_AXIS, *, stages: list[Stage] | None = None):
    """Agregaty edycji, z pamięci podręcznej albo policzone (trzy zapytania plus jedno o etapy).

    Czas życia: doba dla edycji zamkniętej publikacjami albo nie bieżącej; pięć minut dla bieżącej
    w toku. Prosta ochrona przed lawiną: pierwszy liczący zakłada blokadę (``cache.add``), pozostali
    przez chwilę czekają na jego wynik zamiast liczyć to samo równolegle.
    """
    stages = competition_stages(edition) if stages is None else stages
    key = cache_key(edition, stages, axis)
    cached = cache.get(key)
    if cached is not None:
        return cached
    lock = f"{key}:lock"
    if not cache.add(lock, 1, STAMPEDE_LOCK_SECONDS):
        deadline = time.monotonic() + STAMPEDE_WAIT_SECONDS
        while time.monotonic() < deadline:
            time.sleep(0.1)
            cached = cache.get(key)
            if cached is not None:
                return cached
    try:
        summary = build_summary(edition, stages, axis)
        final = not edition.is_current or (
            bool(stages) and all(stage.results_published_at for stage in stages)
        )
        cache.set(key, summary, CACHE_TTL_FINAL if final else CACHE_TTL_LIVE)
    finally:
        cache.delete(lock)
    return summary


def build_summary(edition: Edition, stages: list[Stage], axis: GroupAxis = SCHOOL_AXIS) -> EditionSummary:
    """Agregaty edycji bez pamięci podręcznej.

    Etap z publikacją liczy się z przynależności zamrożonej; etap ogłoszony przed wdrożeniem tej
    funkcji (albo przed zapaleniem flagi) zamrażamy tu, leniwie, z bieżących wpisów. Liczby **osób**
    w grupach i regionach biorą się z wpisów żywych, bez kont po anonimizacji (STAT-01 M1).
    """
    stage_ids = [stage.pk for stage in stages]
    published = publications([stage.pk for stage in stages if stage.results_published_at])
    live = live_members(entry_rows(stage_ids), axis)
    live_by_stage: dict[int, list[MemberRow]] = defaultdict(list)
    for row in live:
        live_by_stage[row.stage_id].append(row)
    frozen = frozen_members(sorted(published), axis)
    for stage_id in published:
        if stage_id not in frozen:
            _freeze_members(stage_id, live_by_stage.get(stage_id, []), axis)
            frozen[stage_id] = list(live_by_stage.get(stage_id, []))

    stats: dict[int, StageStats] = {}
    for stage in stages:
        publication = published.get(stage.pk)
        item = StageStats(
            stage_id=stage.pk,
            name=stage.display_name,
            kind=stage.kind,
            has_submissions=stage.format == StageFormat.SUBMISSIONS,
            published=publication is not None,
            qualified_only=bool(publication and publication.qualified_only),
        )
        for row in frozen[stage.pk] if publication is not None else live_by_stage.get(stage.pk, []):
            item.all.add(row, publication)
            if row.region:
                item.regions.setdefault(row.region, Aggregate()).add(row, publication)
            if row.group is not None:
                item.groups.setdefault(row.group.key, Aggregate()).add(row, publication)
                item.group_regions.setdefault((row.group.key, row.region), Aggregate()).add(row, publication)
        stats[stage.pk] = item

    groups: dict[str, GroupInfo] = {}
    group_members: dict[str, set[int]] = defaultdict(set)
    group_regions: dict[str, Counter] = defaultdict(Counter)
    region_members: dict[str, set[int]] = defaultdict(set)
    region_groups: dict[str, set[str]] = defaultdict(set)
    everyone: set[int] = set()
    for row in live:
        if row.anonymised:
            continue
        everyone.add(row.participant_id)
        if row.region:
            region_members[row.region].add(row.participant_id)
        if row.group is None:
            continue
        group_members[row.group.key].add(row.participant_id)
        group_regions[row.group.key][row.region] += 1
        if row.region:
            region_groups[row.region].add(row.group.key)
        groups.setdefault(
            row.group.key,
            GroupInfo(
                key=row.group.key,
                label=row.group.label,
                city=row.group.city,
                school_id=row.group.school_id,
                rspo=row.group.rspo,
                region="",
            ),
        )
    for key, info in groups.items():
        info.participants = len(group_members[key])
        # Region grupy to region **większości** jej wpisów: szkoła z wykazu ma jedno województwo,
        # ale uczeń mógł wpisać w profilu inne (internat, przeprowadzka) – jeden głos nie przestawia
        # szkoły do sąsiedniego województwa.
        info.region = group_regions[key].most_common(1)[0][0] if group_regions[key] else ""
    regions = {
        code: RegionInfo(
            code=code,
            label=axis.region_label(code),
            participants=len(members),
            groups=len(region_groups.get(code, ())),
        )
        for code, members in region_members.items()
    }
    return EditionSummary(
        edition_id=edition.pk,
        label=edition.year_label,
        stages=[stats[stage.pk] for stage in stages],
        groups=groups,
        regions=regions,
        participants=len(everyone),
    )


def previous_edition(edition: Edition) -> Edition | None:
    """Edycja **tego samego konkursu** założona tuż przed wskazaną – do porównania „rok do roku”."""
    return (
        Edition.objects.filter(competition_id=edition.competition_id, created_at__lt=edition.created_at)
        .order_by("-created_at", "-id")
        .first()
    )


# --- reguły widoczności ------------------------------------------------------------------------


def _safe_rest(rest: int) -> bool:
    return rest == 0 or rest >= K_ANONYMITY


def visible_for_supervisor(group: Aggregate, mine: Aggregate) -> bool:
    """Czy czytelnik znający wyniki ``mine`` może zobaczyć agregat grupy (STAT-01 § 3).

    Dwa warunki: grupa ma co najmniej ``K_ANONYMITY`` wpisów, **i** część grupy spoza znanych
    uczniów ma ich zero albo co najmniej ``K_ANONYMITY``. Drugi warunek jest właściwą ochroną:
    opiekun zna punkty swoich uczniów, więc średnia szkoły z sześcioma wpisami, z których pięć to
    jego uczniowie, wyznacza wynik szóstego co do punktu. Ten sam rachunek robimy osobno dla
    wpisów ocenionych (średnia liczy się z nich, nie ze wszystkich wpisów).
    """
    if group.entries < K_ANONYMITY:
        return False
    return all(_safe_rest(getattr(group, metric) - getattr(mine, metric)) for metric in CHECKED_METRICS)


def _own(rows: list[MemberRow], scope: Scope, publication) -> Aggregate:
    aggregate = Aggregate()
    for row in rows:
        if scope.contains(row):
            aggregate.add(row, publication)
    return aggregate


def nested_safe(
    stage: StageStats, inner: Scope, outer: Scope, own_rows: list[MemberRow], publication
) -> bool:
    """Czy różnica dwóch pokazanych agregatów (``outer`` minus część wspólna z ``inner``) jest bezpieczna.

    Przykład z przeglądu: szkoła = 6 uczniów opiekuna + 5 obcych (widoczna, dopełnienie 5),
    województwo = ta szkoła + 1 obcy (widoczne, dopełnienie 6). Województwo minus szkoła to **jedna
    osoba** – jej wynik wychodzi z odejmowania dwóch pokazanych średnich. Różnica musi więc, po
    odjęciu uczniów znanych czytelnikowi, liczyć 0 albo co najmniej ``K_ANONYMITY`` wpisów –
    osobno dla wszystkich wpisów i dla ocenionych.
    """
    common = inner.intersect(outer)
    for metric in CHECKED_METRICS:
        population = getattr(stage.population(outer), metric) - getattr(stage.population(common), metric)
        own = getattr(_own(own_rows, outer, publication), metric) - getattr(
            _own(own_rows, common, publication), metric
        )
        if not _safe_rest(population - own):
            return False
    return True


def scoped_lines(
    stage: StageStats, scopes: list[tuple[str, object, Scope]], own_rows, publication
) -> list[dict]:
    """Wiersze porównania z regułą progu, dopełnienia, zagnieżdżenia i bramką średniej.

    ``scopes`` – od najwęższego do najszerszego: ``(klucz, podpis, zakres)``. Każda para pokazanych
    wierszy przechodzi :func:`nested_safe`; para, która nie przechodzi, ukrywa wiersz **węższy**
    (szerszy jest mniej wrażliwy i zwykle potrzebny do porównań). Powtarzamy do skutku, bo ukrycie
    jednego wiersza zmienia zestaw par.

    Średnia (L1) pokazuje się dopiero od ``K_ANONYMITY`` wpisów **ocenionych** – grupa z dziesięcioma
    wpisami i dwoma wynikami ma średnią dwóch osób.
    """
    lines = []
    for key, label, scope in scopes:
        aggregate = stage.population(scope)
        own = _own(own_rows, scope, publication)
        lines.append(
            {
                "key": key,
                "label": label,
                "scope": scope,
                "aggregate": aggregate,
                "visible": visible_for_supervisor(aggregate, own),
            }
        )
    changed = True
    while changed:
        changed = False
        for index, inner in enumerate(lines):
            if not inner["visible"]:
                continue
            for outer in lines[index + 1 :]:
                if outer["visible"] and not nested_safe(
                    stage, inner["scope"], outer["scope"], own_rows, publication
                ):
                    inner["visible"] = False
                    changed = True
                    break
    for line in lines:
        aggregate = line["aggregate"]
        line["mean"] = (
            aggregate.mean
            if line["visible"] and stage.has_means and aggregate.scored >= K_ANONYMITY
            else None
        )
    return lines


def _mine(rows: list[MemberRow], publication: _Publication | None) -> Aggregate:
    return _own(rows, EVERYONE, publication)


def coordinator_visibility(
    stage: StageStats, own_by_group: dict[str, list[MemberRow]], publication
) -> tuple[set[str], set[str]]:
    """Grupy i regiony etapu **ukryte** u koordynatora (ekran, CSV, PDF): ``(grupy, regiony)``.

    Trzy warstwy:

    - próg: grupa i region od ``K_ANONYMITY`` wpisów,
    - dopełnienie (M4): grupa szkoły z wykazu przechodzi :func:`visible_for_supervisor` względem
      **sumy** uczniów wszystkich opiekunów tej szkoły w konkursie – plik z rankingiem trafia do
      szkół, a każdy z tych nauczycieli zna punkty swoich uczniów,
    - zagnieżdżenie (H1): region minus suma pokazanych szkół w tym regionie ma 0 albo ≥ ``K``
      wpisów (i ocenionych); inaczej ukrywamy najmniejszą pokazaną szkołę regionu i liczymy znowu.
      Para „region – jedna szkoła” jest szczególnym przypadkiem tej sumy.
    """
    hidden_groups = {
        key
        for key, aggregate in stage.groups.items()
        if not visible_for_supervisor(aggregate, _mine(own_by_group.get(key, []), publication))
    }
    hidden_regions = {code for code, aggregate in stage.regions.items() if aggregate.entries < K_ANONYMITY}
    children: dict[str, list[str]] = defaultdict(list)
    for group, region in stage.group_regions:
        children[region].append(group)
    for region, aggregate in stage.regions.items():
        if region in hidden_regions:
            continue
        while True:
            shown = [group for group in children.get(region, []) if group not in hidden_groups]
            if not shown:
                break
            parts = [stage.group_regions[(group, region)] for group in shown]
            if all(
                _safe_rest(getattr(aggregate, metric) - sum(getattr(part, metric) for part in parts))
                for metric in CHECKED_METRICS
            ):
                break
            hidden_groups.add(
                min(shown, key=lambda group: (stage.group_regions[(group, region)].entries, group))
            )
    return hidden_groups, hidden_regions


def coordinator_cell(stage: StageStats, aggregate: Aggregate | None, *, hidden: bool = False) -> dict:
    """Komórka koordynatora: liczba wpisów zawsze, wyniki tylko poza ukryciem i od progu ocenionych."""
    aggregate = aggregate or Aggregate()
    shown = not hidden and aggregate.entries >= K_ANONYMITY
    return {
        "entries": aggregate.entries,
        "hidden": not shown,
        "mean": aggregate.mean if shown and stage.has_means and aggregate.scored >= K_ANONYMITY else None,
        "qualified": aggregate.qualified if shown and stage.published else None,
    }


# --- uczniowie opiekunów szkoły (M4) ------------------------------------------------------------


def supervisor_entries_by_school(
    competition_id: int, school_ids: list[int], stage_ids: list[int]
) -> dict[int, set[int]]:
    """``szkoła → wpisy uczniów, którzy wskazali **któregokolwiek** opiekuna tej szkoły`` w konkursie.

    Ta sama reguła dopasowania, co ``students_of`` (adres opiekuna bez względu na wielkość liter,
    uczestnicy konkursu profilu opiekuna), tylko hurtem dla wielu szkół naraz – dwa zapytania.
    """
    from apps.accounts.models import SchoolSupervisor
    from apps.accounts.supervisors import normalize_supervisor_email

    if not school_ids or not stage_ids:
        return {}
    emails: dict[str, set[int]] = defaultdict(set)
    for school_id, email in SchoolSupervisor.objects.filter(
        competition_id=competition_id, school_ref_id__in=school_ids
    ).values_list("school_ref_id", "user__email"):
        normalized = normalize_supervisor_email(email)
        if normalized:
            emails[normalized].add(school_id)
    if not emails:
        return {}
    result: dict[int, set[int]] = defaultdict(set)
    for entry_id, email in (
        StageEntry.objects.filter(stage_id__in=stage_ids, participant__competition_id=competition_id)
        .annotate(_email=Lower("participant__supervisor_email"))
        .filter(_email__in=list(emails))
        .values_list("pk", "_email")
    ):
        for school_id in emails.get(email, ()):
            result[school_id].add(entry_id)
    return result


# --- opiekun szkolny -----------------------------------------------------------------------------


@dataclass
class StudentCell:
    """Stan jednego ucznia w jednym etapie – to, co opiekun widzi w kratce tabeli."""

    registered: bool = False
    submitted: bool | None = None
    on_time: bool | None = None
    published: bool = False
    total: Decimal | None = None
    qualified: bool | None = None
    off_list: bool = False


def _student_cell(row: EntryRow | None, stage: StageStats, publication: _Publication | None) -> StudentCell:
    """Kratka ucznia. Punkty i kwalifikacja wyłącznie po publikacji, według jej trybu.

    Lista „tylko awansujący”: uczeń z listy ma swoją sumę (stoi w ogłoszeniu), uczeń spoza listy –
    zdanie „brak na liście” **bez** punktów. Organizator ogłosił tylko listę, więc i opiekun nie
    dostaje więcej niż to, co ogłoszono, plus to, czego lista dowodzi przez nieobecność.
    """
    if row is None:
        return StudentCell()
    cell = StudentCell(
        registered=True,
        submitted=row.has_submission if stage.has_submissions else None,
        on_time=(row.has_submission and not row.has_late) if stage.has_submissions else None,
    )
    if publication is None:
        return cell
    cell.published = True
    cell.qualified = row.status == StageEntryStatus.QUALIFIED
    if publication.qualified_only and not cell.qualified:
        cell.off_list = True
        return cell
    cell.total = publication.totals.get(row.entry_id)
    return cell


def supervisor_scope(supervisor, students, competition=None) -> dict:
    """Szkoła i region, z którymi porównujemy uczniów opiekuna.

    Szkoła – **tylko** z dowiązaniem do wykazu, po weryfikacji przez organizatora **tego** konkursu
    (STAT-01 § 4, M2): rejestracja opiekuna jest otwarta, a bez weryfikacji każdy mógłby podać się za
    nauczyciela dowolnej szkoły i oglądać jej wyniki. Profil z innego konkursu jest tu
    niezweryfikowany – weryfikował go inny organizator. Region – województwo szkoły
    z wykazu, a bez niej województwo najczęstsze wśród uczniów.
    """
    school = supervisor.school_ref if supervisor.school_ref_id else None
    same_competition = competition is None or supervisor.competition_id == getattr(competition, "pk", None)
    verified = bool(supervisor.verified and same_competition)
    school_key = f"s{school.pk}" if school is not None and verified else None
    if school is not None:
        region = school.voivodeship
    else:
        districts = Counter(student.district for student in students if student.district)
        region = districts.most_common(1)[0][0] if districts else ""
    return {"school": school, "school_key": school_key, "region": region, "verified": verified}


def _supervisor_scopes(scope: dict, axis: GroupAxis) -> list[tuple[str, object, Scope]]:
    scopes: list[tuple[str, object, Scope]] = [("mine", _("Moi uczniowie"), Scope(empty=True))]
    if scope["school_key"]:
        scopes.append(("school", _("Szkoła"), Scope(group=scope["school_key"])))
    if scope["region"]:
        scopes.append(("region", axis.region_label(scope["region"]), Scope(region=scope["region"])))
    scopes.append(("all", _("Wszyscy uczestnicy"), EVERYONE))
    return scopes


def _comparison(
    stage: StageStats, own: list[MemberRow], publication, scope: dict, axis: GroupAxis
) -> list[dict]:
    """Wiersze porównania jednego etapu: moi uczniowie (bez progu) i grupy z regułą progu."""
    scopes = _supervisor_scopes(scope, axis)
    lines = scoped_lines(stage, scopes[1:], own, publication)
    mine = _mine(own, publication)
    return [
        {
            "key": "mine",
            "label": scopes[0][1],
            "scope": None,
            "aggregate": mine,
            "visible": True,
            # Średnia własnych uczniów bez progu: opiekun widzi każdy z tych wyników osobno.
            "mean": mine.mean if stage.has_means else None,
        },
        *lines,
    ]


def supervisor_statistics(
    supervisor, competition, edition: Edition | None, *, axis: GroupAxis = SCHOOL_AXIS
) -> dict:
    """Komplet danych ekranu opiekuna: tabela uczniów edycji, porównania i postęp przez edycje.

    Uczniowie to **wyłącznie** ci, którzy wskazali opiekuna (``students_of`` – reguła sprzed tej
    funkcji, bez zmian). Zapytań jest stała liczba niezależnie od liczby uczniów i edycji.
    """
    from apps.accounts.supervisors import students_of

    students = students_of(supervisor)
    scope = supervisor_scope(supervisor, students, competition)
    editions = list(Edition.objects.filter(competition=competition).order_by("created_at", "id"))
    by_edition = stages_by_edition(editions)
    summaries = {
        item.pk: edition_summary(item, axis, stages=by_edition.get(item.pk, [])) for item in editions
    }
    all_stage_ids = [stage.pk for stages in by_edition.values() for stage in stages]
    rows = entry_rows(all_stage_ids, [student.pk for student in students])
    published_ids = {
        stage.stage_id for summary in summaries.values() for stage in summary.stages if stage.published
    }
    members = effective_members(rows, published_ids, axis)
    own_by_stage: dict[int, list[MemberRow]] = defaultdict(list)
    for row in members:
        own_by_stage[row.stage_id].append(row)
    pubs = publications(sorted(published_ids & set(own_by_stage)))

    summary = summaries.get(edition.pk) if edition is not None else None
    table = []
    comparisons = []
    if summary is not None:
        for student in students:
            by_stage = {row.stage_id: row for row in rows if row.participant_id == student.pk}
            table.append(
                {
                    "participant": student,
                    "cells": [
                        _student_cell(by_stage.get(stage.stage_id), stage, pubs.get(stage.stage_id))
                        for stage in summary.stages
                    ],
                }
            )
        for stage in summary.published_stages:
            comparisons.append(
                {
                    "stage": stage,
                    "lines": _comparison(
                        stage, own_by_stage.get(stage.stage_id, []), pubs.get(stage.stage_id), scope, axis
                    ),
                }
            )
    return {
        "students": students,
        "scope": scope,
        "editions": list(reversed(editions)),
        "edition": edition,
        "summary": summary,
        "table": table,
        "comparisons": comparisons,
        "progress": _progress(editions, summaries, own_by_stage, pubs, scope, axis),
    }


def _progress(editions, summaries, own_by_stage, pubs, scope, axis) -> dict:
    """Postęp przez edycje: liczba uczniów opiekuna i średnie w pierwszym etapie z pełną publikacją.

    Te same wiersze i ta sama reguła progu, co porównanie etapu (:func:`_comparison`) – wykres nie
    może pokazać liczby, której nie pokazuje tabela. Edycja bez takiego etapu ma punkt pusty
    (przerwa w linii), a nie zero – zero byłoby wynikiem.
    """
    labels: list[str] = []
    rows = []
    series: dict[str, list[Decimal | None]] = {"mine": [], "school": [], "region": [], "all": []}
    for edition in editions:
        summary = summaries[edition.pk]
        participants = {
            row.participant_id for stage in summary.stages for row in own_by_stage.get(stage.stage_id, [])
        }
        stage = summary.first_stage_with_means
        values = dict.fromkeys(series)
        if stage is not None:
            for line in _comparison(
                stage, own_by_stage.get(stage.stage_id, []), pubs.get(stage.stage_id), scope, axis
            ):
                values[line["key"]] = line["mean"]
        if not participants and stage is None:
            continue
        labels.append(edition.year_label)
        for key in series:
            series[key].append(values[key])
        rows.append({"edition": edition, "participants": len(participants), "stage": stage, "values": values})
    return {"labels": labels, "series": series, "rows": rows}


# --- koordynator ---------------------------------------------------------------------------------

#: Porządki rankingu. Klucz trafia do adresu (``?sort=``), więc jest krótki i stały.
SORTS = ("participants", "results", "name")


def _own_by_group(edition: Edition, summary: EditionSummary, axis) -> dict[int, dict[str, list]]:
    """``etap → grupa → wiersze uczniów opiekunów tej szkoły`` (przynależność zamrożona) – dla M4."""
    school_ids = [info.school_id for info in summary.groups.values() if info.school_id]
    published = [stage.stage_id for stage in summary.published_stages]
    entries = supervisor_entries_by_school(edition.competition_id, school_ids, published)
    if not entries:
        return {}
    wanted = sorted({entry for ids in entries.values() for entry in ids})
    frozen = frozen_members(published, axis, entry_ids=wanted)
    result: dict[int, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for stage_id, rows in frozen.items():
        for row in rows:
            for school_id, ids in entries.items():
                if row.entry_id in ids:
                    result[stage_id][f"s{school_id}"].append(row)
    return result


def coordinator_statistics(
    edition: Edition, *, sort: str = "participants", axis: GroupAxis = SCHOOL_AXIS
) -> dict:
    """Ranking szkół, tabela województw i szkoły „do odzyskania” dla jednej edycji."""
    from apps.core.text import fold

    stages = competition_stages(edition)
    summary = edition_summary(edition, axis, stages=stages)
    previous = previous_edition(edition)
    before = edition_summary(previous, axis) if previous is not None else None
    published = summary.published_stages
    own = _own_by_group(edition, summary, axis)
    pubs = publications([stage.stage_id for stage in published]) if own else {}
    visibility = {
        stage.stage_id: coordinator_visibility(stage, own.get(stage.stage_id, {}), pubs.get(stage.stage_id))
        for stage in published
    }
    ranking = []
    for key, info in summary.groups.items():
        if not info.participants:
            continue
        prior = before.groups.get(key) if before is not None else None
        cells = [
            coordinator_cell(stage, stage.groups.get(key), hidden=key in visibility[stage.stage_id][0])
            for stage in published
        ]
        ranking.append(
            {
                "group": info,
                "region_label": axis.region_label(info.region),
                "participants": info.participants,
                "previous": prior.participants if prior is not None else (0 if before is not None else None),
                "cells": cells,
            }
        )
    first = summary.first_stage_with_means
    first_index = published.index(first) if first is not None else None

    def result_key(row):
        mean = row["cells"][first_index]["mean"] if first_index is not None else None
        return (mean is None, -(mean or 0), -row["participants"], fold(row["group"].label))

    if sort == "results":
        ranking.sort(key=result_key)
    elif sort == "name":
        ranking.sort(key=lambda row: fold(row["group"].label))
    else:
        ranking.sort(key=lambda row: (-row["participants"], fold(row["group"].label)))
    for row in ranking:
        previous_count = row["previous"]
        row["change"] = row["participants"] - previous_count if previous_count is not None else None

    lost = []
    if before is not None:
        lost = sorted(
            (
                info
                for key, info in before.groups.items()
                if info.participants and not (summary.groups.get(key) and summary.groups[key].participants)
            ),
            key=lambda info: (-info.participants, fold(info.label)),
        )
    top = max((info.participants for info in summary.regions.values()), default=0)
    regions = []
    for code, info in sorted(
        summary.regions.items(), key=lambda item: (-item[1].participants, fold(item[1].label))
    ):
        cell = (
            coordinator_cell(first, first.regions.get(code), hidden=code in visibility[first.stage_id][1])
            if first is not None
            else None
        )
        regions.append(
            {
                "region": info,
                "share_width": width_class(info.participants * 100 / top if top else 0),
                "cell": cell,
            }
        )
    return {
        "summary": summary,
        "previous": previous,
        "previous_summary": before,
        "published": published,
        "first_stage": first,
        "ranking": ranking,
        "lost": lost,
        "regions": regions,
        "sort": sort if sort in SORTS else "participants",
    }


def ranking_dataset(edition: Edition, *, axis: GroupAxis = SCHOOL_AXIS):
    """Ranking szkół jako ``apps.core.exports.Dataset`` – te same liczby i ten sam próg, co ekran.

    W pliku nie ma żadnej kolumny o osobie: szkoła, RSPO, miejscowość, województwo i liczby.
    """
    from django.utils.translation import gettext

    from apps.core.exports import Dataset

    data = coordinator_statistics(edition, axis=axis)
    header = [
        gettext("Szkoła"),
        gettext("RSPO"),
        gettext("Miejscowość"),
        gettext("Województwo"),
        gettext("Uczestnicy"),
        gettext("Uczestnicy w poprzedniej edycji"),
        gettext("Zmiana"),
    ]
    for stage in data["published"]:
        header += [
            f"{stage.name}: " + gettext("wpisy"),
            f"{stage.name}: " + gettext("średnia"),
            f"{stage.name}: " + gettext("zakwalifikowani"),
        ]

    def rows():
        for row in data["ranking"]:
            info = row["group"]
            line = [
                info.label,
                info.rspo,
                info.city,
                row["region_label"],
                row["participants"],
                row["previous"],
                row["change"],
            ]
            for cell in row["cells"]:
                line += [cell["entries"], cell["mean"], cell["qualified"]]
            yield line

    return Dataset(
        header=header,
        rows=rows(),
        count=len(data["ranking"]),
        title=gettext("Statystyki szkół"),
        filename=f"statystyki-szkol-{edition.pk}",
    )


def lost_schools_dataset(edition: Edition, *, axis: GroupAxis = SCHOOL_AXIS):
    """Szkoły „do odzyskania” jako CSV – lista adresatów akcji promocyjnej (z RSPO do korespondencji)."""
    from django.utils.translation import gettext

    from apps.core.exports import Dataset

    data = coordinator_statistics(edition, axis=axis)
    lost = data["lost"]

    def rows():
        for info in lost:
            yield [info.label, info.rspo, info.city, axis.region_label(info.region), info.participants]

    return Dataset(
        header=[
            gettext("Szkoła"),
            gettext("RSPO"),
            gettext("Miejscowość"),
            gettext("Województwo"),
            gettext("Uczestnicy w poprzedniej edycji"),
        ],
        rows=rows(),
        count=len(lost),
        title=gettext("Szkoły do odzyskania"),
        filename=f"szkoly-do-odzyskania-{edition.pk}",
    )


# --- raport szkoły (PDF) -----------------------------------------------------------------------------


def school_report(school, edition: Edition, *, own_participant_ids: list[int], axis=SCHOOL_AXIS):
    """Dane raportu szkoły dla dyrektora: same agregaty, bez jednego nazwiska i kodu uczestnika.

    ``own_participant_ids`` – uczniowie, których wyniki zna odbiorca raportu: u opiekuna – jego
    uczniowie, u koordynatora (M4) – uczniowie **wszystkich** opiekunów tej szkoły w konkursie, bo
    raport trafia do szkoły. Obowiązuje ta sama reguła, co na ekranie opiekuna: próg, dopełnienie
    i zagnieżdżenie (szkoła ⊂ województwo ⊂ całość).

    Historia udziału (L2): liczba uczniów szkoły w edycji tylko wtedy, gdy jest bezpieczna wobec
    znanych uczniów (0 albo ≥ ``K`` i dopełnienie 0 albo ≥ ``K``) – inaczej „mniej niż K”.
    """
    key = f"s{school.pk}"
    editions = list(
        Edition.objects.filter(
            competition_id=edition.competition_id, created_at__lte=edition.created_at
        ).order_by("created_at", "id")
    )
    if edition not in editions:
        editions.append(edition)
    by_edition = stages_by_edition(editions)
    summaries = {
        item.pk: edition_summary(item, axis, stages=by_edition.get(item.pk, [])) for item in editions
    }
    summary = summaries[edition.pk]
    all_stage_ids = [stage.pk for stages in by_edition.values() for stage in stages]
    rows = entry_rows(all_stage_ids, own_participant_ids) if own_participant_ids else []
    published_ids = {
        stage.stage_id for item in summaries.values() for stage in item.stages if stage.published
    }
    members = effective_members(rows, published_ids, axis)
    own_by_stage: dict[int, list[MemberRow]] = defaultdict(list)
    for row in members:
        own_by_stage[row.stage_id].append(row)
    pubs = publications(
        [stage.stage_id for stage in summary.published_stages if own_by_stage.get(stage.stage_id)]
    )
    scopes = [
        ("school", _("Szkoła"), Scope(group=key)),
        ("region", axis.region_label(school.voivodeship), Scope(region=school.voivodeship)),
        ("all", _("Wszyscy uczestnicy"), EVERYONE),
    ]
    stages = [
        {
            "stage": stage,
            "lines": scoped_lines(
                stage, scopes, own_by_stage.get(stage.stage_id, []), pubs.get(stage.stage_id)
            ),
        }
        for stage in summary.stages
    ]
    history = []
    for item in editions:
        info = summaries[item.pk].groups.get(key)
        count = info.participants if info is not None else 0
        own_count = len(
            {
                row.participant_id
                for stage in summaries[item.pk].stages
                for row in own_by_stage.get(stage.stage_id, [])
                if row.group_key == key and row.participant_id is not None
            }
        )
        visible = count == 0 or (count >= K_ANONYMITY and _safe_rest(count - own_count))
        history.append({"edition": item, "participants": count if visible else None, "visible": visible})
    return {"school": school, "edition": edition, "summary": summary, "stages": stages, "history": history}


def school_supervisor_participants(competition_id: int, school_id: int) -> list[int]:
    """Uczestnicy, którzy wskazali **któregokolwiek** opiekuna szkoły w konkursie (M4, raport)."""
    from apps.accounts.models import Participant, SchoolSupervisor
    from apps.accounts.supervisors import normalize_supervisor_email

    emails = {
        normalize_supervisor_email(email)
        for email in SchoolSupervisor.objects.filter(
            competition_id=competition_id, school_ref_id=school_id
        ).values_list("user__email", flat=True)
    } - {""}
    if not emails:
        return []
    return list(
        Participant.objects.filter(competition_id=competition_id)
        .annotate(_email=Lower("supervisor_email"))
        .filter(_email__in=list(emails))
        .values_list("pk", flat=True)
    )
