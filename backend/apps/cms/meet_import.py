"""Import obecności na warsztatach z raportu Google Meet: dopasowanie, podgląd i zapis (WS-ATT-02).

Raport (``apps.cms.meet_report``) mówi, kto był na spotkaniu – imieniem, nazwiskiem i adresem konta
Google. Tabela obecności zna wyłącznie **uczestników tego konkursu**. Ten moduł jest mostem między
jednym a drugim i ma trzy zasady:

- **dopasowanie musi być jednoznaczne albo nie ma go wcale.** E-mail, potem imię i nazwisko; dwóch
  kandydatów to pytanie do koordynatora, a nie wybór „pierwszego z brzegu”. Zaświadczenie stoi na
  tej obecności, więc pomyłka jest dokumentem wystawionym niewłaściwej osobie,
- **krąg kandydatów jest ten sam, co wiersze tabeli obecności** – uczestnicy tego konkursu z wpisem
  do bieżącej edycji, bez kont usuniętych. Uczestnik innego konkursu z tym samym adresem nie istnieje
  dla tego importu,
- **z raportu nie zostaje nic poza wynikiem.** Podgląd pokazuje nazwiska i adresy z raportu tylko na
  stronie odpowiedzi; do zatwierdzenia jedzie podpisany token z samymi identyfikatorami, a do bazy
  trafia para „uczestnik – warsztat” (``apps.cms.workshops.add_attendance``) i liczby w audycie.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from django.core import signing

from .meet_report import MeetReport, MeetReportError, format_duration, name_key, normalize_email

#: Domyślny próg obecności w minutach. Bezwzględny, a nie procent czasu zajęć – uzasadnienie
#: w WS-ATT-02 § 3.3: harmonogram podaje godziny tekstem, a raport nie podaje długości spotkania.
DEFAULT_THRESHOLD_MINUTES = 30
MAX_THRESHOLD_MINUTES = 600

#: Token podglądu: sól własna (podpis z innego miejsca serwisu tu nie przejdzie) i czas życia.
#: Pół godziny wystarcza na przejrzenie listy kilkuset osób i wybranie niejednoznacznych, a nie
#: pozwala zatwierdzić podglądu sprzed tygodnia, gdy krąg uczestników jest już inny.
PREVIEW_SALT = "cms.meet_import.preview"
PREVIEW_MAX_AGE = 30 * 60
TOKEN_VERSION = 1

SOURCE_FILE = "file"
SOURCE_LINK = "link"
SOURCES = (SOURCE_FILE, SOURCE_LINK)

VIA_EMAIL = "email"
VIA_NAME = "name"


def candidate_participants(competition, edition):
    """Krąg kandydatów: uczestnicy konkursu z wpisem do etapu bieżącej edycji, bez kont usuniętych.

    To jest ten sam krąg, co wiersze tabeli obecności i odbiorcy zaświadczeń
    (``apps.web.views.coordinator_workshops._participants``) – obecność osoby spoza niego nie
    miałaby gdzie się pokazać ani na czym przypiąć dokumentu.
    """
    from apps.accounts.anonymised import anonymised_q
    from apps.accounts.models import Participant

    if edition is None:
        return Participant.objects.none()
    return (
        Participant.objects.for_competition(competition)
        .filter(stage_entries__stage__edition=edition)
        .exclude(anonymised_q("user"))
        .select_related("user")
        .distinct()
    )


def teacher_emails(competition) -> set[str]:
    """Znormalizowane adresy opiekunów szkolnych tego konkursu – „nauczyciel – pominięty”."""
    from apps.accounts.models import SchoolSupervisor

    return {
        normalize_email(email)
        for email in SchoolSupervisor.objects.for_competition(competition).values_list(
            "user__email", flat=True
        )
        if email
    }


@dataclass
class PreviewEntry:
    """Wiersz podglądu: osoba z raportu (po zsumowaniu) i jej rozstrzygnięcie."""

    label: str
    email: str
    seconds: int
    unreadable_time: bool = False
    via: str = ""
    participant: object | None = None
    candidates: list = field(default_factory=list)
    #: Numer pola wyboru (niejednoznaczni) albo pola kodu (nieznalezieni) w formularzu zatwierdzenia.
    index: int | None = None

    @property
    def duration(self) -> str:
        return format_duration(self.seconds)


@dataclass
class MeetPreview:
    workshop: dict
    source: str
    threshold_minutes: int
    report_rows: int
    people: int
    meeting_date: object = None
    #: Warsztat wybrany po dacie z raportu (koordynator zostawił „rozpoznaj po dacie”).
    workshop_from_date: bool = False
    #: Data z raportu jest inna niż data wybranego warsztatu – ostrzeżenie w podglądzie.
    date_mismatch: bool = False
    longest_seconds: int = 0
    matched: list[PreviewEntry] = field(default_factory=list)
    below: list[PreviewEntry] = field(default_factory=list)
    ambiguous: list[PreviewEntry] = field(default_factory=list)
    unknown: list[PreviewEntry] = field(default_factory=list)
    teachers: list[PreviewEntry] = field(default_factory=list)
    already: list[PreviewEntry] = field(default_factory=list)
    token: str = ""

    @property
    def longest(self) -> str:
        return format_duration(self.longest_seconds)

    def counts(self) -> dict[str, int]:
        return {
            "report_rows": self.report_rows,
            "people": self.people,
            "matched": len(self.matched),
            "below": len(self.below),
            "ambiguous": len(self.ambiguous),
            "unknown": len(self.unknown),
            "teachers": len(self.teachers),
            "already": len(self.already),
        }


def suggest_workshop(workshops: list[dict], meeting_date) -> dict | None:
    """Warsztat z tą samą datą co spotkanie – wyłącznie gdy jest **dokładnie jeden** (WS-ATT-02 § 3.2)."""
    if meeting_date is None:
        return None
    same_day = [row for row in workshops if row.get("date_value") == meeting_date]
    return same_day[0] if len(same_day) == 1 else None


def build_preview(
    report: MeetReport,
    *,
    competition,
    edition,
    workshop: dict,
    threshold_minutes: int,
    source: str,
    user,
    workshop_from_date: bool = False,
) -> MeetPreview:
    """Dopasowanie osób z raportu do uczestników i podział na sekcje podglądu (WS-ATT-02 § 4).

    Nic nie zapisuje. Kolejność reguł: e-mail uczestnika → e-mail opiekuna szkolnego → imię
    i nazwisko wśród uczestników **niedopasowanych jeszcze po e-mailu**. Ta ostatnia część chroni
    przed fałszywą niejednoznacznością: dwóch Janów Kowalskich w konkursie, jeden przyszedł ze
    znanym adresem, drugi z prywatnym – drugi ma już tylko jednego kandydata.
    """
    from .models import WorkshopAttendance

    threshold_seconds = int(threshold_minutes) * 60
    participants = list(candidate_participants(competition, edition))
    by_email: dict[str, list] = defaultdict(list)
    by_name: dict[str, list] = defaultdict(list)
    for participant in participants:
        if participant.user.email:
            by_email[normalize_email(participant.user.email)].append(participant)
        key = name_key(participant.user.first_name, participant.user.last_name)
        if key:
            by_name[key].append(participant)
    teachers = teacher_emails(competition)

    assigned: dict[int, PreviewEntry] = {}
    ambiguous: list[PreviewEntry] = []
    unknown: list[PreviewEntry] = []
    teacher_rows: list[PreviewEntry] = []
    pending = []

    def entry_for(person) -> PreviewEntry:
        return PreviewEntry(
            label=person.label,
            email=person.email,
            seconds=person.seconds,
            unreadable_time=person.unreadable_time,
        )

    def assign(entry: PreviewEntry, participant, via: str) -> None:
        current = assigned.get(participant.pk)
        if current is None:
            entry.participant, entry.via = participant, via
            assigned[participant.pk] = entry
            return
        # Druga osoba z raportu wskazała tego samego uczestnika (telefon i laptop, dwa konta Google):
        # czasy się sumują, a w podglądzie widać obie nazwy.
        current.seconds += entry.seconds
        current.unreadable_time = current.unreadable_time or entry.unreadable_time
        if entry.label not in current.label:
            current.label = f"{current.label} / {entry.label}"
        if entry.email and entry.email not in current.email:
            current.email = f"{current.email} / {entry.email}" if current.email else entry.email
        if via == VIA_EMAIL:
            current.via = VIA_EMAIL

    for person in report.people:
        entry = entry_for(person)
        found = by_email.get(normalize_email(person.email), []) if person.email else []
        if len(found) == 1:
            assign(entry, found[0], VIA_EMAIL)
        elif len(found) > 1:
            entry.candidates = found
            ambiguous.append(entry)
        else:
            pending.append((person, entry))

    taken = set(assigned)
    for person, entry in pending:
        if person.email and normalize_email(person.email) in teachers:
            teacher_rows.append(entry)
            continue
        key = name_key(person.first_name, person.last_name, person.full_name)
        found = (
            [participant for participant in by_name.get(key, []) if participant.pk not in taken]
            if key
            else []
        )
        if len(found) == 1:
            assign(entry, found[0], VIA_NAME)
        elif len(found) > 1:
            entry.candidates = found
            ambiguous.append(entry)
        else:
            unknown.append(entry)

    preview = MeetPreview(
        workshop=workshop,
        source=source,
        threshold_minutes=int(threshold_minutes),
        report_rows=report.rows,
        people=len(report.people),
        meeting_date=report.meeting_date,
        workshop_from_date=workshop_from_date,
        date_mismatch=bool(report.meeting_date and report.meeting_date != workshop.get("date_value")),
        longest_seconds=report.longest_seconds,
        teachers=teacher_rows,
    )
    existing = set(
        WorkshopAttendance.objects.filter(
            participant_id__in=list(assigned), workshop_key=workshop["key"]
        ).values_list("participant_id", flat=True)
    )
    for participant_id, entry in assigned.items():
        if participant_id in existing:
            preview.already.append(entry)
        elif entry.seconds >= threshold_seconds:
            preview.matched.append(entry)
        else:
            preview.below.append(entry)
    for entry in ambiguous:
        (preview.ambiguous if entry.seconds >= threshold_seconds else preview.below).append(entry)
    for entry in unknown:
        (preview.unknown if entry.seconds >= threshold_seconds else preview.below).append(entry)
    for bucket in (
        preview.matched,
        preview.below,
        preview.ambiguous,
        preview.unknown,
        preview.teachers,
        preview.already,
    ):
        bucket.sort(key=lambda row: row.label.casefold())
    for index, entry in enumerate(preview.ambiguous):
        entry.index = index
    for index, entry in enumerate(preview.unknown):
        entry.index = index
    preview.token = pack_preview(preview, competition=competition, edition=edition, user=user)
    return preview


def pack_preview(preview: MeetPreview, *, competition, edition, user) -> str:
    """Token zatwierdzenia: **same identyfikatory i liczby**, bez niczego z raportu (WS-ATT-02 § 5)."""
    payload = {
        "v": TOKEN_VERSION,
        "c": competition.pk,
        "u": user.pk,
        "e": getattr(edition, "pk", None),
        "k": preview.workshop["key"],
        "s": preview.source,
        "t": preview.threshold_minutes,
        "m": sorted(entry.participant.pk for entry in preview.matched),
        "b": sorted(entry.participant.pk for entry in preview.below if entry.participant is not None),
        "a": [[candidate.pk for candidate in entry.candidates] for entry in preview.ambiguous],
        "n": len(preview.unknown),
        "x": preview.counts(),
    }
    return signing.dumps(payload, salt=PREVIEW_SALT, compress=True)


def unpack_preview(token: str, *, competition, user) -> dict:
    """Token → ładunek; odmowa, gdy podpis zły, token stary albo z innego konkursu lub konta."""
    try:
        payload = signing.loads(token or "", salt=PREVIEW_SALT, max_age=PREVIEW_MAX_AGE)
    except signing.BadSignature as exc:  # obejmuje ``SignatureExpired``
        raise MeetReportError(
            "Podgląd importu wygasł albo jest nieczytelny. Wczytaj raport jeszcze raz."
        ) from exc
    if (
        not isinstance(payload, dict)
        or payload.get("v") != TOKEN_VERSION
        or payload.get("c") != competition.pk
        or payload.get("u") != getattr(user, "pk", None)
        or payload.get("s") not in SOURCES
    ):
        # Token z innego konkursu albo od innego koordynatora: podpis jest ważny, ale nie dla tego
        # żądania. Ten sam komunikat, co przy złym podpisie – rozróżnienie nic tu nikomu nie daje.
        raise MeetReportError("Podgląd importu wygasł albo jest nieczytelny. Wczytaj raport jeszcze raz.")
    return payload


def _ids(values) -> set[int]:
    return {int(value) for value in values if str(value).isdigit()}


def commit_preview(post, *, competition, edition, workshops: list[dict], user, request=None) -> dict:
    """Zatwierdzenie podglądu: dokłada obecności wybranym osobom i zapisuje audyt (WS-ATT-02 § 3.5, § 6.4).

    Wszystko, co przychodzi w formularzu, jest zawężane do tego, co **serwer** pokazał w podglądzie:
    zaznaczeni – do list z tokenu, wybór niejednoznacznego – do kandydatów tego wiersza, kod wpisany
    ręcznie – do kręgu kandydatów. Ręcznie zbudowany POST nie dopisze więc obecności nikomu spoza
    konkursu ani spoza edycji.
    """
    from apps.accounts.models import CompetitionRole
    from apps.accounts.services import has_role
    from apps.core.models import audit

    from .workshops import add_attendance

    # Rola także tutaj, nie tylko w widoku: serwis zapisuje oświadczenie organizatora.
    if not has_role(user, competition, CompetitionRole.COORDINATOR):
        raise MeetReportError("Import obecności jest dostępny wyłącznie dla koordynatora.")
    payload = unpack_preview(post.get("token"), competition=competition, user=user)
    if payload.get("e") != getattr(edition, "pk", None):
        raise MeetReportError("Bieżąca edycja zmieniła się od podglądu. Wczytaj raport jeszcze raz.")
    workshop = next((row for row in workshops if row["key"] == payload.get("k")), None)
    if workshop is None:
        raise MeetReportError("Tego warsztatu nie ma już w harmonogramie. Wczytaj raport jeszcze raz.")

    chosen = _ids(post.getlist("attend")) & set(payload.get("m") or [])
    chosen |= _ids(post.getlist("below")) & set(payload.get("b") or [])
    manual = 0
    for index, candidates in enumerate(payload.get("a") or []):
        value = post.get(f"choice_{index}") or ""
        if value.isdigit() and int(value) in set(candidates):
            chosen.add(int(value))
            manual += 1
    codes = {}
    for index in range(int(payload.get("n") or 0)):
        code = (post.get(f"code_{index}") or "").strip().upper()
        if code:
            codes[index] = code[:32]
    bad_codes: list[str] = []
    if codes:
        found = {
            participant.public_code.upper(): participant.pk
            for participant in candidate_participants(competition, edition).filter(
                public_code__in=set(codes.values())
            )
        }
        for code in codes.values():
            if code in found:
                chosen.add(found[code])
                manual += 1
            elif code not in bad_codes:
                bad_codes.append(code)
    # Kandydaci z tokenu są sprzed (do) pół godziny – uczestnik mógł w tym czasie usunąć konto.
    # Zapisujemy wyłącznie tym, którzy nadal są w kręgu.
    allowed = set(
        candidate_participants(competition, edition).filter(pk__in=chosen).values_list("pk", flat=True)
    )
    added = add_attendance(allowed, workshop["key"], actor=user)
    counts = dict(payload.get("x") or {})
    audit(
        user,
        "workshop.attendance_imported",
        # Wpis przy edycji, jak zapis tabeli obecności. Liczby zamiast listy osób: audyt mówi, co się
        # stało, a nie jest drugą kopią raportu (WS-ATT-02 § 6.4).
        edition if edition is not None else user,
        {
            "workshop": workshop["key"],
            "source": payload.get("s"),
            "threshold_minutes": payload.get("t"),
            **{key: int(value) for key, value in counts.items() if isinstance(value, int)},
            "selected": len(allowed),
            "manual": manual,
            "bad_codes": len(bad_codes),
            "added": added,
        },
        request=request,
    )
    return {
        "added": added,
        "selected": len(allowed),
        "manual": manual,
        "bad_codes": bad_codes,
        "workshop": workshop,
    }
