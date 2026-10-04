"""Zestawienia oficera logistyki: przyloty, wyżywienie, koszulki, kompletność, przypomnienia, CSV.

Wszystko liczone w Pythonie na członkach jednej edycji (kilkaset wierszy): pola szyfrowane nie
dają się grupować w SQL-u, a zestawienie złożone z dwóch dróg (część w bazie, część w pamięci)
rozjeżdżałoby się przy pierwszej zmianie.

Eksporty CSV idą przez ``apps.core.exports`` (BOM, średnik, apostrof przed „formułą” w nazwisku).
Nagłówki po polsku – plik otwiera organizator, a jego ekrany są po polsku (I18N-01 § 0).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import time

from django.template.loader import render_to_string
from django.urls import reverse

from apps.core.models import audit

from .models import Diet, FieldGroup, LogisticsReminder, MemberKind, TravelMode, TshirtSize
from .services import edition_members, event_for, group_deadline, groups_for, missing_groups

REMINDER_SUBJECT_TEMPLATE = "delegation_logistics/email/reminder_subject.txt"
REMINDER_BODY_TEMPLATE = "delegation_logistics/email/reminder_body.txt"


# --- przyloty i odloty ---------------------------------------------------------------------------


def travel_board(members, *, direction: str = "arrival") -> dict:
    """Tablica przyjazdów (albo wyjazdów): dzień → grupy osób tym samym lotem/pociągiem.

    Grupą jest (godzina, miejsce, środek, numer) – to jest jeden odbiór z lotniska: kierowca
    czeka na lot LH1234 o 14:05 na WAW, a nie na sześć osób osobno. Osoby bez daty trafiają
    na koniec jako „bez danych”, bo to one są pracą na dziś.
    """
    prefix = "arrival" if direction == "arrival" else "departure"
    groups: dict = defaultdict(list)
    undated = []
    for member in members:
        day = getattr(member, f"{prefix}_date")
        if day is None:
            undated.append(member)
            continue
        key = (
            day,
            getattr(member, f"{prefix}_time"),
            getattr(member, f"{prefix}_place") or "",
            getattr(member, f"{prefix}_mode") or "",
            (getattr(member, f"{prefix}_number") or "").upper(),
        )
        groups[key].append(member)
    days: dict = defaultdict(list)
    for (day, moment, place, mode, number), people in groups.items():
        days[day].append(
            {
                "time": moment,
                "place": place,
                "mode": TravelMode(mode).label if mode in TravelMode.values else "",
                "number": number,
                "people": people,
                "countries": sorted({person.delegation.country.name for person in people}),
            }
        )
    board = []
    for day in sorted(days):
        slots = sorted(days[day], key=lambda slot: (slot["time"] is None, slot["time"] or time.min))
        board.append({"day": day, "slots": slots, "count": sum(len(slot["people"]) for slot in slots)})
    return {"days": board, "undated": undated}


# --- wyżywienie i koszulki ---------------------------------------------------------------------------


def dietary_summary(members) -> dict:
    """Liczby diet i lista osób z alergią albo uwagą – materiał dla kuchni."""
    counts = Counter(member.diet or "" for member in members)
    rows = [
        member
        for member in members
        if member.allergies or member.diet_notes or member.medical_notes or member.diet not in ("", Diet.NONE)
    ]
    return {
        "counts": [(Diet(value).label, counts.get(value, 0)) for value in Diet.values],
        "unknown": counts.get("", 0),
        "rows": rows,
    }


def tshirt_summary(members) -> dict:
    """Koszulki: rozmiar × rola (uczniowie, opiekunowie, goście) i suma – zamówienie u drukarza."""
    table = {size: Counter() for size in TshirtSize.values}
    unknown = Counter()
    for member in members:
        if member.tshirt_size in table:
            table[member.tshirt_size][member.kind] += 1
        else:
            unknown[member.kind] += 1
    kinds = list(MemberKind.values)
    rows = [
        {
            "size": TshirtSize(size).label,
            "by_kind": [table[size].get(kind, 0) for kind in kinds],
            "total": sum(table[size].values()),
        }
        for size in TshirtSize.values
    ]
    return {
        "kinds": [MemberKind(kind).label for kind in kinds],
        "rows": rows,
        "unknown": [unknown.get(kind, 0) for kind in kinds],
        "unknown_total": sum(unknown.values()),
        "total": sum(row["total"] for row in rows),
    }


# --- kompletność i przypomnienia ------------------------------------------------------------------------


def completeness(competition, edition, members) -> list[dict]:
    """Per delegacja: liczba osób i ile osób ma braki w każdej grupie."""
    groups = groups_for(competition)
    by_delegation: dict = {}
    for member in members:
        row = by_delegation.setdefault(
            member.delegation_id,
            {"delegation": member.delegation, "members": 0, "missing": Counter(), "complete": 0},
        )
        row["members"] += 1
        missing = missing_groups(member, groups)
        row["missing"].update(missing)
        if not missing:
            row["complete"] += 1
    rows = sorted(by_delegation.values(), key=lambda row: row["delegation"].country.name.lower())
    for row in rows:
        row["by_group"] = [row["missing"].get(group, 0) for group in groups]
    return rows


def _missing_lines(members, groups) -> list[dict]:
    lines = []
    for member in members:
        missing = missing_groups(member, groups)
        if missing:
            # Etykiety leniwe – tłumaczy je dopiero szablon listu, w języku odbiorcy (``language_for``).
            lines.append({"name": member.full_name, "groups": [FieldGroup(group).label for group in missing]})
    return lines


def send_reminders(competition, edition, *, delegations=None, actor, request=None) -> int:
    """E-maile do opiekunów delegacji z brakami – każdy w języku **odbiorcy** (``language_for``).

    List wymienia osoby i sekcje z brakami oraz terminy sekcji; danych samych w sobie nie przepisuje
    (żadnego numeru paszportu w poczcie). Zwraca liczbę wysłanych listów. Delegacje bez braków są
    pomijane – przypomnienie o niczym uczy ignorowania przypomnień.
    """
    from apps.accounts.activation import absolute_url, queue_mail
    from apps.accounts.delegations import DelegationLeader
    from apps.accounts.preferences import language_for
    from apps.tenancy import branding

    groups = groups_for(competition)
    event = event_for(edition)
    members = edition_members(edition)
    by_delegation: dict = defaultdict(list)
    for member in members:
        by_delegation[member.delegation_id].append(member)
    wanted = {delegation.pk for delegation in delegations} if delegations is not None else None
    link = absolute_url(reverse("web:delegation-logistics"), request, competition)
    sent = 0
    for delegation_id, people in by_delegation.items():
        if wanted is not None and delegation_id not in wanted:
            continue
        lines = _missing_lines(people, groups)
        if not lines:
            continue
        delegation = people[0].delegation
        leaders = list(DelegationLeader.objects.active().filter(delegation=delegation).select_related("user"))
        for leader in leaders:
            with language_for(leader.user, competition):
                deadlines = [
                    {"group": str(FieldGroup(group).label), "deadline": group_deadline(event, group)}
                    for group in groups
                    if group_deadline(event, group) is not None
                ]
                context = {
                    "first_name": leader.user.first_name,
                    "country": delegation.country.name,
                    "lines": lines,
                    "deadlines": deadlines,
                    "link": link,
                    "brand": branding.brand_names(competition),
                }
                subject = render_to_string(REMINDER_SUBJECT_TEMPLATE, context).strip().replace("\n", " ")
                body = render_to_string(REMINDER_BODY_TEMPLATE, context)
            queue_mail(subject, body, leader.user.email, competition=competition)
            sent += 1
        reminder = LogisticsReminder.objects.create(
            delegation=delegation,
            sent_by=actor if getattr(actor, "is_authenticated", False) else None,
            missing_count=len(lines),
            recipients=len(leaders),
        )
        audit(
            actor,
            "logistics.reminder_sent",
            reminder,
            {"delegation": delegation.pk, "missing": len(lines), "recipients": len(leaders)},
            request=request,
        )
    return sent


def last_reminders(edition) -> dict[int, LogisticsReminder]:
    rows = LogisticsReminder.objects.filter(delegation__edition=edition).order_by("delegation_id", "-sent_at")
    latest: dict[int, LogisticsReminder] = {}
    for row in rows:
        latest.setdefault(row.delegation_id, row)
    return latest


# --- eksporty CSV ------------------------------------------------------------------------------------


def _date(value) -> str:
    return value.isoformat() if value else ""


def _time(value) -> str:
    return value.strftime("%H:%M") if value else ""


def _base(member) -> list:
    return [member.delegation.country.name, member.role_label, member.first_name, member.last_name]


BASE_HEADER = ["kraj", "rola", "imię", "nazwisko"]


def dataset(kind: str, competition, edition, members):
    """Eksport jednego zestawienia – ``kind`` z :data:`EXPORT_KINDS`."""
    from apps.core.exports import Dataset

    builder = EXPORT_KINDS[kind]
    header, rows, title = builder(competition, edition, members)
    return Dataset(
        header=header, rows=iter(rows), count=len(rows), title=title, filename=f"logistyka-finalu-{kind}"
    )


def _travel(competition, edition, members):
    header = [
        *BASE_HEADER,
        "przyjazd – dzień",
        "przyjazd – godzina",
        "przyjazd – środek",
        "przyjazd – numer",
        "przyjazd – miejsce",
        "wyjazd – dzień",
        "wyjazd – godzina",
        "wyjazd – środek",
        "wyjazd – numer",
        "wyjazd – miejsce",
    ]
    rows = [
        [
            *_base(m),
            _date(m.arrival_date),
            _time(m.arrival_time),
            m.get_arrival_mode_display() if m.arrival_mode else "",
            m.arrival_number,
            m.arrival_place,
            _date(m.departure_date),
            _time(m.departure_time),
            m.get_departure_mode_display() if m.departure_mode else "",
            m.departure_number,
            m.departure_place,
        ]
        for m in members
    ]
    return header, rows, "Przyjazdy i wyjazdy"


def _rooming(competition, edition, members):
    from .rooming import reference_day

    day = reference_day(edition)
    header = [*BASE_HEADER, "budynek", "pokój", "płeć", "niepełnoletni", "nocleg", "preferencja", "uwagi"]
    ordered = sorted(members, key=lambda m: (m.room is None, str(m.room or ""), m.last_name))
    rows = [
        [
            *_base(m),
            m.room.building if m.room else "",
            m.room.name if m.room else "",
            m.gender,
            "tak" if m.is_minor_on(day) else "nie",
            "tak" if m.needs_accommodation else "nie",
            m.roommate_preference,
            m.accommodation_notes,
        ]
        for m in ordered
    ]
    return header, rows, "Lista pokoi"


def _dietary(competition, edition, members):
    header = [*BASE_HEADER, "dieta", "uwagi do diety", "alergie"]
    rows = [
        [*_base(m), m.get_diet_display() if m.diet else "", m.diet_notes, m.allergies]
        for m in members
        if m.diet or m.diet_notes or m.allergies
    ]
    return header, rows, "Wyżywienie"


def _tshirts(competition, edition, members):
    header = [*BASE_HEADER, "rozmiar koszulki"]
    rows = [[*_base(m), m.get_tshirt_size_display() if m.tshirt_size else ""] for m in members]
    return header, rows, "Koszulki"


def _full(competition, edition, members):
    """Pełny eksport – z danymi paszportowymi i zdrowia. Wyłącznie oficer, każdy eksport w audycie."""
    groups = groups_for(competition)
    header = [
        *BASE_HEADER,
        "e-mail",
        "imię i nazwisko z paszportu",
        "obywatelstwo",
        "data urodzenia",
        "numer paszportu",
        "paszport ważny do",
        "rozmiar koszulki",
        "kontakt alarmowy",
        "telefon alarmowy",
        "braki",
    ]
    health = FieldGroup.HEALTH in groups
    if health:
        header += ["dieta", "uwagi do diety", "alergie", "uwagi medyczne"]
    rows = []
    for m in members:
        user = m.account_user
        email = user.email if user is not None else (m.guest.email if m.guest_id else "")
        row = [
            *_base(m),
            email,
            m.passport_name,
            m.nationality,
            m.date_of_birth,
            m.passport_number,
            m.passport_expiry,
            m.get_tshirt_size_display() if m.tshirt_size else "",
            m.emergency_name,
            m.emergency_phone,
            ", ".join(str(FieldGroup(g).label) for g in missing_groups(m, groups)),
        ]
        if health:
            row += [m.get_diet_display() if m.diet else "", m.diet_notes, m.allergies, m.medical_notes]
        rows.append(row)
    return header, rows, "Logistyka finału – pełne dane"


EXPORT_KINDS = {
    "travel": _travel,
    "rooming": _rooming,
    "dietary": _dietary,
    "tshirts": _tshirts,
    "full": _full,
}
