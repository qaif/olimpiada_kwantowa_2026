"""Logika domenowa etapu w formie rozmowy kwalifikacyjnej online.

Osobny moduł od ``services.py``, bo to inny przedmiot: tam jest kalendarz edycji i arkusz zadań,
tutaj – terminy rozmów i zapisy uczestników. Zasady są te same, co w całym projekcie: reguły
mieszkają tu, widoki tylko orkiestrują, każda zmiana zostawia wpis audytowy **bez danych
osobowych** (w ``diff`` idą identyfikatory i liczniki, nigdy nazwiska i adresy), a czas zawsze
przez ``timezone.now()``.

Dlaczego okno rozmów to po prostu okno etapu: koordynator ma już jeden komplet dat do pilnowania
(``opens_at``…``deadline_at``) i ogłasza go uczestnikom. Drugi, niezależny zakres „od kiedy do
kiedy rozmowy” rozjeżdżałby się z tamtym przy pierwszym przesunięciu terminu, a uczestnik czytałby
na harmonogramie jedną datę i widział w panelu drugą.

Czego tu **nie ma**: sprawdzania kolizji terminów. Kilka komisji rozmawia równolegle, więc dwa
sloty o tych samych godzinach są normalną sytuacją; rozróżnia je ``note`` („komisja A”), a liczbę
osób – ``capacity``.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Prefetch, ProtectedError
from django.utils import timezone
from rest_framework import status

from apps.core.api import DomainError

from .models import (
    InterviewBooking,
    InterviewSlot,
    Stage,
    StageEntry,
    StageEntryStatus,
)

logger = logging.getLogger(__name__)

#: Granice pojedynczego wywołania „dodaj terminy”. Rozmowa krótsza niż minuta i dłuższa niż osiem
#: godzin to zawsze literówka, a nie plan zawodów; pięćdziesiąt slotów naraz wystarcza na cały
#: dzień pracy jednej komisji i chroni przed wpisaniem „500” w pole liczby powtórzeń.
MIN_DURATION_MINUTES = 1
MAX_DURATION_MINUTES = 480
MIN_SLOT_COUNT = 1
MAX_SLOT_COUNT = 50
#: Górna granica miejsc w jednym terminie – rozmowa kwalifikacyjna to spotkanie, a nie wykład.
MAX_SLOT_CAPACITY = 20


def _conflict(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, status.HTTP_409_CONFLICT)


def _bad_request(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, status.HTTP_400_BAD_REQUEST)


def _assert_interview_stage(stage: Stage, *, http_status: int) -> None:
    """Wspólna brama wejściowa: wszystko w tym module dotyczy wyłącznie etapu w formie rozmowy.

    Kod odpowiedzi zależy od tego, kto pyta: koordynatorowi, który wszedł na ekran terminów złego
    etapu, należy się 409 (konflikt stanu), uczestnikowi próbującemu się zapisać – 403 (nie ma do
    czego).
    """
    if not stage.is_interview:
        raise DomainError(
            "Ten etap nie odbywa się w formie rozmowy kwalifikacyjnej.",
            "STAGE_NOT_INTERVIEW",
            http_status,
        )


# --- terminy: koordynator -----------------------------------------------------------------------


@transaction.atomic
def create_slots(
    stage: Stage,
    actor,
    *,
    starts_at,
    duration_minutes: int,
    count: int = 1,
    capacity: int = 1,
    meeting_url: str = "",
    note: str = "",
    request=None,
) -> list[InterviewSlot]:
    """Tworzy ``count`` kolejnych terminów po ``duration_minutes`` minut, licząc od ``starts_at``.

    Terminy powstają hurtem, bo tak wygląda praca koordynatora: „w czwartek od 9:00 dwanaście
    rozmów po 20 minut”. Klikanie tego pojedynczo dwanaście razy byłoby dwunastoma okazjami do
    pomyłki o godzinę.

    Wszystkie sloty muszą się zmieścić w oknie etapu – to okno jest ogłoszonym terminem zawodów,
    więc rozmowa poza nim byłaby terminem, którego nie ma na harmonogramie.
    """
    _assert_interview_stage(stage, http_status=status.HTTP_409_CONFLICT)
    now = timezone.now()

    if not (MIN_DURATION_MINUTES <= duration_minutes <= MAX_DURATION_MINUTES):
        raise _bad_request(
            f"Długość rozmowy musi mieścić się w {MIN_DURATION_MINUTES}–{MAX_DURATION_MINUTES} minutach.",
            "SLOT_DURATION_INVALID",
        )
    if not (MIN_SLOT_COUNT <= count <= MAX_SLOT_COUNT):
        raise _bad_request(
            f"Liczba terminów musi mieścić się w {MIN_SLOT_COUNT}–{MAX_SLOT_COUNT}.",
            "SLOT_COUNT_INVALID",
        )
    if not (1 <= capacity <= MAX_SLOT_CAPACITY):
        raise _bad_request(
            f"Liczba miejsc musi mieścić się w 1–{MAX_SLOT_CAPACITY}.", "SLOT_CAPACITY_INVALID"
        )
    if starts_at <= now:
        raise _bad_request(
            "Termin rozmowy musi być w przyszłości – na przeszły nikt się już nie zapisze.",
            "SLOT_IN_PAST",
        )

    duration = timedelta(minutes=duration_minutes)
    last_end = starts_at + duration * count
    if starts_at < stage.opens_at or last_end > stage.deadline_at:
        raise _bad_request(
            "Terminy rozmów muszą mieścić się w oknie etapu "
            f"({timezone.localtime(stage.opens_at):%Y-%m-%d %H:%M} – "
            f"{timezone.localtime(stage.deadline_at):%Y-%m-%d %H:%M}, czas polski).",
            "SLOT_OUTSIDE_STAGE",
        )

    slots = [
        InterviewSlot(
            stage=stage,
            starts_at=starts_at + duration * index,
            ends_at=starts_at + duration * (index + 1),
            capacity=capacity,
            meeting_url=meeting_url or "",
            note=note or "",
        )
        for index in range(count)
    ]
    for slot in slots:
        # ``validate_constraints=False``: sloty jeszcze nie istnieją, więc walidacja constraintów
        # kosztowałaby zapytanie do bazy na każdy z nich (do pięćdziesięciu w jednym wywołaniu),
        # a sprawdzałaby dokładnie te dwie reguły, które powyższe warunki i tak już wymusiły
        # (dodatnia długość terminu i co najmniej jedno miejsce). Baza pilnuje ich na końcu.
        slot.full_clean(validate_constraints=False)
    InterviewSlot.objects.bulk_create(slots)

    from apps.core.models import audit

    audit(
        actor,
        "interview.slots_created",
        stage,
        {
            "count": count,
            "from": starts_at.isoformat(),
            "to": last_end.isoformat(),
            "capacity": capacity,
        },
        request=request,
    )
    logger.info("Etap %s: utworzono %s terminów rozmów po %s min.", stage.pk, count, duration_minutes)
    return slots


@transaction.atomic
def delete_slot(slot: InterviewSlot, actor, *, request=None) -> None:
    """Usuwa termin. Odmawia, gdy ktoś jest już na niego zapisany.

    Zapis jest umową z uczestnikiem: skasowanie terminu z zapisami zostawiłoby go bez rozmowy
    i bez informacji o tym fakcie. Koordynator, który musi odwołać termin, najpierw uzgadnia
    zmianę z uczestnikiem – i dopiero wtedy termin jest pusty.

    Wiersz terminu blokujemy (``select_for_update``) **przed** sprawdzeniem zapisów, bo inaczej
    między „nikt nie jest zapisany” a ``delete()`` mieści się cały ``book_slot`` uczestnika.
    ``of=("self",)`` zawęża blokadę do samego terminu – etap i edycja nie mają tu być zamrożone,
    bo pracują na nich równolegle zupełnie inne ekrany panelu. Wyjątek ``ProtectedError`` jest
    drugą linią: gdyby baza dopuściła jednak zapis w tej szczelinie, koordynator ma dostać ten sam
    komunikat 409, a nie 500.
    """
    locked = InterviewSlot.objects.select_for_update(of=("self",)).get(pk=slot.pk)
    if InterviewBooking.objects.filter(slot=locked).exists():
        raise _conflict("Na ten termin ktoś jest już zapisany – nie można go usunąć.", "SLOT_HAS_BOOKINGS")

    from apps.core.models import audit

    # Audyt przed skasowaniem: po ``delete()`` obiekt nie ma ``pk``, więc wpisu nie dałoby się
    # połączyć z historią etapu.
    audit(
        actor,
        "interview.slot_deleted",
        locked,
        {"stage": locked.stage_id, "starts_at": locked.starts_at.isoformat(), "capacity": locked.capacity},
        request=request,
    )
    try:
        locked.delete()
    except ProtectedError as exc:
        raise _conflict(
            "Na ten termin ktoś jest już zapisany – nie można go usunąć.", "SLOT_HAS_BOOKINGS"
        ) from exc


# --- zapisy: uczestnik --------------------------------------------------------------------------


def _entry_for(participant, stage: Stage) -> StageEntry:
    """Wpis uczestnika w etapie – jedyna przepustka do zapisu na rozmowę."""
    entry = StageEntry.objects.filter(participant=participant, stage=stage).first()
    if entry is None or entry.status == StageEntryStatus.DISQUALIFIED:
        raise DomainError(
            "Do rozmowy przystępują wyłącznie osoby zakwalifikowane w poprzednim etapie.",
            "NOT_QUALIFIED",
            status.HTTP_403_FORBIDDEN,
        )
    return entry


def _assert_stage_open(stage: Stage) -> None:
    """Etap zamknięty nie przyjmuje już ani zapisów, ani rezygnacji.

    ``closed_at`` znaczy „ten etap jest rozliczony”: lista osób, które stanęły przed komisją, jest
    od tej chwili faktem, na którym stoi kwalifikacja do następnego etapu. Dopisanie się po
    zamknięciu byłoby terminem, którego nikt nie obsłuży, a wypisanie – zniknięciem rozmowy,
    która się odbyła.
    """
    if stage.closed_at is not None:
        raise _conflict(
            "Etap jest zamknięty – zapisy na rozmowy nie są już możliwe.",
            "STAGE_CLOSED",
        )


def _assert_bookable(stage: Stage) -> None:
    _assert_interview_stage(stage, http_status=status.HTTP_403_FORBIDDEN)
    if not stage.edition.is_current:
        # Etap edycji archiwalnej nie przyjmuje zapisów, choćby jego terminy wyglądały na otwarte.
        # Własny kod błędu, a nie ``STAGE_NOT_INTERVIEW``: forma etapu jest tu w porządku, zły jest
        # rocznik – a uczestnik, który zobaczy „to nie jest etap w formie rozmowy” na ekranie pełnym
        # terminów rozmów, dostaje komunikat wprost mu przeczący.
        raise DomainError(
            "Ten etap należy do edycji archiwalnej – zapisy na rozmowy są w niej zamknięte.",
            "EDITION_NOT_CURRENT",
            status.HTTP_403_FORBIDDEN,
        )
    _assert_stage_open(stage)


def _confirmation_message(stage: Stage, slot: InterviewSlot) -> tuple[str, str]:
    """Treść listu potwierdzającego. Bez danych osobowych – adresat i tak wie, kim jest.

    Godziny idą w czasie polskim: uczestnik ma przepisać je do kalendarza, a nie przeliczać
    z UTC. Link do rozmowy jest w liście **i** w panelu; gdyby list zaginął w spamie, termin
    nadal jest osiągalny po zalogowaniu.
    """
    starts = timezone.localtime(slot.starts_at)
    ends = timezone.localtime(slot.ends_at)
    subject = f"Termin rozmowy kwalifikacyjnej: {stage.display_name}"
    lines = [
        f"Etap: {stage.display_name} ({stage.edition.year_label}).",
        f"Termin rozmowy: {starts:%d.%m.%Y, %H:%M} – {ends:%H:%M} (czas polski).",
    ]
    if slot.note:
        lines.append(f"Oznaczenie: {slot.note}")
    if slot.meeting_url:
        lines.append(f"Link do rozmowy: {slot.meeting_url}")
    lines.append(
        "Ten sam termin i link znajdziesz po zalogowaniu w panelu uczestnika. Termin możesz "
        "zmienić lub odwołać do chwili jego rozpoczęcia."
    )
    return subject, "\n".join(lines)


def _send_confirmation(entry: StageEntry, stage: Stage, slot: InterviewSlot) -> None:
    """Kolejkuje potwierdzenie **po commicie** – worker nie może czytać stanu, którego nie ma.

    Wysyłka jest zadaniem na kolejce ``mail``, a nie ``send_mail`` w środku żądania: niedostępny
    MTA nie może zamienić udanego zapisu na błąd 500 ani zająć workera gunicorna na czas timeoutu.
    """
    subject, message = _confirmation_message(stage, slot)
    recipient = entry.participant.user.email

    def _enqueue() -> None:
        from apps.core.tasks import send_mail_task

        send_mail_task.delay(subject, message, [recipient])

    if recipient:
        transaction.on_commit(_enqueue)


@transaction.atomic
def book_slot(participant, slot: InterviewSlot, *, now=None, request=None) -> InterviewBooking:
    """Zapisuje uczestnika na termin rozmowy albo **przenosi** jego dotychczasowy zapis.

    Przeniesienie, a nie drugi zapis: uczestnik ma w etapie jedną rozmowę (``OneToOneField`` na
    ``StageEntry``), więc „zmieniam termin” jest jedną operacją, w jednej transakcji. Gdyby były
    to dwa kliknięcia (odwołaj, zapisz się), między nimi ktoś inny zająłby ostatnie wolne miejsce
    i uczestnik zostałby bez terminu.

    Wolne miejsca liczymy pod ``select_for_update()`` na wierszu terminu: bez blokady dwa
    równoległe zapisy widziałyby ten sam stan „jedno miejsce wolne” i oba by weszły. Blokada jest
    zawężona do samego terminu (``of=("self",)``) i dlatego etap czytamy **osobnym** zapytaniem:
    ``select_related`` w jednym zapytaniu z ``FOR UPDATE`` zamknąłby także wiersz etapu i wiersz
    edycji, przez co dwa zapisy na dwa różne terminy tego samego etapu stałyby w kolejce, a każda
    edycja etapu w panelu koordynatora czekałaby na uczestnika (i odwrotnie).
    """
    now = now or timezone.now()
    locked = InterviewSlot.objects.select_for_update(of=("self",)).get(pk=slot.pk)
    stage = Stage.objects.select_related("edition").get(pk=locked.stage_id)
    _assert_bookable(stage)
    entry = _entry_for(participant, stage)

    if locked.starts_at <= now:
        raise _conflict("Ten termin już się rozpoczął – wybierz inny.", "SLOT_STARTED")

    existing = InterviewBooking.objects.filter(entry=entry).select_related("slot").first()
    if existing is not None:
        if existing.slot_id == locked.pk:
            raise _conflict("Jesteś już zapisany na ten termin.", "ALREADY_BOOKED")
        if existing.slot.starts_at <= now:
            raise _conflict(
                "Twoja rozmowa już się rozpoczęła – terminu nie można już zmienić.",
                "BOOKING_LOCKED",
            )

    taken = InterviewBooking.objects.filter(slot=locked).count()
    if taken >= locked.capacity:
        raise _conflict("Na tym terminie nie ma już wolnych miejsc.", "SLOT_FULL")

    from apps.core.models import audit

    if existing is not None:
        previous_slot_id = existing.slot_id
        existing.delete()
        booking = InterviewBooking.objects.create(slot=locked, entry=entry)
        action, diff = (
            "interview.booking_moved",
            {"from_slot": previous_slot_id, "to_slot": locked.pk},
        )
    else:
        booking = InterviewBooking.objects.create(slot=locked, entry=entry)
        action, diff = "interview.booked", {"slot": locked.pk}

    # Cel wpisu to ``StageEntry``, a nie uczestnik: historia zapisu należy do udziału w etapie
    # i czyta się ją razem z resztą jego przebiegu. W ``diff`` idą wyłącznie identyfikatory.
    audit(participant.user, action, entry, diff, request=request)
    _send_confirmation(entry, stage, locked)
    return booking


@transaction.atomic
def cancel_booking(participant, *, stage: Stage, now=None, request=None) -> None:
    """Odwołuje zapis uczestnika w tym etapie. Po rozpoczęciu rozmowy – już nie.

    Rozmowa, która się zaczęła, jest faktem: skasowanie jej zapisu zostawiłoby komisję z pustym
    kalendarzem i bez śladu, kto przed nią stanął.

    Blokada obejmuje wyłącznie wiersz zapisu (``of=("self",)``); termin dociągamy osobnym
    zapytaniem, żeby rezygnacja jednej osoby nie zamrażała slotu (i przez to całej kolejki
    zapisów) na czas transakcji.
    """
    now = now or timezone.now()
    entry = _entry_for(participant, stage)
    _assert_stage_open(stage)
    booking = InterviewBooking.objects.select_for_update(of=("self",)).filter(entry=entry).first()
    if booking is None:
        raise _conflict("Nie masz zapisanego terminu w tym etapie.", "BOOKING_NOT_FOUND")
    if booking.slot.starts_at <= now:
        raise _conflict("Rozmowa już się rozpoczęła – terminu nie można odwołać.", "BOOKING_LOCKED")

    from apps.core.models import audit

    slot_id = booking.slot_id
    booking.delete()
    audit(participant.user, "interview.cancelled", entry, {"slot": slot_id}, request=request)


# --- odczyt dla ekranów -------------------------------------------------------------------------


def _slots_with_counts(stage: Stage):
    """Terminy etapu z liczbą zapisów policzoną w bazie – jedno zapytanie na całą listę."""
    return stage.interview_slots.annotate(taken=Count("bookings")).order_by("starts_at", "id")


def slots_for_participant(stage: Stage, participant, now=None) -> list[dict]:
    """Wiersze tabeli terminów dla uczestnika: termin, wolne miejsca i to, czy wolno się zapisać.

    Link do rozmowy **nie jest** częścią wiersza: dokłada go szablon wyłącznie przy własnym
    terminie uczestnika. Adres pokoju wideo, do którego wchodzi się bez logowania, jest de facto
    poświadczeniem – lista terminów pokazywana wszystkim zakwalifikowanym nie może go nieść.

    Każdy niedostępny wiersz niesie ``reason`` – powód, dla którego ``book_slot`` by go odmówił.
    Bez tego panel pisał pod każdym zablokowanym przyciskiem „Brak miejsc”, więc uczestnik, którego
    rozmowa właśnie się zaczęła, czytał, że wszystkie terminy w olimpiadzie są pełne. Powody idą
    w tej samej kolejności, w jakiej odmawia serwis (etap → termin → własny zapis → miejsca), żeby
    ekran i odpowiedź na POST nie mówiły dwóch różnych rzeczy:

    - ``"closed"`` – etap zamknięty, archiwalny albo nie w formie rozmowy (``STAGE_CLOSED`` /
      ``EDITION_NOT_CURRENT``),
    - ``"started"`` – ten termin już się zaczął (``SLOT_STARTED``),
    - ``"locked"`` – *własna* rozmowa uczestnika już trwa, więc terminu nie da się przenieść
      (``BOOKING_LOCKED``),
    - ``"full"`` – komplet zapisanych (``SLOT_FULL``).
    """
    now = now or timezone.now()
    mine = booking_for_participant(stage, participant)
    mine_slot_id = mine.slot_id if mine is not None else None
    booking_locked = mine is not None and mine.slot.starts_at <= now
    stage_open = stage.is_interview and stage.edition.is_current and stage.closed_at is None
    rows = []
    for slot in _slots_with_counts(stage):
        free = max(slot.capacity - slot.taken, 0)
        is_mine = slot.pk == mine_slot_id
        started = slot.starts_at <= now
        reason = None
        if not is_mine:
            if not stage_open:
                reason = "closed"
            elif started:
                reason = "started"
            elif booking_locked:
                reason = "locked"
            elif free <= 0:
                reason = "full"
        rows.append(
            {
                "slot": slot,
                "free": free,
                "is_mine": is_mine,
                "bookable": not is_mine and reason is None,
                "started": started,
                "reason": reason,
            }
        )
    return rows


def slots_for_coordinator(stage: Stage) -> list[InterviewSlot]:
    """Terminy wraz z zapisanymi uczestnikami – jedyny ekran, na którym wolno pokazać dane osobowe.

    ``prefetch_related`` schodzi aż do konta uczestnika, bo tabela wypisuje imię, nazwisko i adres
    e-mail: bez tego lista robiłaby dwa zapytania na każdy zapis.
    """
    bookings = InterviewBooking.objects.select_related(
        "entry", "entry__participant", "entry__participant__user"
    ).order_by("created_at", "id")
    return list(_slots_with_counts(stage).prefetch_related(Prefetch("bookings", queryset=bookings)))


def booking_for_participant(stage: Stage, participant) -> InterviewBooking | None:
    """Zapis uczestnika w tym etapie albo ``None`` – do karty „Rozmowa kwalifikacyjna”."""
    return (
        InterviewBooking.objects.select_related("slot")
        .filter(entry__stage=stage, entry__participant=participant)
        .first()
    )
