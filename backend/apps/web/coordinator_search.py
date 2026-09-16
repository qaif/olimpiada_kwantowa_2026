"""Wyszukiwarka panelu koordynatora: jedno pole na wszystko, co ma nazwisko, kod albo numer.

Po co: panel ma dziś ponad dwadzieścia ekranów, a najczęstsze pytanie koordynatora brzmi
„gdzie jest **ta jedna** osoba / to jedno zadanie”. Bez wspólnego pola droga do uczestnika wiodła
przez listę kont, do członka komisji – przez tę samą listę z innym filtrem, a do zadania – przez
etap. Tutaj wszystkie pięć rodzajów obiektów odpowiada na jedno zapytanie.

Trzy zasady, na których to stoi:

- **jedno zapytanie na grupę**. Każda grupa to jeden ``SELECT`` z ``select_related`` i twardym
  limitem; wyszukiwarka nie może być najdroższym ekranem w panelu,
- **adresy są niepewne**. Karty uczestnika, członka komisji i zadania powstają równolegle
  (osobne gałęzie pracy), więc każdy odnośnik ma **wariant zapasowy** – ekran edycji konta albo
  zadania. Rozstrzyga ``NoReverseMatch``, a nie nasza wiedza o tym, co już jest wdrożone,
- **wyszukiwarka niczego nie odsłania**. Wszystko, co tu widać, koordynator widzi i tak na
  swoich ekranach; bramką jest ``CoordinatorRequiredMixin`` na widoku, a nie ten moduł.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q

from apps.web.coordinator_nav import resolve

#: Ile wyników pokazujemy w jednej grupie. Wyszukiwarka ma **znaleźć**, a nie wylistować bazę:
#: przy dwudziestu trafieniach właściwą odpowiedzią jest doprecyzowanie frazy, a nie przewijanie.
GROUP_LIMIT = 20

#: Poniżej dwóch znaków zapytanie nie zawęża niczego – pojedyncza litera trafiłaby w połowę bazy.
MIN_QUERY_LENGTH = 2


@dataclass(frozen=True)
class Hit:
    """Jedno trafienie: podpis, wyjaśnienie „co to jest” i adres, pod który prowadzi."""

    label: str
    meta: str
    url: str


@dataclass(frozen=True)
class Group:
    """Grupa trafień jednego rodzaju. Grupa bez trafień nie trafia do wyniku."""

    key: str
    label: str
    hits: list[Hit]


def _full_name(user) -> str:
    """Imię i nazwisko konta albo sam adres – nie każde konto ma wypełnione oba pola."""
    name = f"{user.first_name} {user.last_name}".strip()
    return name or user.email


def _participants(query: str) -> list[Hit]:
    """Uczestnicy: kod publiczny, nazwisko, imię, adres e-mail i nazwa szkoły.

    Odnośnik prowadzi na kartę uczestnika, a gdy jej jeszcze nie ma – na ekran edycji konta,
    czyli tam, gdzie i tak kończy się większość telefonów („proszę poprawić literówkę”).
    """
    from apps.accounts.models import Participant

    rows = (
        Participant.objects.select_related("user")
        .filter(
            Q(public_code__icontains=query)
            | Q(user__last_name__icontains=query)
            | Q(user__first_name__icontains=query)
            | Q(user__email__icontains=query)
            | Q(school__icontains=query)
        )
        .order_by("user__last_name", "user__email")[:GROUP_LIMIT]
    )
    hits = []
    for participant in rows:
        url = resolve(("web:coordinator-participant",), (participant.pk,)) or resolve(
            ("web:coordinator-account-edit",), (participant.user_id,)
        )
        if url is None:  # pragma: no cover - oba adresy znikają dopiero razem z panelem
            continue
        hits.append(
            Hit(
                label=_full_name(participant.user),
                meta=f"{participant.public_code} · {participant.school} · {participant.user.email}",
                url=url,
            )
        )
    return hits


def _members(query: str) -> list[Hit]:
    """Członkowie komisji: nazwisko, imię, adres. Karta członka, a w zapasie edycja konta."""
    from apps.accounts.models import CommitteeMember

    rows = (
        CommitteeMember.objects.select_related("user")
        .filter(
            Q(user__last_name__icontains=query)
            | Q(user__first_name__icontains=query)
            | Q(user__email__icontains=query)
        )
        .order_by("user__last_name", "user__email")[:GROUP_LIMIT]
    )
    hits = []
    for member in rows:
        url = resolve(("web:coordinator-member",), (member.pk,)) or resolve(
            ("web:coordinator-account-edit",), (member.user_id,)
        )
        if url is None:  # pragma: no cover
            continue
        district = member.get_district_display() if member.district else "bez województwa"
        hits.append(
            Hit(
                label=_full_name(member.user),
                meta=f"{member.get_status_display()} · {district} · {member.user.email}",
                url=url,
            )
        )
    return hits


def _problems(query: str) -> list[Hit]:
    """Zadania: tytuł albo numer. Numer szukany wprost, bo „3” to najczęstsza fraza w tej grupie."""
    from apps.competitions.models import Problem

    condition = Q(title__icontains=query) | Q(title_en__icontains=query)
    if query.isdigit():
        condition |= Q(number=int(query))
    rows = (
        Problem.objects.select_related("stage", "stage__edition")
        .filter(condition)
        .order_by("-stage__opens_at", "number")[:GROUP_LIMIT]
    )
    hits = []
    for problem in rows:
        url = resolve(("web:coordinator-problem",), (problem.pk,)) or resolve(
            ("web:coordinator-problem-edit",), (problem.pk,)
        )
        if url is None:  # pragma: no cover
            continue
        hits.append(
            Hit(
                label=f"Zadanie {problem.number}: {problem.title}",
                meta=f"{problem.stage.display_name} · {problem.stage.edition.year_label}",
                url=url,
            )
        )
    return hits


def _stages(query: str) -> list[Hit]:
    """Etapy bieżącej edycji. Filtr jest w Pythonie, bo ``display_name`` jest właściwością.

    Nazwa etapu bywa pusta i wtedy podpisem jest etykieta rodzaju („Etap I – eliminacje”) –
    a tej w bazie nie ma. Zapytanie jest jedno (etapy edycji), rekordów kilka.
    """
    from apps.web.coordinator_nav import current_stages

    needle = query.casefold()
    hits = []
    for stage in current_stages():
        if needle not in stage.display_name.casefold():
            continue
        url = resolve(("web:coordinator-stage-edit",), (stage.pk,))
        if url is None:  # pragma: no cover
            continue
        state = "zamknięty" if stage.closed_at else "otwarty"
        hits.append(Hit(label=stage.display_name, meta=f"{state} · {stage.get_format_display()}", url=url))
    return hits[:GROUP_LIMIT]


def _issues(query: str) -> list[Hit]:
    """Zgłoszenia recenzentów – wyłącznie po numerze.

    Po treści szukać nie ma po czym: opis zgłoszenia jest zdaniem o jednej pracy, a nie polem
    katalogowym. Numer bierze się z rozmowy („piszę w sprawie zgłoszenia 42”) i to on prowadzi
    do kolejki, w której sprawa stoi.
    """
    if not query.isdigit():
        return []
    from apps.grading.models import WorkIssue

    rows = (
        WorkIssue.objects.select_related("submission", "submission__entry__participant", "review")
        .filter(pk=int(query))
        .order_by("-created_at")[:GROUP_LIMIT]
    )
    base = resolve(("web:coordinator-issues",))
    if base is None:  # pragma: no cover
        return []
    hits = []
    for issue in rows:
        code = getattr(issue.submission.entry.participant, "public_code", "—")
        hits.append(
            Hit(
                label=f"Zgłoszenie {issue.pk}: {issue.get_kind_display()}",
                meta=f"{issue.get_status_display()} · praca {code}",
                url=f"{base}#zgloszenie-{issue.pk}",
            )
        )
    return hits


#: Kolejność grup na ekranie wyników. Uczestnicy na górze, bo to o nich są telefony.
GROUPS = (
    ("participants", "Uczestnicy", _participants),
    ("members", "Członkowie komisji", _members),
    ("problems", "Zadania", _problems),
    ("stages", "Etapy", _stages),
    ("issues", "Zgłoszenia", _issues),
)


def search(raw_query: str) -> list[Group]:
    """Wyniki dla frazy, pogrupowane. Fraza krótsza niż dwa znaki daje pustą listę grup."""
    query = (raw_query or "").strip()
    if len(query) < MIN_QUERY_LENGTH:
        return []
    groups = []
    for key, label, finder in GROUPS:
        hits = finder(query)
        if hits:
            groups.append(Group(key=key, label=label, hits=hits))
    return groups
