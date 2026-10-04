"""Agregaty udziału i wyników po szkołach – logika STAT-01 (``docs/tasks/STAT-01.md``).

Trzy zasady, na których stoi cały moduł:

- **nic przed publikacją.** Punkty pochodzą wyłącznie z ``ResultsPublication.entry_totals`` –
  zamrożonej mapy sum z chwili ogłoszenia – a kwalifikacja liczy się tylko w etapie, który ma
  publikację. Etap bez publikacji ma w statystyce liczbę wpisów i oddanych prac, i nic ponadto.
  Bieżące ``StageEntry.total_points`` nie jest tu czytane wcale: po późnej decyzji komisji
  statystyka twierdziłaby wtedy coś innego niż ogłoszona tabela,
- **lista „tylko awansujący” nie ma średnich.** Ze snapshotu samych zakwalifikowanych publiczne
  statystyki też niczego nie liczą (``apps.results.statistics.build_statistics``) – średnia szkoły
  z takiego etapu zdradzałaby wyniki, których organizator świadomie nie ogłosił,
- **k-anonimowość (próg 5) na wyjściu, nie na wejściu.** Agregaty liczymy w całości i dopiero
  widok dla konkretnej roli decyduje, co wolno pokazać (:func:`visible_for_supervisor`,
  :func:`coordinator_cell`). Dzięki temu pamięć podręczna jest jedna dla wszystkich ról, a reguła
  progu jest w jednym miejscu, a nie w każdym szablonie.

W pamięci podręcznej leżą wyłącznie agregaty (liczby po grupach) – żadnego identyfikatora
uczestnika ani wpisu. Część „moi uczniowie” opiekuna liczy się zawsze na żywo, jednym zapytaniem.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import NamedTuple

from django.core.cache import cache
from django.db.models import Exists, OuterRef
from django.utils.translation import gettext_lazy as _

from apps.competitions.models import Edition, Stage, StageEntry, StageEntryStatus, StageFormat, StageKind
from apps.core.points import round_points, to_points
from apps.results.models import ResultsPublication
from apps.results.statistics import width_class
from apps.submissions.models import Submission, SubmissionStatus

from .grouping import SCHOOL_AXIS, EntryRow, GroupAxis

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

CACHE_PREFIX = "school_stats:v1"
#: Edycja, w której **każdy** etap ma publikację, już się nie zmienia (ponowna publikacja zmienia
#: odcisk w kluczu) – doba wystarcza z zapasem.
CACHE_TTL_FINAL = 24 * 3600
#: Edycja w toku: liczby udziału i oddanych prac są żywe, więc pięć minut. Dłużej koordynator
#: oglądałby wczorajszą liczbę zgłoszeń w środku akcji promocyjnej.
CACHE_TTL_LIVE = 5 * 60


def enabled(competition) -> bool:
    """Czy ten konkurs ma statystyki szkół. **Jedyne** wejście do flagi; brak konkursu = brak funkcji."""
    return competition is not None and competition.has_feature(FLAG)


# --- agregat ------------------------------------------------------------------------------------


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

    def add(self, row: EntryRow, publication: _Publication | None) -> None:
        self.entries += 1
        if row.has_submission:
            self.submitted += 1
            if not row.has_late:
                self.on_time += 1
        if publication is None:
            return
        if row.status == StageEntryStatus.QUALIFIED:
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


class _Publication(NamedTuple):
    qualified_only: bool
    totals: dict[int, Decimal]


@dataclass
class StageStats:
    """Etap edycji z agregatami: całość, województwa i grupy (szkoły)."""

    stage_id: int
    name: str
    kind: str
    has_submissions: bool
    published: bool
    qualified_only: bool
    all: Aggregate = field(default_factory=Aggregate)
    regions: dict[str, Aggregate] = field(default_factory=dict)
    groups: dict[str, Aggregate] = field(default_factory=dict)

    @property
    def has_means(self) -> bool:
        """Czy w tym etapie w ogóle liczy się średnie (publikacja pełnej tabeli)."""
        return self.published and not self.qualified_only


@dataclass
class GroupInfo:
    """Grupa w edycji: podpis, miejscowość, region i liczba **różnych** uczestników."""

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


# --- odczyt z bazy --------------------------------------------------------------------------------


def competition_stages(edition: Edition) -> list[Stage]:
    """Etapy zawodów edycji (bez treningu), w kolejności terminów – tej samej, co na osi czasu."""
    return list(
        Stage.objects.filter(edition=edition)
        .exclude(kind=StageKind.TRAINING)
        .only("id", "edition_id", "kind", "name", "format", "opens_at", "results_published_at")
        .order_by("opens_at", "id")
    )


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
    queryset = queryset.annotate(_has_submission=Exists(live), _has_late=Exists(live.filter(is_late=True)))
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
        ).order_by("pk")
    ]


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
    """Agregaty edycji, z pamięci podręcznej albo policzone (dwa zapytania plus jedno o etapy).

    Czas życia zależy od tego, czy edycja jest zamknięta publikacjami: doba, gdy każdy etap ma
    publikację; pięć minut, dopóki choć jeden jej nie ma (``CACHE_TTL_*``).
    """
    stages = competition_stages(edition) if stages is None else stages
    key = cache_key(edition, stages, axis)
    cached = cache.get(key)
    if cached is not None:
        return cached
    summary = build_summary(edition, stages, axis)
    final = bool(stages) and all(stage.results_published_at for stage in stages)
    cache.set(key, summary, CACHE_TTL_FINAL if final else CACHE_TTL_LIVE)
    return summary


def build_summary(edition: Edition, stages: list[Stage], axis: GroupAxis = SCHOOL_AXIS) -> EditionSummary:
    """Agregaty edycji bez pamięci podręcznej – jeden przebieg po wpisach."""
    stage_ids = [stage.pk for stage in stages]
    published = publications([stage.pk for stage in stages if stage.results_published_at])
    stats = {
        stage.pk: StageStats(
            stage_id=stage.pk,
            name=stage.display_name,
            kind=stage.kind,
            has_submissions=stage.format == StageFormat.SUBMISSIONS,
            published=stage.pk in published,
            qualified_only=bool(published.get(stage.pk) and published[stage.pk].qualified_only),
        )
        for stage in stages
    }
    groups: dict[str, GroupInfo] = {}
    group_members: dict[str, set[int]] = defaultdict(set)
    group_regions: dict[str, Counter] = defaultdict(Counter)
    region_members: dict[str, set[int]] = defaultdict(set)
    region_groups: dict[str, set[str]] = defaultdict(set)
    everyone: set[int] = set()

    for row in entry_rows(stage_ids):
        stage = stats[row.stage_id]
        publication = published.get(row.stage_id)
        stage.all.add(row, publication)
        everyone.add(row.participant_id)
        region = axis.region_of(row)
        stage.regions.setdefault(region, Aggregate()).add(row, publication)
        region_members[region].add(row.participant_id)
        group = axis.group_of(row)
        if group is None:
            continue
        stage.groups.setdefault(group.key, Aggregate()).add(row, publication)
        group_members[group.key].add(row.participant_id)
        group_regions[group.key][region] += 1
        region_groups[region].add(group.key)
        groups.setdefault(
            group.key,
            GroupInfo(
                key=group.key,
                label=group.label,
                city=group.city,
                school_id=group.school_id,
                rspo=row.school_rspo,
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


def visible_for_supervisor(group: Aggregate, mine: Aggregate) -> bool:
    """Czy opiekun może zobaczyć agregat grupy, w której są też jego uczniowie (STAT-01 § 3).

    Dwa warunki: grupa ma co najmniej ``K_ANONYMITY`` wpisów, **i** część grupy spoza uczniów
    opiekuna ma ich zero albo co najmniej ``K_ANONYMITY``. Drugi warunek jest właściwą ochroną:
    opiekun zna punkty swoich uczniów, więc średnia szkoły z sześcioma wpisami, z których pięć to
    jego uczniowie, wyznacza wynik szóstego co do punktu. Ten sam rachunek robimy osobno dla
    wpisów ocenionych (średnia liczy się z nich, nie ze wszystkich wpisów).
    """

    def safe(total: int, own: int) -> bool:
        rest = total - own
        return rest == 0 or rest >= K_ANONYMITY

    if group.entries < K_ANONYMITY:
        return False
    return safe(group.entries, mine.entries) and safe(group.scored, mine.scored)


def coordinator_cell(stage: StageStats, aggregate: Aggregate | None) -> dict:
    """Komórka rankingu koordynatora: liczba wpisów zawsze, wyniki dopiero od progu k-anonimowości.

    Liczba wpisów jest daną operacyjną, którą koordynator widzi i tak na liście uczestników; średnia
    i liczba zakwalifikowanych grupy poniżej progu wskazywałyby wynik konkretnego ucznia w pliku,
    który z założenia wędruje dalej (akcja promocyjna, kuratorium).
    """
    aggregate = aggregate or Aggregate()
    shown = aggregate.entries >= K_ANONYMITY
    return {
        "entries": aggregate.entries,
        "hidden": not shown,
        "mean": aggregate.mean if shown and stage.has_means else None,
        "qualified": aggregate.qualified if shown and stage.published else None,
    }


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


def _mine(rows: list[EntryRow], publication: _Publication | None) -> Aggregate:
    aggregate = Aggregate()
    for row in rows:
        aggregate.add(row, publication)
    return aggregate


def supervisor_scope(supervisor, students) -> dict:
    """Szkoła i region, z którymi porównujemy uczniów opiekuna.

    Szkoła – **tylko** z dowiązaniem do wykazu i po weryfikacji przez organizatora (STAT-01 § 4):
    rejestracja opiekuna jest otwarta, a bez weryfikacji każdy mógłby podać się za nauczyciela
    dowolnej szkoły i oglądać jej wyniki. Region – województwo szkoły z wykazu, a bez niej
    województwo najczęstsze wśród uczniów (to nie ujawnia niczego, czego opiekun nie widzi i tak).
    """
    school = supervisor.school_ref if supervisor.school_ref_id else None
    school_key = f"s{school.pk}" if school is not None and supervisor.verified else None
    if school is not None:
        region = school.voivodeship
    else:
        districts = Counter(student.district for student in students if student.district)
        region = districts.most_common(1)[0][0] if districts else ""
    return {"school": school, "school_key": school_key, "region": region}


def _comparison(
    stage: StageStats, rows: list[EntryRow], publication, scope: dict, axis: GroupAxis
) -> list[dict]:
    """Wiersze porównania jednego etapu: moi uczniowie, szkoła, województwo, wszyscy."""
    lines = [
        {"key": "mine", "label": _("Moi uczniowie"), "aggregate": _mine(rows, publication), "visible": True}
    ]
    if scope["school_key"]:
        own = _mine([row for row in rows if f"s{row.school_ref_id}" == scope["school_key"]], publication)
        group = stage.groups.get(scope["school_key"], Aggregate())
        lines.append(
            {
                "key": "school",
                "label": _("Szkoła"),
                "aggregate": group,
                "visible": visible_for_supervisor(group, own),
            }
        )
    if scope["region"]:
        own = _mine([row for row in rows if axis.region_of(row) == scope["region"]], publication)
        group = stage.regions.get(scope["region"], Aggregate())
        lines.append(
            {
                "key": "region",
                "label": axis.region_label(scope["region"]),
                "aggregate": group,
                "visible": visible_for_supervisor(group, own),
            }
        )
    everyone = stage.all
    lines.append(
        {
            "key": "all",
            "label": _("Wszyscy uczestnicy"),
            "aggregate": everyone,
            "visible": visible_for_supervisor(everyone, lines[0]["aggregate"]),
        }
    )
    return lines


def supervisor_statistics(
    supervisor, competition, edition: Edition | None, *, axis: GroupAxis = SCHOOL_AXIS
) -> dict:
    """Komplet danych ekranu opiekuna: tabela uczniów edycji, porównania i postęp przez edycje.

    Uczniowie to **wyłącznie** ci, którzy wskazali opiekuna (``students_of`` – reguła sprzed tej
    funkcji, bez zmian). Zapytań jest stała liczba niezależnie od liczby uczniów i edycji: etapy
    konkursu (jedno), agregaty edycji (z pamięci podręcznej), wpisy uczniów opiekuna we wszystkich
    edycjach (jedno) i publikacje etapów, w których ci uczniowie mają wpisy (jedno).
    """
    from apps.accounts.supervisors import students_of

    students = students_of(supervisor)
    scope = supervisor_scope(supervisor, students)
    editions = list(Edition.objects.filter(competition=competition).order_by("created_at", "id"))
    stages_by_edition: dict[int, list[Stage]] = defaultdict(list)
    for stage in (
        Stage.objects.filter(edition__in=editions)
        .exclude(kind=StageKind.TRAINING)
        .only("id", "edition_id", "kind", "name", "format", "opens_at", "results_published_at")
        .order_by("opens_at", "id")
    ):
        stages_by_edition[stage.edition_id].append(stage)
    summaries = {
        item.pk: edition_summary(item, axis, stages=stages_by_edition.get(item.pk, [])) for item in editions
    }
    all_stage_ids = [stage.pk for stages in stages_by_edition.values() for stage in stages]
    rows = entry_rows(all_stage_ids, [student.pk for student in students])
    rows_by_stage: dict[int, list[EntryRow]] = defaultdict(list)
    for row in rows:
        rows_by_stage[row.stage_id].append(row)
    published_ids = {
        stage.stage_id for summary in summaries.values() for stage in summary.stages if stage.published
    }
    pubs = publications(sorted(published_ids & set(rows_by_stage)))

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
                        stage, rows_by_stage.get(stage.stage_id, []), pubs.get(stage.stage_id), scope, axis
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
        "progress": _progress(editions, summaries, rows_by_stage, pubs, scope, axis),
    }


def _progress(editions, summaries, rows_by_stage, pubs, scope, axis) -> dict:
    """Postęp przez edycje: liczba uczniów opiekuna i średnie w pierwszym etapie z pełną publikacją.

    Edycja bez takiego etapu ma punkt pusty (przerwa w linii), a nie zero – zero byłoby wynikiem.
    """
    labels: list[str] = []
    rows = []
    series: dict[str, list[Decimal | None]] = {"mine": [], "school": [], "region": [], "all": []}
    for edition in editions:
        summary = summaries[edition.pk]
        participants = {
            row.participant_id for stage in summary.stages for row in rows_by_stage.get(stage.stage_id, [])
        }
        stage = summary.first_stage_with_means
        values = dict.fromkeys(series)
        if stage is not None:
            for line in _comparison(
                stage, rows_by_stage.get(stage.stage_id, []), pubs.get(stage.stage_id), scope, axis
            ):
                if line["visible"]:
                    values[line["key"]] = line["aggregate"].mean
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


def coordinator_statistics(
    edition: Edition, *, sort: str = "participants", axis: GroupAxis = SCHOOL_AXIS
) -> dict:
    """Ranking szkół, tabela województw i szkoły „do odzyskania” dla jednej edycji."""
    from apps.core.text import fold

    summary = edition_summary(edition, axis)
    previous = previous_edition(edition)
    before = edition_summary(previous, axis) if previous is not None else None
    published = summary.published_stages
    ranking = []
    for key, info in summary.groups.items():
        prior = before.groups.get(key) if before is not None else None
        cells = [coordinator_cell(stage, stage.groups.get(key)) for stage in published]
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
            (info for key, info in before.groups.items() if key not in summary.groups and info.participants),
            key=lambda info: (-info.participants, fold(info.label)),
        )
    top = max((info.participants for info in summary.regions.values()), default=0)
    regions = []
    for code, info in sorted(
        summary.regions.items(), key=lambda item: (-item[1].participants, fold(item[1].label))
    ):
        cell = coordinator_cell(first, first.regions.get(code)) if first is not None else None
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


# --- raport szkoły (PDF) -----------------------------------------------------------------------------


def school_report(school, edition: Edition, *, own_rows: list[EntryRow] | None = None, axis=SCHOOL_AXIS):
    """Dane raportu szkoły dla dyrektora: same agregaty, bez jednego nazwiska i kodu uczestnika.

    ``own_rows`` – wpisy uczniów opiekuna, gdy raport pobiera opiekun: wtedy obowiązuje jego reguła
    progu (z dopełnieniem). Koordynator dostaje regułę prostą: grupa od ``K_ANONYMITY`` wpisów.
    Historia udziału (liczba uczniów szkoły w kolejnych edycjach) ma ten sam próg.
    """
    key = f"s{school.pk}"
    summary = edition_summary(edition, axis)
    pubs = publications([stage.stage_id for stage in summary.published_stages]) if own_rows else {}
    stages = []
    for stage in summary.stages:
        lines = []
        own_stage = [row for row in (own_rows or []) if row.stage_id == stage.stage_id]
        publication = pubs.get(stage.stage_id)
        for scope_key, label, aggregate, own in (
            (
                "school",
                _("Szkoła"),
                stage.groups.get(key, Aggregate()),
                [r for r in own_stage if r.school_ref_id == school.pk],
            ),
            (
                "region",
                axis.region_label(school.voivodeship),
                stage.regions.get(school.voivodeship, Aggregate()),
                [r for r in own_stage if axis.region_of(r) == school.voivodeship],
            ),
            ("all", _("Wszyscy uczestnicy"), stage.all, own_stage),
        ):
            if own_rows is None:
                visible = aggregate.entries >= K_ANONYMITY
            else:
                visible = visible_for_supervisor(aggregate, _mine(own, publication))
            lines.append({"key": scope_key, "label": label, "aggregate": aggregate, "visible": visible})
        stages.append({"stage": stage, "lines": lines})
    history = []
    for item in Edition.objects.filter(
        competition_id=edition.competition_id, created_at__lte=edition.created_at
    ).order_by("created_at", "id"):
        info = edition_summary(item, axis).groups.get(key)
        count = info.participants if info is not None else 0
        history.append(
            {"edition": item, "participants": count, "visible": count >= K_ANONYMITY or count == 0}
        )
    return {"school": school, "edition": edition, "summary": summary, "stages": stages, "history": history}
