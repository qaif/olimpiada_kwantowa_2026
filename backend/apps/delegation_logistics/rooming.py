"""Lista pokoi finału: pokoje, przydziały i zasady zakwaterowania niepełnoletnich (LOG-01 § 4).

Zasady są **twarde** (serwis odmawia), a nie podpowiedzią na ekranie, bo dotyczą bezpieczeństwa
dzieci – a lista pokoi bywa edytowana w pośpiechu, w nocy przed przyjazdem:

1. pojemność pokoju nie może być przekroczona,
2. pokój ma płeć (kobiety / mężczyźni) albo jest „dowolny” – i ten ostatni wyłącznie dla dorosłych,
3. **niepełnoletni nigdy nie śpi w jednym pokoju z dorosłym** – także z opiekunem własnej drużyny.
   Pokój opiekuna i pokój uczniów są osobne; jeśli regulamin konkretnego finału przewiduje wyjątek
   (rodzic z dzieckiem), organizator rozwiązuje go poza systemem, a nie przez obejście reguły.

Wiek liczymy na **pierwszy dzień finału** (``FinalEvent.starts_on``), bo to w tym dniu dziecko
przyjeżdża; bez daty finału – na dziś. Brak daty urodzenia ucznia = niepełnoletni
(``DelegationMember.is_minor_on`` – odwrót ostrożny).
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


def rooms_of(edition):
    return Room.objects.filter(edition=edition).order_by("building", "name", "id")


def room_for(competition, pk: int) -> Room:
    room = Room.objects.for_competition(competition).filter(pk=pk).first()
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


def _gender_ok(room: Room, member: DelegationMember, minor: bool) -> str:
    if room.gender == RoomGender.ANY:
        return "" if not minor else "Pokój „dowolna płeć” jest wyłącznie dla dorosłych."
    if member.gender not in (Gender.FEMALE, Gender.MALE):
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
    minor = member.is_minor_on(day)
    reason = _gender_ok(room, member, minor)
    if reason:
        return reason
    for other in others:
        if other.is_minor_on(day) != minor:
            return "Niepełnoletni nie może dzielić pokoju z osobą dorosłą."
    return ""


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


def rooming_rows(edition, members: list[DelegationMember]) -> dict:
    """Pokoje z mieszkańcami i lista osób bez przydziału (potrzebujących noclegu)."""
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
            }
        )
    return {
        "rows": rows,
        "unassigned": [{"member": m, "minor": m.is_minor_on(day)} for m in unassigned],
        "day": day,
        "beds": sum(room.capacity for room in rooms),
        "needed": sum(1 for m in members if m.needs_accommodation),
    }
