"""Postęp klasy nauczyciela: jeden serwis dla ekranu, eksportu i kafli „Start” (UX-PANEL-02 § 4–5).

Skąd lista uczniów: **wyłącznie** ``apps.accounts.supervisors.students_of`` – uczniowie, którzy sami
wskazali adres nauczyciela, z konkursu jego profilu i bez kont po anonimizacji. Ten moduł nie ma
drugiej drogi do uczniów (po szkole, po imporcie, po klasie) i mieć nie może: cała reguła dostępu
stoi tam, a ekran, plik i kafle są trzema widokami tego samego wyniku.

Czego tu **nie ma** i dlaczego (spec § 5):

- treści prac, plików, komentarzy recenzentów i ocen cząstkowych – panel jest listą stanów, nie
  drugim wejściem do akt oceniania (ta sama reguła, co dawny pulpit opiekuna sprzed UX-PANEL-02),
- punktów i decyzji o kwalifikacji przed ogłoszeniem wyników etapu – wartość ``None``, a nie zero,
  bo zero wyglądałoby jak wynik; „ogłoszone” znaczy publikacja **w mocy** (``ResultsPublication.live``),
- adresów e-mail i danych kontaktowych uczniów,
- zgód, zaświadczenia i obecności – chyba że konkurs włączył ``supervisor_class_details``
  (:func:`details_enabled`); uczniowi obiecano, że nauczyciel jego zgód nie zobaczy, więc zmiana
  zakresu jest decyzją organizatora, która zmienia też informację dla ucznia.

Koszt: stała liczba zapytań względem liczby uczniów (D9) – każda kategoria danych jednym zapytaniem
dla całej klasy, złożenie w Pythonie.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from django.db.models import Count
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.bulk_registration import (
    STATE_ACTIVE,
    STATE_INVITED,
    STATE_SELF_REGISTERED,
    invitation_state,
)
from apps.accounts.supervisors import students_of

from .flags import DETAILS_FLAG, details_enabled  # noqa: F401 - wejście także dla wołających ``services``

ACCOUNT_LABELS = {
    STATE_ACTIVE: _("aktywne"),
    STATE_INVITED: _("zaproszony – bez konta"),
    STATE_SELF_REGISTERED: _("zapisał się sam"),
}
ACCOUNT_TONES = {STATE_ACTIVE: "ok", STATE_INVITED: "warn", STATE_SELF_REGISTERED: "neutral"}

#: Stan procedury etapu ucznia – klucze ``apps.submissions.status_track.STEPS`` i „nic nie oddano”.
STEP_NONE = "none"
STEP_LABELS = {
    STEP_NONE: _("nic nie oddano"),
    "submitted": _("oddane"),
    "review": _("w ocenie"),
    "graded": _("oceniona"),
    "results": _("wyniki"),
}

#: Zgoda rodzica (opiekuna prawnego) – wyłącznie za flagą ``supervisor_class_details``.
GUARDIAN_OK = "ok"
GUARDIAN_MISSING = "missing"
GUARDIAN_NA = "na"
GUARDIAN_LABELS = {
    GUARDIAN_OK: _("jest"),
    GUARDIAN_MISSING: _("brak"),
    GUARDIAN_NA: _("pełnoletni"),
}

#: Stany zaświadczenia o statusie ucznia (``apps.student_status``) – podpisy dla nauczyciela.
CERT_MISSING = "MISSING"
CERT_PENDING = "PENDING"
CERT_ACCEPTED = "ACCEPTED"
CERT_REJECTED = "REJECTED"
CERTIFICATE_LABELS = {
    CERT_MISSING: _("brak"),
    CERT_PENDING: _("czeka na weryfikację"),
    CERT_ACCEPTED: _("zaakceptowane"),
    CERT_REJECTED: _("odrzucone"),
}
CERTIFICATE_TONES = {
    CERT_MISSING: "warn",
    CERT_PENDING: "neutral",
    CERT_ACCEPTED: "ok",
    CERT_REJECTED: "danger",
}

DECISION_LABELS = {
    "QUALIFIED": _("zakwalifikowany"),
    "NOT_QUALIFIED": _("niezakwalifikowany"),
}


@dataclass
class StageColumn:
    stage: object
    problems: int
    published: bool
    open_now: bool

    @property
    def stage_id(self) -> int:
        return self.stage.pk


@dataclass
class StageCell:
    """Jeden etap jednego ucznia. ``points``/``decision`` są ``None`` do ogłoszenia wyników."""

    entered: bool = False
    submitted: int = 0
    total: int = 0
    last_at: datetime | None = None
    step: str = STEP_NONE
    points: Decimal | None = None
    decision: str = ""
    interview: bool = False
    #: Wpis nie jest dyskwalifikacją – zdyskwalifikowany nie „zalega” z pracą.
    status_open: bool = True

    @property
    def step_label(self):
        return STEP_LABELS.get(self.step, STEP_LABELS[STEP_NONE])

    @property
    def decision_label(self):
        return DECISION_LABELS.get(self.decision, "")


@dataclass
class StudentRow:
    participant: object
    account: str
    cells: list[StageCell] = field(default_factory=list)
    #: Zapisany do etapu przyjmującego teraz prace i nic w nim nie oddał.
    missing_work: bool = False
    guardian: str | None = None
    certificate: str | None = None
    workshops: int | None = None

    @property
    def name(self) -> str:
        return self.participant.user.get_full_name()

    @property
    def grade(self) -> str:
        return str(self.participant.grade or "")

    @property
    def account_label(self):
        return ACCOUNT_LABELS.get(self.account, "")

    @property
    def account_tone(self) -> str:
        return ACCOUNT_TONES.get(self.account, "neutral")

    @property
    def guardian_label(self):
        return GUARDIAN_LABELS.get(self.guardian or "", "")

    @property
    def certificate_label(self):
        return CERTIFICATE_LABELS.get(self.certificate or "", "")

    @property
    def certificate_tone(self) -> str:
        return CERTIFICATE_TONES.get(self.certificate or "", "neutral")

    @property
    def submitted_total(self) -> int:
        return sum(cell.submitted for cell in self.cells)


@dataclass
class ClassProgress:
    edition: object
    columns: list[StageColumn]
    rows: list[StudentRow]
    details: bool = False
    #: Kolumny za flagą: zaświadczenie tylko w konkursie, który je zbiera; zgoda rodzica tylko przy
    #: zestawie zgód, który jej wymaga; warsztaty – gdy strona „Warsztaty” ma harmonogram.
    show_certificate: bool = False
    show_guardian: bool = False
    show_workshops: bool = False
    workshops_total: int = 0
    workshops_page: object = None
    workshop_keys: list[str] = field(default_factory=list)

    @property
    def grades(self) -> list[str]:
        return sorted({row.grade for row in self.rows if row.grade}, key=_grade_key)


def _grade_key(value: str):
    return (0, int(value)) if value.isdigit() else (1, value)


# --- składanie ---------------------------------------------------------------------------------


def class_progress(supervisor, competition, *, now=None, edition=None) -> ClassProgress:
    """Uczniowie nauczyciela z etapami bieżącej edycji – patrz docstring modułu.

    ``edition`` można podać, gdy wołający już ją ma (pulpit); domyślnie bieżąca edycja konkursu.
    """
    from apps.competitions.models import Problem, Stage, StageEntry, StageKind
    from apps.competitions.services import current_edition
    from apps.results.models import ResultsPublication

    now = now or timezone.now()
    if edition is None:
        edition = current_edition(competition)
    participants = students_of(supervisor)
    details = details_enabled(competition)
    progress = ClassProgress(edition=edition, columns=[], rows=[], details=details)
    if details:
        _detail_switches(progress, competition, edition)

    # Kolumny etapów liczymy także przy pustej klasie – „Start” pokazuje z nich najbliższy termin.
    columns: list[StageColumn] = []
    stages = []
    if edition is not None:
        stages = list(
            Stage.objects.filter(edition=edition).exclude(kind=StageKind.TRAINING).order_by("opens_at", "id")
        )
    if stages:
        published = set(
            ResultsPublication.objects.live().filter(stage__in=stages).values_list("stage_id", flat=True)
        )
        problem_counts = {}
        if participants:
            problem_counts = dict(
                Problem.objects.filter(stage__in=stages)
                .order_by()
                .values("stage_id")
                .annotate(n=Count("id"))
                .values_list("stage_id", "n")
            )
        columns = [
            StageColumn(
                stage=stage,
                problems=1 if stage.is_quiz else problem_counts.get(stage.pk, 0),
                published=stage.pk in published,
                open_now=not stage.is_interview and stage.is_open_for_submissions(now),
            )
            for stage in stages
        ]
    progress.columns = columns
    if not participants:
        return progress

    entries: dict[tuple[int, int], object] = {}
    if stages:
        for entry in StageEntry.objects.filter(participant__in=participants, stage__in=stages).only(
            "id", "stage_id", "participant_id", "status", "total_points"
        ):
            entries[(entry.participant_id, entry.stage_id)] = entry
    work = _work_by_entry(columns, entries)

    for participant in participants:
        row = StudentRow(participant=participant, account=invitation_state(participant))
        for column in columns:
            entry = entries.get((participant.pk, column.stage_id))
            row.cells.append(_cell(column, entry, work))
        row.missing_work = any(
            column.open_now and cell.entered and cell.status_open and cell.submitted == 0
            for column, cell in zip(columns, row.cells, strict=True)
        )
        progress.rows.append(row)
    if details:
        _fill_details(progress, competition, edition)
    return progress


def _work_by_entry(columns: list[StageColumn], entries: dict) -> dict[int, dict]:
    """``{wpis: {"submitted": n, "last_at": …, "statuses": [...]}}`` – dwa zapytania dla całej klasy.

    Najnowsza wersja każdego zadania (praca odrzucona przez antywirusa nie jest „oddaną pracą” –
    ta sama reguła, co statystyki szkół (``school_stats.services.entry_rows``)). Test online liczy się
    zakończonym podejściem (``QuizAttempt.submitted_at``).
    """
    from apps.quiz.models import QuizAttempt
    from apps.submissions.models import Submission, SubmissionStatus

    if not entries:
        return {}
    quiz_stages = {column.stage_id for column in columns if column.stage.is_quiz}
    upload_entries = [entry.pk for entry in entries.values() if entry.stage_id not in quiz_stages]
    quiz_entries = [entry.pk for entry in entries.values() if entry.stage_id in quiz_stages]
    work: dict[int, dict] = {}
    if upload_entries:
        latest: dict[tuple[int, int], tuple[str, datetime]] = {}
        for entry_id, problem_id, status, submitted_at in (
            Submission.objects.filter(entry_id__in=upload_entries)
            .exclude(status=SubmissionStatus.REJECTED_INFECTED)
            .order_by("entry_id", "problem_id", "-version")
            .values_list("entry_id", "problem_id", "status", "submitted_at")
        ):
            latest.setdefault((entry_id, problem_id), (status, submitted_at))
        for (entry_id, _problem_id), (status, submitted_at) in latest.items():
            bucket = work.setdefault(entry_id, {"submitted": 0, "last_at": None, "statuses": []})
            bucket["submitted"] += 1
            bucket["statuses"].append(status)
            if bucket["last_at"] is None or submitted_at > bucket["last_at"]:
                bucket["last_at"] = submitted_at
    if quiz_entries:
        for entry_id, submitted_at in (
            QuizAttempt.objects.filter(entry_id__in=quiz_entries, submitted_at__isnull=False)
            .order_by("entry_id", "-submitted_at")
            .values_list("entry_id", "submitted_at")
        ):
            if entry_id in work:
                continue
            work[entry_id] = {"submitted": 1, "last_at": submitted_at, "statuses": [], "quiz": True}
    return work


def _cell(column: StageColumn, entry, work: dict) -> StageCell:
    from apps.competitions.models import StageEntryStatus
    from apps.submissions.status_track import status_track

    cell = StageCell(total=column.problems, interview=column.stage.is_interview)
    if entry is None:
        return cell
    cell.entered = True
    cell.status_open = entry.status != StageEntryStatus.DISQUALIFIED
    bucket = work.get(entry.pk)
    if bucket:
        cell.submitted = bucket["submitted"]
        cell.last_at = bucket["last_at"]
    if column.published:
        cell.step = "results"
        cell.points = entry.total_points
        cell.decision = entry.status if entry.status in DECISION_LABELS else ""
    elif bucket and bucket.get("quiz"):
        cell.step = "submitted"
    elif bucket:
        # Etap ucznia podpisuje praca **najmniej** zaawansowana (reguła dawnego pulpitu opiekuna):
        # jedna w ocenie i dwie ocenione to „w ocenie”, a nie „ocenione”.
        tracks = [
            status_track(submission=SimpleNamespace(status=status), stage=column.stage, publication=None)
            for status in bucket["statuses"]
        ]
        track = min(tracks, key=lambda t: sum(1 for step in t.steps if step.state == "done"))
        done = [step.key for step in track.steps if step.state == "done"]
        cell.step = done[-1] if done else STEP_NONE
    return cell


def _detail_switches(progress: ClassProgress, competition, edition) -> None:
    from apps.cms.workshops import workshop_rows, workshops_page
    from apps.coordinator_stats.funnel import guardian_required
    from apps.student_status.models import enabled as student_status_enabled

    progress.show_certificate = student_status_enabled(competition) and edition is not None
    progress.show_guardian = guardian_required(competition)
    page = workshops_page(competition)
    progress.workshops_page = page
    keys = [row["key"] for row in workshop_rows(page)]
    progress.workshops_total = len(keys)
    progress.show_workshops = bool(keys)
    progress.workshop_keys = keys


def _fill_details(progress: ClassProgress, competition, edition) -> None:
    """Zaświadczenie, zgoda rodzica i obecność – po jednym zapytaniu dla całej klasy (za flagą)."""
    from apps.accounts.guardian import requires_guardian_consent

    ids = [row.participant.pk for row in progress.rows]
    certificates: dict[int, str] = {}
    if progress.show_certificate:
        from apps.student_status.models import StudentStatusCertificate

        certificates = dict(
            StudentStatusCertificate.objects.filter(participant_id__in=ids, edition=edition, is_current=True)
            .order_by()
            .values_list("participant_id", "status")
        )
    attendance: dict[int, int] = {}
    if progress.show_workshops:
        from apps.workshop_certificates.rules import attendance_counts

        attendance = attendance_counts(competition, progress.workshop_keys, ids)
    for row in progress.rows:
        participant = row.participant
        if progress.show_guardian:
            if not requires_guardian_consent(participant):
                row.guardian = GUARDIAN_NA
            else:
                row.guardian = GUARDIAN_OK if participant.guardian_consent else GUARDIAN_MISSING
        if progress.show_certificate:
            state = certificates.get(participant.pk, CERT_MISSING)
            row.certificate = state if state in CERTIFICATE_LABELS else CERT_MISSING
        if progress.show_workshops:
            row.workshops = attendance.get(participant.pk, 0)


# --- filtry i sortowanie -------------------------------------------------------------------------

SORTS = ("name", "grade", "account", "work")
WORK_MISSING = "missing"


@dataclass(frozen=True)
class Filters:
    grade: str = ""
    account: str = ""
    work: str = ""
    certificate: str = ""
    guardian: str = ""
    sort: str = "name"
    descending: bool = False

    @property
    def sort_param(self) -> str:
        return f"-{self.sort}" if self.descending else self.sort

    @property
    def active(self) -> bool:
        return bool(self.grade or self.account or self.work or self.certificate or self.guardian)

    def query(self, **override) -> str:
        """Parametry adresu (do eksportu i nagłówków sortowania) – tylko niepuste."""
        from urllib.parse import urlencode

        values = {
            "klasa": self.grade,
            "konto": self.account,
            "praca": self.work,
            "zaswiadczenie": self.certificate,
            "zgoda": self.guardian,
            "sort": self.sort_param if self.sort_param != "name" else "",
        }
        values.update(override)
        return urlencode({key: value for key, value in values.items() if value})


def parse_filters(params, progress: ClassProgress) -> Filters:
    """Filtry z ``request.GET``. Wartość spoza zamkniętej listy znaczy „bez filtra” – nie błąd."""
    grade = (params.get("klasa") or "").strip()
    account = params.get("konto") or ""
    work = params.get("praca") or ""
    certificate = params.get("zaswiadczenie") or ""
    guardian = params.get("zgoda") or ""
    raw_sort = params.get("sort") or "name"
    descending = raw_sort.startswith("-")
    sort = raw_sort.lstrip("-")
    return Filters(
        grade=grade if grade in progress.grades else "",
        account=account if account in ACCOUNT_LABELS else "",
        work=work if work == WORK_MISSING else "",
        certificate=certificate if progress.show_certificate and certificate in CERTIFICATE_LABELS else "",
        guardian=guardian if progress.show_guardian and guardian == GUARDIAN_MISSING else "",
        sort=sort if sort in SORTS else "name",
        descending=descending and sort in SORTS,
    )


def apply_filters(progress: ClassProgress, filters: Filters) -> list[StudentRow]:
    rows = [
        row
        for row in progress.rows
        if (not filters.grade or row.grade == filters.grade)
        and (not filters.account or row.account == filters.account)
        and (not filters.work or row.missing_work)
        and (not filters.certificate or row.certificate == filters.certificate)
        and (not filters.guardian or row.guardian == filters.guardian)
    ]
    # Najpierw porządek nazwiskami (stabilny), potem klucz wybrany – remisy zostają alfabetyczne.
    rows.sort(
        key=lambda row: (row.participant.user.last_name.lower(), row.participant.user.first_name.lower())
    )
    if filters.sort == "grade":
        rows.sort(key=lambda row: _grade_key(row.grade) if row.grade else (2, ""), reverse=filters.descending)
    elif filters.sort == "account":
        order = {STATE_INVITED: 0, STATE_SELF_REGISTERED: 1, STATE_ACTIVE: 2}
        rows.sort(key=lambda row: order.get(row.account, 3), reverse=filters.descending)
    elif filters.sort == "work":
        rows.sort(key=lambda row: row.submitted_total, reverse=filters.descending)
    elif filters.descending:
        rows.reverse()
    return rows


# --- eksport ---------------------------------------------------------------------------------------


def export_dataset(progress: ClassProgress, rows: list[StudentRow]):
    """``Dataset`` eksportu: **te same** kolumny, co tabela ekranu, w języku nauczyciela (D4, D8).

    Wartości są już napisami do czytania (stan konta, stan etapu), bo ``exports._cell`` zamienia
    prawdę/fałsz na polskie „tak/nie”, a nauczyciel IQO czyta plik po angielsku. Punkty zostają liczbą
    – w arkuszu mają się sumować. Bez adresów e-mail: tabela ich nie ma.
    """
    from django.utils.translation import gettext

    from apps.core.exports import Dataset

    header = [gettext("Imię i nazwisko"), gettext("Kod uczestnika"), gettext("Klasa"), gettext("Konto")]
    if progress.show_guardian:
        header.append(gettext("Zgoda rodzica"))
    if progress.show_certificate:
        header.append(gettext("Zaświadczenie o statusie ucznia"))
    for column in progress.columns:
        name = column.stage.display_name
        header += [
            f"{name}: " + gettext("zapis"),
            f"{name}: " + gettext("oddane prace"),
            f"{name}: " + gettext("ostatnie oddanie"),
            f"{name}: " + gettext("stan"),
            f"{name}: " + gettext("punkty"),
            f"{name}: " + gettext("kwalifikacja"),
        ]
    if progress.show_workshops:
        header.append(gettext("Warsztaty (obecności)"))

    def _rows():
        yes, no = gettext("tak"), gettext("nie")
        for row in rows:
            values = [row.name, row.participant.public_code, row.grade, str(row.account_label)]
            if progress.show_guardian:
                values.append(str(row.guardian_label))
            if progress.show_certificate:
                values.append(str(row.certificate_label))
            for column, cell in zip(progress.columns, row.cells, strict=True):
                values += [
                    yes if cell.entered else no,
                    f"{cell.submitted}/{cell.total}" if cell.entered and not cell.interview else "",
                    timezone.localtime(cell.last_at).date().isoformat() if cell.last_at else "",
                    str(cell.step_label) if cell.entered else "",
                    cell.points if column.published and cell.entered else None,
                    str(cell.decision_label) if column.published else "",
                ]
            if progress.show_workshops:
                values.append(row.workshops or 0)
            yield values

    return Dataset(
        header=header,
        rows=_rows(),
        count=len(rows),
        title=gettext("Postęp klasy"),
        filename="postep-klasy",
    )


# --- Start --------------------------------------------------------------------------------------


@dataclass
class StartSummary:
    progress: ClassProgress
    active: int = 0
    invited: int = 0
    missing_work: int = 0
    deadline: dict | None = None
    workshop: dict | None = None
    workshops_url: str = ""
    certificate_missing: int = 0
    certificate_pending: int = 0
    guardian_missing: int = 0


def start_summary(supervisor, competition, *, now=None) -> StartSummary:
    """Liczby do kafli „Start” – z tego samego :func:`class_progress`, co tabela (D4)."""
    from apps.cms.workshops import upcoming_workshops, workshops_page

    now = now or timezone.now()
    progress = class_progress(supervisor, competition, now=now)
    summary = StartSummary(progress=progress)
    for row in progress.rows:
        if row.account == STATE_INVITED:
            summary.invited += 1
        else:
            summary.active += 1
        summary.missing_work += int(row.missing_work)
        if row.certificate in (CERT_MISSING, CERT_REJECTED):
            summary.certificate_missing += 1
        elif row.certificate == CERT_PENDING:
            summary.certificate_pending += 1
        summary.guardian_missing += int(row.guardian == GUARDIAN_MISSING)
    summary.deadline = nearest_deadline(progress.columns, now)
    page = progress.workshops_page if progress.details else workshops_page(competition)
    upcoming = upcoming_workshops(page, now=now, limit=1)
    summary.workshop = upcoming[0] if upcoming else None
    summary.workshops_url = page.url if page is not None and summary.workshop else ""
    return summary


def nearest_deadline(columns: list[StageColumn], now) -> dict | None:
    """Najbliższy termin bieżącej edycji: oddanie prac w otwartym etapie albo start następnego.

    Etap z rozmową nie ma terminu oddania – liczy się tylko jego start. Trening jest już odfiltrowany
    w :func:`class_progress` (data-wartownik 2099 nie jest terminem).
    """
    candidates = []
    for column in columns:
        stage = column.stage
        if column.open_now:
            candidates.append((stage.deadline_at, "deadline", stage))
        elif stage.opens_at > now:
            candidates.append((stage.opens_at, "opens", stage))
    if not candidates:
        return None
    at, kind, stage = min(candidates, key=lambda item: item[0])
    return {"at": at, "kind": kind, "stage": stage}
