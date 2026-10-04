"""Czynności logistyki finału: członkowie delegacji, formularz, goście, zdjęcia, finał (LOG-01).

Widoki tylko orkiestrują – każda reguła („czy wolno zmienić tę grupę po terminie”, „czy dane
o zdrowiu wolno zapisać”, „kto jest członkiem tej delegacji”) ma tu jedno miejsce. Zestawienia
(przyloty, pokoje, wyżywienie) są w ``reports`` i ``rooming``, listy wizowe w ``letters``,
identyfikatory i odhaczanie w ``badges``, retencja i RODO w ``privacy``.

Granice bezpieczeństwa:

- **zakres delegacji.** Opiekun dochodzi do członka wyłącznie przez :func:`member_for_leader` –
  queryset zawężony do **jego** delegacji, więc członek innego kraju albo konkursu daje 404,
- **zakres konkursu.** Oficer dochodzi do członka przez :func:`member_for_competition` – zakres
  ``for_competition`` (cudzy konkurs → 404),
- **audyt bez wartości.** Wpis ``logistics.member_updated`` niesie **nazwy** zmienionych pól, nigdy
  ich treść: numer paszportu albo alergia przepisane do dziennika zdarzeń zostałyby tam bez
  szyfrowania i bez retencji.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import date, datetime

from django.db import transaction
from django.http import Http404
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import status

from apps.core.api import DomainError
from apps.core.models import audit
from apps.submissions.storage import get_submission_storage, sanitize_segment

from .models import (
    HEALTH_CONSENT_VERSION,
    DelegationGuest,
    DelegationMember,
    Diet,
    FieldGroup,
    FinalEvent,
    GuestRole,
    MemberKind,
    ScanStatus,
    enabled,
)

logger = logging.getLogger(__name__)

#: Najwięcej gości (obserwatorów) w jednej delegacji. Regulaminy olimpiad przewidują zwykle dwóch
#: obserwatorów; dziesięć to zapas na rodziców i tłumacza, a zarazem bariera przed wpisaniem całej
#: szkoły jako „gości” na koszt organizatora.
MAX_GUESTS = 10

#: Prefiks kluczy zdjęć w prywatnym storage (ten sam bucket, co rozwiązania – jak skany statusu ucznia).
PHOTO_PREFIX = "final-badges"
PHOTO_MAX_BYTES = 5 * 1024 * 1024
PHOTO_FORMATS = {
    "jpg": (b"\xff\xd8\xff", "image/jpeg"),
    "png": (b"\x89PNG\r\n\x1a\n", "image/png"),
}
#: Zdjęcie po czystym skanie jest przekodowywane (Pillow) do JPEG-a najwyżej tej wielkości – bez EXIF-u
#: (współrzędne GPS z telefonu, model aparatu) i bez ryzyka, że PDF identyfikatorów kilkuset osób
#: dostanie kilkaset zdjęć po 12 Mpx (poprawka po przeglądzie, M3).
PHOTO_MAX_SIZE = (600, 800)
PHOTO_JPEG_QUALITY = 85
#: Najwięcej pikseli zdjęcia **przed** dekodowaniem – obrona przed „bombą dekompresyjną” (mały plik,
#: ogromny obraz). 40 Mpx mieści każdy aparat telefonu z zapasem.
PHOTO_MAX_PIXELS = 40_000_000

#: Pola każdej grupy – kolejność jest kolejnością formularza.
GROUP_FIELDS: dict[str, tuple[str, ...]] = {
    FieldGroup.IDENTITY: (
        "passport_name",
        "nationality",
        "date_of_birth",
        "passport_number",
        "passport_expiry",
    ),
    FieldGroup.TRAVEL: (
        "arrival_date",
        "arrival_time",
        "arrival_mode",
        "arrival_number",
        "arrival_place",
        "departure_date",
        "departure_time",
        "departure_mode",
        "departure_number",
        "departure_place",
    ),
    FieldGroup.ACCOMMODATION: ("needs_accommodation", "gender", "roommate_preference", "accommodation_notes"),
    FieldGroup.HEALTH: ("diet", "diet_notes", "allergies", "medical_notes"),
    FieldGroup.PERSONAL: ("tshirt_size", "emergency_name", "emergency_phone"),
}

#: Pola wymagane do uznania grupy za kompletną (przypomnienia, kolumny „braki”).
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    FieldGroup.IDENTITY: GROUP_FIELDS[FieldGroup.IDENTITY],
    FieldGroup.TRAVEL: (
        "arrival_date",
        "arrival_time",
        "arrival_mode",
        "arrival_place",
        "departure_date",
        "departure_time",
        "departure_mode",
        "departure_place",
    ),
    FieldGroup.ACCOMMODATION: ("gender",),
    FieldGroup.HEALTH: ("diet",),
    FieldGroup.PERSONAL: ("tshirt_size", "emergency_name", "emergency_phone"),
}

#: Pola, które przechowujemy jako napis ISO w polu szyfrowanym, a formularz podaje jako ``date``.
ENCRYPTED_DATES = ("date_of_birth", "passport_expiry")

HEALTH_FIELDS = GROUP_FIELDS[FieldGroup.HEALTH]

NATIONALITY_RE = re.compile(r"^[A-Z]{2}$")


# --- bramki i finał -----------------------------------------------------------------------------


def require_enabled(competition) -> None:
    """404, gdy konkurs nie prowadzi logistyki finału – ekranów wtedy **nie ma**."""
    if not enabled(competition):
        raise Http404("Ten konkurs nie prowadzi logistyki finału dla delegacji.")


def collects_health(competition) -> bool:
    """Decyzja D21 – czytana z logistyki etapu, a nie powielana (patrz docstring ``models``)."""
    from apps.competitions.logistics import collects_special_needs

    return collects_special_needs(competition)


def current_edition(competition):
    from apps.competitions.services import current_edition as _current

    edition = _current(competition)
    if edition is None:
        raise Http404("Konkurs nie ma bieżącej edycji.")
    return edition


def event_for(edition) -> FinalEvent | None:
    return FinalEvent.objects.filter(edition=edition).first()


EVENT_FIELDS = (
    "name",
    "city",
    "venue",
    "starts_on",
    "ends_on",
    "retention_days",
    "letter_prefix",
    "deadline_identity",
    "deadline_travel",
    "deadline_accommodation",
    "deadline_health",
    "deadline_personal",
)


@transaction.atomic
def save_event(competition, edition, *, actor, request=None, **fields) -> FinalEvent:
    """Zapisuje ustawienia finału (zakłada wiersz przy pierwszym zapisie). Audyt: nazwy pól."""
    if edition.competition_id != competition.pk:
        raise Http404("Edycja należy do innego konkursu.")
    event, _created = FinalEvent.objects.select_for_update().get_or_create(
        edition=edition, defaults={"competition": competition}
    )
    changed = []
    for name in EVENT_FIELDS:
        if name in fields and getattr(event, name) != fields[name]:
            setattr(event, name, fields[name])
            changed.append(name)
    if event.starts_on and event.ends_on and event.ends_on < event.starts_on:
        raise DomainError(
            "Ostatni dzień finału nie może być przed pierwszym.", "EVENT_DATES", status.HTTP_400_BAD_REQUEST
        )
    event.updated_at = timezone.now()
    event.save()
    #: Liczba pokoi z naruszeniem zasad po zmianie pierwszego dnia finału (wiek liczony na ten dzień).
    #: Naruszenia są oznaczane, a nie naprawiane – powód w docstringu ``rooming`` (H2).
    event.room_violations = 0
    if "starts_on" in changed:
        from .rooming import edition_room_problems

        event.room_violations = len(edition_room_problems(edition))
    if changed:
        audit(
            actor,
            "logistics.event_updated",
            event,
            {"fields": changed, "room_violations": event.room_violations},
            request=request,
        )
    return event


def group_deadline(event: FinalEvent | None, group: str):
    return event.deadline_for(group) if event is not None else None


def group_locked(event: FinalEvent | None, group: str, now=None) -> bool:
    """Czy termin grupy minął – po nim opiekun grupy nie zmienia (oficer – tak)."""
    if event is not None and event.purged_at is not None:
        return True
    deadline = group_deadline(event, group)
    return deadline is not None and deadline <= (now or timezone.now())


def is_purged(edition) -> bool:
    event = event_for(edition)
    return event is not None and event.purged_at is not None


def require_not_purged(edition) -> None:
    """Po retencji finału nikt – także oficer – nie wpisuje nowych danych osób (M1)."""
    if is_purged(edition):
        raise DomainError(
            _("Finał się zakończył, a dane delegacji zostały usunięte."),
            "LOGISTICS_PURGED",
            status.HTTP_409_CONFLICT,
        )


def require_retention_anchor(event: FinalEvent | None) -> None:
    """Dane paszportowe i o zdrowiu przyjmujemy dopiero, gdy wiadomo, kiedy zostaną usunięte (M1).

    Termin usunięcia liczy się od ostatniego dnia finału. Bez niego automat retencji nie ma od czego
    liczyć, więc taki numer paszportu leżałby w bazie bez końca – odmowa jest uczciwsza niż zastępczy
    termin, którego nikt nie ustalił.
    """
    if event is None or event.ends_on is None:
        raise DomainError(
            _(
                "Organizator nie ustalił jeszcze dat finału – dane dokumentu podróży i o zdrowiu "
                "przyjmiemy po ich ustaleniu."
            ),
            "LOGISTICS_NO_EVENT_DATES",
            status.HTTP_409_CONFLICT,
        )


def groups_for(competition) -> list[str]:
    """Grupy formularza w tym konkursie – „Zdrowie” wyłącznie przy decyzji D21."""
    groups = list(FieldGroup.values)
    if not collects_health(competition):
        groups.remove(FieldGroup.HEALTH)
    return groups


# --- członkowie ------------------------------------------------------------------------------------


def _member_queryset():
    return DelegationMember.objects.select_related(
        "participant__user", "user", "guest", "room", "delegation__country", "delegation__competition"
    )


def _delete_photos_on_commit(keys) -> None:
    """Kasuje obiekty zdjęć **po** commicie – ta sama kolejność, co przy skanach statusu ucznia."""
    keys = [key for key in keys if key]
    if not keys:
        return

    def _delete() -> None:
        storage = get_submission_storage()
        for key in keys:
            try:
                storage.delete(key)
            except Exception:  # noqa: BLE001 - osierocony obiekt bez wiersza, a nie błąd 500
                logger.exception("Nie udało się usunąć zdjęcia identyfikatora %s ze storage.", key)

    transaction.on_commit(_delete)


def delete_members(queryset) -> int:
    """Usuwa wiersze członków razem z plikami zdjęć i z ich wierszami w migawkach listów wizowych.

    Kaskada bazy nie wie ani o buckecie, ani o zaszyfrowanym JSON-ie listu: osoba wypisana z delegacji,
    usunięty gość czy skasowane konto nie może zostać z numerem paszportu w liście, który da się
    pobrać jeszcze miesiąc (poprawka po przeglądzie, M4). Każda droga usuwania członka idzie tędy.
    """
    from .letters import drop_person
    from .models import InvitationLetter

    rows = list(queryset.values_list("pk", "delegation_id"))
    keys = list(queryset.exclude(photo_key="").values_list("photo_key", flat=True))
    guest_ids = list(queryset.exclude(guest__isnull=True).values_list("guest_id", flat=True))
    if rows:
        member_ids = [pk for pk, _delegation in rows]
        letters = InvitationLetter.objects.filter(
            delegation_id__in={delegation for _pk, delegation in rows}, content_purged_at__isnull=True
        ).exclude(content="")
        for letter in letters:
            drop_person(letter, member_ids)
    deleted, _by_model = DelegationMember.objects.filter(pk__in=[pk for pk, _d in rows]).delete()
    DelegationGuest.objects.filter(pk__in=guest_ids).delete()
    _delete_photos_on_commit(keys)
    return deleted


def _sync_once(delegation, request) -> None:
    """Synchronizacja najwyżej raz na żądanie dla danej delegacji (L10)."""
    if request is not None:
        done = request.__dict__.setdefault("_logistics_synced", set())
        if delegation.pk in done:
            return
        done.add(delegation.pk)
    sync_members(delegation)


@transaction.atomic
def sync_members(delegation) -> None:
    """Dopasowuje wiersze członków do składu delegacji: uczniowie i opiekunowie z DEL-01.

    Lista uczniów i opiekunów należy do DEL-01 – tu jej nie powielamy, tylko dokładamy do niej dane
    pobytu. Uczeń wypisany z delegacji i opiekun odwołany znikają razem ze swoimi danymi (i zdjęciem):
    numer paszportu osoby, która nie jedzie, nie ma żadnego celu. Konta po anonimizacji odpadają tak
    samo. Wołane przy wejściu na ekran delegacji – skład zmienia się rzadko, a dwa zapytania na
    wejście są tańsze niż sygnały rozsiane po DEL-01.
    """
    from apps.accounts.delegations import DelegationLeader
    from apps.accounts.models import Participant

    if is_purged(delegation.edition):
        # Po retencji nie zakładamy pustych wierszy od nowa – finał się odbył, dane mają nie wracać.
        return
    students = set(
        Participant.objects.filter(delegation=delegation).exclude_anonymised().values_list("pk", flat=True)
    )
    leaders = set(
        DelegationLeader.objects.active().filter(delegation=delegation).values_list("user_id", flat=True)
    )
    rows = DelegationMember.objects.filter(delegation=delegation)
    stale = rows.filter(kind=MemberKind.STUDENT).exclude(participant_id__in=students) | rows.filter(
        kind=MemberKind.LEADER
    ).exclude(user_id__in=leaders)
    if stale.exists():
        delete_members(stale)
    have_students = set(rows.filter(kind=MemberKind.STUDENT).values_list("participant_id", flat=True))
    have_leaders = set(rows.filter(kind=MemberKind.LEADER).values_list("user_id", flat=True))
    DelegationMember.objects.bulk_create(
        [
            DelegationMember(delegation=delegation, kind=MemberKind.STUDENT, participant_id=pk)
            for pk in sorted(students - have_students)
        ]
        + [
            DelegationMember(delegation=delegation, kind=MemberKind.LEADER, user_id=pk)
            for pk in sorted(leaders - have_leaders)
        ],
        ignore_conflicts=True,
    )


KIND_ORDER = {MemberKind.LEADER: 0, MemberKind.STUDENT: 1, MemberKind.GUEST: 2}


def members_of(delegation, *, sync: bool = True, request=None) -> list[DelegationMember]:
    """Członkowie delegacji: opiekunowie, uczniowie, goście – w tej kolejności, potem nazwiska.

    To jest też **interfejs dla płatności delegacji (PAY-01)**: lista osób, za które delegacja płaci,
    bez żadnych danych pobytu poza tym, co niesie wiersz (rola, imię, nazwisko).
    """
    if sync:
        _sync_once(delegation, request)
    members = list(_member_queryset().filter(delegation=delegation))
    members.sort(key=lambda m: (KIND_ORDER.get(m.kind, 9), m.last_name.lower(), m.first_name.lower(), m.pk))
    return members


def edition_members(edition, *, sync: bool = True, request=None) -> list[DelegationMember]:
    """Członkowie wszystkich delegacji edycji – materiał zestawień oficera."""
    from apps.accounts.delegations import Delegation

    delegations = list(Delegation.objects.filter(edition=edition).select_related("country"))
    if sync:
        for delegation in delegations:
            _sync_once(delegation, request)
    members = list(_member_queryset().filter(delegation__edition=edition))
    members.sort(
        key=lambda m: (
            m.delegation.country.name.lower(),
            KIND_ORDER.get(m.kind, 9),
            m.last_name.lower(),
            m.first_name.lower(),
            m.pk,
        )
    )
    return members


def member_for_leader(leader, pk: int) -> DelegationMember:
    """Członek **delegacji tego opiekuna** albo 404 – zakres z querysetu, nie z warunku w widoku."""
    member = _member_queryset().filter(delegation=leader.delegation, pk=pk).first()
    if member is None:
        raise Http404("Nie ma takiej osoby w tej delegacji.")
    return member


def member_for_competition(competition, pk: int, edition=None) -> DelegationMember:
    """Członek delegacji **tego konkursu** (i bieżącej edycji, gdy podana) albo 404 – ekrany oficera."""
    members = _member_queryset().for_competition(competition)
    if edition is not None:
        members = members.filter(delegation__edition=edition)
    member = members.filter(pk=pk).first()
    if member is None:
        raise Http404("Nie ma takiej osoby w delegacjach tego konkursu.")
    return member


# --- kompletność --------------------------------------------------------------------------------------


def _filled(member: DelegationMember, field: str) -> bool:
    value = getattr(member, field)
    return value not in (None, "")


def missing_groups(member: DelegationMember, groups: list[str]) -> list[str]:
    """Grupy, w których brakuje pól wymaganych. Zdjęcie należy do grupy „identyfikator”.

    Zakwaterowanie bez potrzeby noclegu jest kompletne bez płci – płeć służy wyłącznie przydziałowi
    pokoi, a kto nie nocuje, nie dostaje pokoju. „Zdrowie” jest kompletne dopiero z dietą (choćby
    „bez ograniczeń”) – inaczej kuchnia nie wie, czy brak odpowiedzi znaczy „nic”, czy „zapomniałem”.
    """
    missing = []
    for group in groups:
        required = REQUIRED_FIELDS[group]
        if group == FieldGroup.ACCOMMODATION and not member.needs_accommodation:
            required = ()
        incomplete = any(not _filled(member, field) for field in required)
        if group == FieldGroup.PERSONAL and not member.photo_key:
            incomplete = True
        if incomplete:
            missing.append(group)
    return missing


# --- zapis formularza -----------------------------------------------------------------------------------


def _locked_error(group: str) -> DomainError:
    return DomainError(
        _("Termin na uzupełnienie sekcji „%(group)s” minął – zmiany wprowadza już tylko organizator.")
        % {"group": FieldGroup(group).label},
        "LOGISTICS_GROUP_LOCKED",
        status.HTTP_409_CONFLICT,
    )


def _normalise(field: str, value):
    """Wartość z formularza w postaci modelu (daty szyfrowane jako ISO, teksty bez spacji wokół)."""
    if field in ENCRYPTED_DATES:
        if isinstance(value, (date, datetime)):
            return value.isoformat()[:10]
        return (value or "").strip()
    if field == "nationality":
        return (value or "").strip().upper()
    if isinstance(value, str):
        return value.strip()
    return value


def _validate(member: DelegationMember, event: FinalEvent | None, changed: list[str]) -> None:
    errors = []
    if member.nationality and not NATIONALITY_RE.match(member.nationality):
        errors.append(_("Obywatelstwo podaj dwuliterowym kodem kraju (np. DE, PL)."))
    for field in ENCRYPTED_DATES:
        raw = getattr(member, field)
        if raw:
            try:
                date.fromisoformat(raw)
            except ValueError:
                errors.append(_("Niepoprawna data."))
    if member.passport_expiry and "passport_expiry" in changed:
        try:
            expiry = date.fromisoformat(member.passport_expiry)
        except ValueError:
            expiry = None
        if expiry is not None:
            limit = event.ends_on if event is not None and event.ends_on else timezone.localdate()
            if expiry < limit:
                errors.append(
                    _("Paszport traci ważność przed końcem finału – podaj dokument ważny w czasie pobytu.")
                )
    if member.arrival_date and member.departure_date and member.departure_date < member.arrival_date:
        errors.append(_("Wyjazd nie może być przed przyjazdem."))
    if errors:
        raise DomainError(
            " ".join(str(error) for error in errors), "LOGISTICS_INVALID", status.HTTP_400_BAD_REQUEST
        )


@transaction.atomic
def save_member(
    member: DelegationMember,
    data: dict,
    *,
    actor,
    as_officer: bool = False,
    health_consent: bool = False,
    request=None,
    now=None,
) -> list[str]:
    """Zapisuje dane pobytu jednej osoby. Zwraca nazwy zmienionych pól.

    Reguły w kolejności:

    1. grupa „Zdrowie” istnieje wyłącznie przy decyzji D21 – bez niej jej pola są **odrzucane**
       (nie trafiają do bazy nawet wtedy, gdy ktoś dopisze je do żądania),
    2. grupa po terminie: opiekun nie może zmienić **żadnego** jej pola (odmowa całego zapisu, żeby
       nie zapisać połowy formularza); oficer – może, z wpisem audytowym jak każdy,
    3. dane o zdrowiu wymagają wyraźnej zgody (art. 9 ust. 2 lit. a RODO): niepuste pole zdrowia
       bez zapisanej i bez właśnie złożonej zgody jest odmową. Zgoda zapisuje się z osobą, chwilą
       i wersją tekstu,
    4. walidacja (obywatelstwo, dieta z listy, daty, ważność paszportu w czasie finału, wyjazd po
       przyjeździe); dane paszportowe i o zdrowiu wyłącznie przy znanym końcu finału (retencja),
    5. po retencji – odmowa dla wszystkich, także oficera,
    6. zapis **wyłącznie zmienionych kolumn** (``update_fields``): pole, którego nie dało się
       odszyfrować (klucz bez fallbacku), czyta się jako pusty napis – i nie może tym pustym napisem
       nadpisać szyfrogramu, który jeszcze da się odzyskać starym kluczem,
    7. osoba z pokojem, której zmieniła się płeć, data urodzenia albo potrzeba noclegu, jest
       sprawdzana ponownie (``rooming.recheck_member``) – niepasująca traci przydział, a w wyniku
       pojawia się ``"room"``.
    """
    competition = member.delegation.competition
    require_not_purged(member.delegation.edition)
    event = event_for(member.delegation.edition)
    now = now or timezone.now()
    allowed_groups = groups_for(competition)
    member = (
        DelegationMember.objects.select_for_update(of=("self",))
        .select_related("delegation__competition", "participant__user", "user", "guest")
        .get(pk=member.pk)
    )
    changed: list[str] = []
    for group in allowed_groups:
        for field in GROUP_FIELDS[group]:
            if field not in data:
                continue
            value = _normalise(field, data[field])
            if getattr(member, field) == value:
                continue
            if not as_officer and group_locked(event, group, now):
                raise _locked_error(group)
            if field == "diet" and value not in ("", *Diet.values):
                raise DomainError(_("Wybierz dietę z listy."), "DIET_INVALID", status.HTTP_400_BAD_REQUEST)
            if group in (FieldGroup.IDENTITY, FieldGroup.HEALTH) and value not in ("", None):
                require_retention_anchor(event)
            setattr(member, field, value)
            changed.append(field)
    if FieldGroup.HEALTH in allowed_groups:
        # „Bez ograniczeń” nie mówi niczego o zdrowiu ani o wyznaniu – zgody nie potrzebuje.
        # Każda inna dieta (halal, koszerna, bezglutenowa) już tak: art. 9 obejmuje i zdrowie,
        # i przekonania religijne.
        has_health = any(_filled(member, field) for field in HEALTH_FIELDS if field != "diet") or (
            member.diet not in ("", None, Diet.NONE)
        )
        if health_consent and member.health_consent_at is None:
            member.health_consent_at = now
            member.health_consent_by = actor if getattr(actor, "is_authenticated", False) else None
            member.health_consent_version = HEALTH_CONSENT_VERSION
            changed.append("health_consent")
        if has_health and member.health_consent_at is None:
            raise DomainError(
                _(
                    "Dane o wyżywieniu i zdrowiu zapiszemy dopiero po zaznaczeniu zgody osoby (albo jej "
                    "rodzica, jeśli jest niepełnoletnia) na ich przetwarzanie."
                ),
                "HEALTH_CONSENT_REQUIRED",
                status.HTTP_400_BAD_REQUEST,
            )
    if not changed:
        return []
    _validate(member, event, changed)
    member.updated_at = now
    columns = [field for field in changed if field != "health_consent"] + ["updated_at"]
    if "health_consent" in changed:
        columns += ["health_consent_at", "health_consent_by", "health_consent_version"]
    member.save(update_fields=columns)
    audit(
        actor,
        "logistics.member_updated",
        member,
        {"fields": changed, "delegation": member.delegation_id, "by_officer": as_officer},
        request=request,
    )
    if member.room_id and set(changed) & {"gender", "date_of_birth", "needs_accommodation"}:
        from .rooming import recheck_member

        if recheck_member(member, actor=actor, request=request):
            changed.append("room")
    return changed


@transaction.atomic
def withdraw_health_consent(member: DelegationMember, *, actor, request=None) -> None:
    """Cofnięcie zgody na dane o zdrowiu: zgoda i **wszystkie** dane zdrowia znikają od razu.

    Pod blokadą wiersza i wyłącznie kolumny zdrowia (``update_fields``) – równoległy zapis formularza
    nie może przywrócić alergii, a pola innych grup nie są przepisywane (L3).
    """
    member = DelegationMember.objects.select_for_update().get(pk=member.pk)
    for field in HEALTH_FIELDS:
        setattr(member, field, "")
    member.health_consent_at = None
    member.health_consent_by = None
    member.health_consent_version = ""
    member.updated_at = timezone.now()
    member.save(
        update_fields=[
            *HEALTH_FIELDS,
            "health_consent_at",
            "health_consent_by",
            "health_consent_version",
            "updated_at",
        ]
    )
    audit(actor, "logistics.health_consent_withdrawn", member, {}, request=request)


# --- goście -----------------------------------------------------------------------------------------


def _require_guest_editable(delegation, as_officer: bool) -> None:
    """Skład gości zamyka się razem z dokumentem podróży (L2): po tym terminie lista osób do listów
    wizowych i identyfikatorów jest ostateczna, a zmiany wprowadza oficer."""
    require_not_purged(delegation.edition)
    if not as_officer and group_locked(event_for(delegation.edition), FieldGroup.IDENTITY):
        raise _locked_error(FieldGroup.IDENTITY)


@transaction.atomic
def add_guest(
    delegation,
    *,
    first_name: str,
    last_name: str,
    email: str = "",
    role: str,
    actor,
    as_officer: bool = False,
    request=None,
):
    """Dodaje gościa (obserwatora) do delegacji – wiersz gościa i jego wiersz członka razem."""
    from apps.accounts.delegations import Delegation

    Delegation.objects.select_for_update().filter(pk=delegation.pk).values_list("pk", flat=True).first()
    _require_guest_editable(delegation, as_officer)
    if role not in GuestRole.values:
        raise DomainError(_("Wybierz rolę z listy."), "GUEST_ROLE_INVALID", status.HTTP_400_BAD_REQUEST)
    first_name, last_name = (first_name or "").strip(), (last_name or "").strip()
    if not first_name or not last_name:
        raise DomainError(_("Podaj imię i nazwisko."), "GUEST_NAME_REQUIRED", status.HTTP_400_BAD_REQUEST)
    if DelegationGuest.objects.filter(delegation=delegation).count() >= MAX_GUESTS:
        raise DomainError(
            _("Delegacja może mieć najwyżej %(count)s gości.") % {"count": MAX_GUESTS},
            "GUEST_LIMIT",
            status.HTTP_409_CONFLICT,
        )
    guest = DelegationGuest.objects.create(
        delegation=delegation,
        first_name=first_name[:150],
        last_name=last_name[:150],
        email=(email or "").strip(),
        role=role,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )
    member = DelegationMember.objects.create(delegation=delegation, kind=MemberKind.GUEST, guest=guest)
    audit(
        actor, "logistics.guest_added", member, {"delegation": delegation.pk, "role": role}, request=request
    )
    return member


@transaction.atomic
def update_guest(
    member: DelegationMember,
    *,
    first_name: str,
    last_name: str,
    email: str,
    role: str,
    actor,
    as_officer: bool = False,
    request=None,
):
    if member.kind != MemberKind.GUEST:
        raise Http404("To nie jest gość delegacji.")
    _require_guest_editable(member.delegation, as_officer)
    if role not in GuestRole.values:
        raise DomainError(_("Wybierz rolę z listy."), "GUEST_ROLE_INVALID", status.HTTP_400_BAD_REQUEST)
    guest = member.guest
    guest.first_name = (first_name or "").strip()[:150] or guest.first_name
    guest.last_name = (last_name or "").strip()[:150] or guest.last_name
    guest.email = (email or "").strip()
    guest.role = role
    guest.save()
    audit(actor, "logistics.guest_updated", member, {"role": role}, request=request)
    return member


@transaction.atomic
def remove_guest(member: DelegationMember, *, actor, request=None) -> None:
    """Usuwa gościa z delegacji razem z jego danymi i zdjęciem."""
    if member.kind != MemberKind.GUEST:
        raise Http404("To nie jest gość delegacji.")
    audit(actor, "logistics.guest_removed", member, {"delegation": member.delegation_id}, request=request)
    delete_members(DelegationMember.objects.filter(pk=member.pk))


# --- zdjęcie do identyfikatora ------------------------------------------------------------------------


def _photo_format(upload) -> tuple[str, str]:
    upload.seek(0)
    header = upload.read(8)
    upload.seek(0)
    for ext, (magic, mime) in PHOTO_FORMATS.items():
        if header.startswith(magic):
            return ext, mime
    raise DomainError(
        _("Zdjęcie musi być plikiem JPG albo PNG."), "PHOTO_FORMAT", status.HTTP_400_BAD_REQUEST
    )


def _unreadable_photo() -> DomainError:
    return DomainError(
        _("Nie udało się odczytać zdjęcia – wgraj inny plik JPG lub PNG."),
        "PHOTO_UNREADABLE",
        status.HTTP_400_BAD_REQUEST,
    )


def _check_dimensions(upload) -> None:
    """Sam nagłówek obrazu (bez dekodowania pikseli): format zgodny z sygnaturą i limit pikseli."""
    from PIL import Image, UnidentifiedImageError

    upload.seek(0)
    try:
        with Image.open(upload) as image:
            fmt, (width, height) = image.format, image.size
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise _unreadable_photo() from exc
    finally:
        upload.seek(0)
    if fmt not in ("JPEG", "PNG"):
        raise _unreadable_photo()
    if width * height > PHOTO_MAX_PIXELS:
        raise DomainError(
            _("Zdjęcie ma zbyt dużą rozdzielczość."), "PHOTO_TOO_MANY_PIXELS", status.HTTP_400_BAD_REQUEST
        )


def reencode_photo(data: bytes) -> bytes:
    """Przekodowanie zdjęcia po czystym skanie: obrót z EXIF-u, RGB, najwyżej 600×800, JPEG bez metadanych.

    Dekodujemy dopiero plik, który przeszedł ClamAV, i tylko po sprawdzeniu liczby pikseli w nagłówku.
    Wynik nie niesie niczego z oryginału poza pikselami – EXIF (GPS, aparat, data) znika.
    """
    from io import BytesIO

    from PIL import Image, ImageOps

    with Image.open(BytesIO(data)) as source:
        if source.size[0] * source.size[1] > PHOTO_MAX_PIXELS:
            raise ValueError("za dużo pikseli")
        image = ImageOps.exif_transpose(source)
        image = image.convert("RGB")
        image.thumbnail(PHOTO_MAX_SIZE)
        out = BytesIO()
        image.save(out, format="JPEG", quality=PHOTO_JPEG_QUALITY, optimize=True)
    return out.getvalue()


def _size(upload) -> int:
    size = getattr(upload, "size", None)
    if size is None:
        upload.seek(0, 2)
        size = upload.tell()
        upload.seek(0)
    return int(size)


def _sha256(upload) -> str:
    digest = hashlib.sha256()
    upload.seek(0)
    for chunk in iter(lambda: upload.read(1024 * 1024), b""):
        digest.update(chunk)
    upload.seek(0)
    return digest.hexdigest()


@transaction.atomic
def upload_photo(
    member: DelegationMember, upload, *, actor, as_officer: bool = False, request=None
) -> DelegationMember:
    """Przyjmuje zdjęcie do identyfikatora: format po treści, limit, storage, skan ClamAV.

    Zdjęcie pokazujemy (identyfikator, ekran skanowania) **dopiero po czystym skanie**. Poprzednie
    zdjęcie znika ze storage po commicie – trzymanie dwóch zdjęć tej samej osoby nie ma celu.
    """
    require_not_purged(member.delegation.edition)
    event = event_for(member.delegation.edition)
    if not as_officer and group_locked(event, FieldGroup.PERSONAL):
        raise _locked_error(FieldGroup.PERSONAL)
    size = _size(upload)
    if size <= 0:
        raise DomainError(_("Plik jest pusty."), "PHOTO_EMPTY", status.HTTP_400_BAD_REQUEST)
    if size > PHOTO_MAX_BYTES:
        raise DomainError(
            _("Zdjęcie może mieć najwyżej 5 MB."), "PHOTO_TOO_LARGE", status.HTTP_400_BAD_REQUEST
        )
    ext, mime = _photo_format(upload)
    _check_dimensions(upload)
    sha = _sha256(upload)
    locked = DelegationMember.objects.select_for_update().get(pk=member.pk)
    previous = locked.photo_key
    key = (
        "/".join(
            [
                PHOTO_PREFIX,
                sanitize_segment(member.delegation.competition_id),
                sanitize_segment(member.delegation.edition_id),
                sanitize_segment(locked.pk),
                sanitize_segment(uuid.uuid4().hex),
            ]
        )
        + f"/{sha}.{ext}"
    )
    get_submission_storage().put(key, upload, mime)
    locked.photo_key = key
    locked.photo_mime = mime
    locked.photo_size = size
    locked.photo_scan = ScanStatus.PENDING
    locked.photo_uploaded_at = timezone.now()
    locked.save(update_fields=["photo_key", "photo_mime", "photo_size", "photo_scan", "photo_uploaded_at"])
    if previous and previous != key:
        _delete_photos_on_commit([previous])

    def _enqueue(pk: int = locked.pk, object_key: str = key) -> None:
        from .tasks import scan_badge_photo

        scan_badge_photo.delay(pk, object_key)

    transaction.on_commit(_enqueue)
    audit(actor, "logistics.photo_uploaded", locked, {"size": size}, request=request)
    return locked


def _store_reencoded(member: DelegationMember, key: str) -> str | None:
    """Czysty oryginał → przekodowany JPEG pod nowym kluczem. ``None``, gdy obrazu nie da się odczytać."""
    from apps.submissions.tasks import MissingStorageObject, _open_object

    storage = get_submission_storage()
    try:
        stream = _open_object(storage, key)
    except MissingStorageObject:
        return None
    try:
        data = stream.read()
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    try:
        jpeg = reencode_photo(data)
    except Exception:  # noqa: BLE001 - Pillow zgłasza wiele klas błędów; skutek jest jeden: plik odrzucony
        logger.warning("Zdjęcie identyfikatora %s nie dało się przekodować – odrzucone.", member.pk)
        return None
    from io import BytesIO

    new_key = key.rsplit("/", 1)[0] + f"/{hashlib.sha256(jpeg).hexdigest()}.jpg"
    storage.put(new_key, BytesIO(jpeg), "image/jpeg")
    member.photo_size = len(jpeg)
    member.photo_mime = "image/jpeg"
    return new_key


def apply_photo_scan(pk: int, key: str, verdict: str) -> DelegationMember | None:
    """Zapisuje werdykt skanu – o ile zdjęcie nie zostało w międzyczasie podmienione.

    Czysty plik jest przekodowywany (:func:`reencode_photo`) i dopiero przekodowany JPEG staje się
    zdjęciem osoby; oryginał znika ze storage. Plik, którego Pillow nie odczyta, kończy jak błąd skanu.
    """
    with transaction.atomic():
        member = DelegationMember.objects.select_for_update().filter(pk=pk, photo_key=key).first()
        if member is None or member.photo_scan != ScanStatus.PENDING:
            return member
        if verdict == ScanStatus.CLEAN:
            new_key = _store_reencoded(member, key)
            if new_key is not None:
                member.photo_scan = ScanStatus.CLEAN
                member.photo_key = new_key
                member.save(update_fields=["photo_scan", "photo_key", "photo_size", "photo_mime"])
                if new_key != key:
                    _delete_photos_on_commit([key])
                return member
            verdict = ScanStatus.ERROR
        # Zainfekowane albo nieczytelne – plik znika, opiekun wgrywa nowe zdjęcie.
        member.photo_scan = verdict if verdict in ScanStatus.values else ScanStatus.ERROR
        member.photo_key = ""
        member.save(update_fields=["photo_scan", "photo_key"])
        audit(None, "logistics.photo_rejected", member, {"verdict": member.photo_scan})
        _delete_photos_on_commit([key])
    return member


def open_photo(member: DelegationMember):
    """Strumień **czystego** zdjęcia albo ``None`` (brak, przed skanem, zniknęło ze storage)."""
    from apps.submissions.tasks import MissingStorageObject, _open_object

    if not member.photo_clean:
        return None
    try:
        return _open_object(get_submission_storage(), member.photo_key)
    except MissingStorageObject:
        logger.warning("Brak zdjęcia identyfikatora członka %s w storage.", member.pk)
        return None


def photo_bytes(member: DelegationMember) -> bytes | None:
    stream = open_photo(member)
    if stream is None:
        return None
    try:
        return stream.read()
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()
