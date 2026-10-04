"""Lista pokoi finału: pokoje, przydziały i zasady zakwaterowania niepełnoletnich (LOG-01 § 4).

Zasady są **twarde** (serwis odmawia), a nie podpowiedzią na ekranie, bo dotyczą bezpieczeństwa
dzieci – a lista pokoi bywa edytowana w pośpiechu, w nocy przed przyjazdem:

1. pojemność pokoju nie może być przekroczona,
2. pokój ma płeć (kobiety / mężczyźni) albo jest „dowolny” – i ten ostatni jest dla dorosłych,
3. **niepełnoletni nigdy nie śpi w jednym pokoju z dorosłym** – także z opiekunem własnej drużyny.
   Pokój opiekuna i pokój uczniów są osobne; jeśli regulamin konkretnego finału przewiduje wyjątek
   (rodzic z dzieckiem), organizator rozwiązuje go poza systemem, a nie przez obejście reguły,
4. niepełnoletni z płcią „inna / wolę nie podawać” (albo bez płci) dostaje **pokój jednoosobowy**:
   wolno go położyć w dowolnym pokoju, w którym nikogo nie ma, i nikt nie dołącza do niego. Bez tej
   reguły taka osoba nie miałaby żadnego dozwolonego miejsca (poprawka po przeglądzie, L6).

Wiek liczymy na **pierwszy dzień finału** (``FinalEvent.starts_on``), bo to w tym dniu dziecko
przyjeżdża; bez daty finału – na dziś. Brak daty urodzenia ucznia = niepełnoletni
(``DelegationMember.is_minor_on`` – odwrót ostrożny).

**Zasady sprawdzane także po fakcie** (poprawka po przeglądzie, H2). Przydział zgodny w chwili
zapisu przestaje być zgodny, gdy zmienią się dane osoby (płeć, data urodzenia, „bez noclegu”) albo
data finału (urodziny przed/po pierwszym dniu). Dwie odpowiedzi, każda z powodem:

- **zmiana danych osoby** → przydział jest zdejmowany (``services.save_member`` woła
  :func:`recheck_member`) z wpisem audytu i komunikatem: to ta osoba przestała pasować do pokoju,
  a zostawienie jej tam choćby do rana jest dokładnie tym, czego reguła ma nie dopuścić,
- **zmiana daty finału** → naruszenia są **oznaczane** (:func:`room_problems` – ekran pokoi
  i kolumna CSV) i liczone w komunikacie zapisu ustawień, ale nikt nie jest wyprowadzany sam:
  jedna poprawiona data mogłaby opróżnić kilkanaście pokoi naraz, a decyzja „kogo przenieść” należy
  do oficera.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils import timezone
from rest_framework import status

from apps.core.api import DomainError
from apps.core.models import audit

from .models import DelegationMember, Gender, Room, RoomGender
from .services import event_for

BINARY_GENDERS = (Gender.FEMALE, Gender.MALE)


def rooms_of(edition):
    return Room.objects.filter(edition=edition).order_by("building", "name", "id")


def room_for(competition, pk: int, edition=None) -> Room:
    rooms = Room.objects.for_competition(competition)
    if edition is not None:
        rooms = rooms.filter(edition=edition)
    room = rooms.filter(pk=pk).first()
    if room is None:
        raise Http404("Nie ma takiego pokoju.")
    return room


def reference_day(edition):
    event = event_for(edition)
    return event.starts_on if event is not None and event.starts_on else timezone.localdate()


def create_room(
    competition, edition, *, name, building="", capacity, gender, note="", actor, request=None
) -> Room:
    if gender not in RoomGender.values:
        raise DomainError("Wybierz płeć pokoju.", "ROOM_GENDER", status.HTTP_400_BAD_REQUEST)
    try:
        with transaction.atomic():
            room = Room.objects.create(
                competition=competition,
                edition=edition,
                name=(name or "").strip(),
                building=(building or "").strip(),
                capacity=capacity,
                gender=gender,
                note=(note or "").strip(),
            )
    except IntegrityError as exc:
        raise DomainError(
            "Pokój o tej nazwie w tym budynku już istnieje.", "ROOM_DUPLICATE", status.HTTP_400_BAD_REQUEST
        ) from exc
    audit(actor, "logistics.room_created", room, {"capacity": capacity, "gender": gender}, request=request)
    return room


@transaction.atomic
def delete_room(room: Room, *, actor, request=None) -> None:
    """Usuwa pokój; osoby z niego wracają do „nieprzydzielonych” (``SET_NULL``)."""
    audit(actor, "logistics.room_deleted", room, {"occupants": room.occupants.count()}, request=request)
    room.delete()


def _single_minor(member: DelegationMember, minor: bool) -> bool:
    """Niepełnoletni bez płci binarnej – mieszka sam (reguła 4)."""
    return minor and member.gender not in BINARY_GENDERS


def _person_fits(room: Room, member: DelegationMember, minor: bool, others_count: int) -> str:
    """Czy ta osoba może mieszkać w tym pokoju niezależnie od współlokatorów (płeć i wiek pokoju)."""
    if _single_minor(member, minor):
        return "" if others_count == 0 else "Niepełnoletni bez podanej płci mieszka w pokoju jednoosobowo."
    if room.gender == RoomGender.ANY:
        return "" if not minor else "Pokój „dowolna płeć” jest wyłącznie dla dorosłych."
    if member.gender not in BINARY_GENDERS:
        return (
            "Osoba nie ma podanej płci – przydziel ją do pokoju „dowolna płeć” (dorośli) albo uzupełnij dane."
        )
    if member.gender != room.gender:
        return "Płeć osoby nie zgadza się z płcią pokoju."
    return ""


def check_assignment(room: Room, member: DelegationMember, occupants: list[DelegationMember], day) -> str:
    """Powód odmowy przydziału albo pusty napis. Czysta funkcja – ekran pokazuje ją także jako ostrzeżenie."""
    others = [occupant for occupant in occupants if occupant.pk != member.pk]
    if len(others) >= room.capacity:
        return "Pokój jest pełny."
    return _compatible(room, member, others, day)


def _compatible(room: Room, member: DelegationMember, others: list[DelegationMember], day) -> str:
    """Reguły 2–4 (bez pojemności): ta osoba w tym pokoju razem z tymi współlokatorami."""
    if not member.needs_accommodation:
        return "Osoba zgłosiła, że nie potrzebuje noclegu."
    minor = member.is_minor_on(day)
    reason = _person_fits(room, member, minor, len(others))
    if reason:
        return reason
    for other in others:
        other_minor = other.is_minor_on(day)
        if other_minor != minor:
            return "Niepełnoletni nie może dzielić pokoju z osobą dorosłą."
        if _single_minor(other, other_minor):
            return "W pokoju mieszka niepełnoletni bez podanej płci – pokój jest jednoosobowy."
    return ""


def room_problems(room: Room, occupants: list[DelegationMember], day) -> list[str]:
    """Naruszenia zasad w pokoju **takim, jaki jest** – po zmianie daty finału albo danych.

    Każdą osobę sprawdzamy tymi samymi regułami, co przydział (:func:`_compatible`), względem reszty
    mieszkańców. Pojemność liczymy raz, dla całego pokoju – inaczej pełny pokój wyglądałby jak
    przepełniony przy każdej osobie z osobna.
    """
    problems: list[str] = []
    if len(occupants) > room.capacity:
        problems.append("Pokój jest przepełniony.")
    for member in occupants:
        others = [occupant for occupant in occupants if occupant.pk != member.pk]
        reason = _compatible(room, member, others, day)
        if reason and reason not in problems:
            problems.append(reason)
    return problems


def edition_room_problems(edition) -> dict[int, list[str]]:
    """Pokoje edycji z naruszeniami: ``{room_id: [powody]}`` – dla komunikatu po zmianie daty finału."""
    day = reference_day(edition)
    occupants: dict[int, list[DelegationMember]] = {}
    for member in DelegationMember.objects.filter(room__edition=edition).select_related(
        "participant", "guest", "room"
    ):
        occupants.setdefault(member.room_id, []).append(member)
    result = {}
    for room in rooms_of(edition):
        problems = room_problems(room, occupants.get(room.pk, []), day)
        if problems:
            result[room.pk] = problems
    return result


@transaction.atomic
def assign(member: DelegationMember, room: Room | None, *, actor, request=None) -> DelegationMember:
    """Przydziela osobę do pokoju (``None`` – zdejmuje przydział). Reguły pod blokadą pokoju.

    Blokada wiersza pokoju szereguje dwa równoległe przydziały do tego samego pokoju – bez niej
    dwóch oficerów klikających w tej samej sekundzie wpisałoby piątą osobę do czwórki.
    """
    if room is not None:
        if room.edition_id != member.delegation.edition_id:
            raise Http404("Pokój należy do innej edycji.")
        Room.objects.select_for_update().filter(pk=room.pk).values_list("pk", flat=True).first()
        occupants = list(
            DelegationMember.objects.filter(room=room).select_related("participant", "guest", "user")
        )
        reason = check_assignment(room, member, occupants, reference_day(room.edition))
        if reason:
            raise DomainError(reason, "ROOM_RULE", status.HTTP_409_CONFLICT)
    before = member.room_id
    DelegationMember.objects.filter(pk=member.pk).update(room=room)
    member.room = room
    audit(
        actor,
        "logistics.room_assigned",
        member,
        {"from": before, "to": room.pk if room is not None else None},
        request=request,
    )
    return member


def recheck_member(member: DelegationMember, *, actor, request=None) -> str:
    """Po zmianie danych osoby: czy nadal pasuje do swojego pokoju. Jeśli nie – zdejmuje przydział.

    Zwraca powód zdjęcia (pusty napis, gdy przydział zostaje). Wołane z ``services.save_member``
    w tej samej transakcji, po zapisie pól osoby.
    """
    if not member.room_id:
        return ""
    room = Room.objects.select_for_update().get(pk=member.room_id)
    occupants = list(
        DelegationMember.objects.filter(room=room)
        .exclude(pk=member.pk)
        .select_related("participant", "guest")
    )
    reason = check_assignment(room, member, occupants, reference_day(room.edition))
    if not reason:
        return ""
    DelegationMember.objects.filter(pk=member.pk).update(room=None)
    member.room = None
    audit(
        actor,
        "logistics.room_unassigned",
        member,
        {"room": room.pk, "reason": "rules_after_data_change"},
        request=request,
    )
    return reason


def rooming_rows(edition, members: list[DelegationMember]) -> dict:
    """Pokoje z mieszkańcami, naruszenia zasad per pokój i lista osób bez przydziału."""
    day = reference_day(edition)
    rooms = list(rooms_of(edition))
    by_room: dict[int, list[DelegationMember]] = {room.pk: [] for room in rooms}
    unassigned = []
    for member in members:
        if member.room_id and member.room_id in by_room:
            by_room[member.room_id].append(member)
        elif member.needs_accommodation:
            unassigned.append(member)
    rows = []
    for room in rooms:
        occupants = by_room[room.pk]
        rows.append(
            {
                "room": room,
                "occupants": [{"member": m, "minor": m.is_minor_on(day)} for m in occupants],
                "free": max(0, room.capacity - len(occupants)),
                "problems": room_problems(room, occupants, day),
            }
        )
    return {
        "rows": rows,
        "unassigned": [{"member": m, "minor": m.is_minor_on(day)} for m in unassigned],
        "day": day,
        "beds": sum(room.capacity for room in rooms),
        "needed": sum(1 for m in members if m.needs_accommodation),
        "violations": sum(1 for row in rows if row["problems"]),
    }
