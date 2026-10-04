"""RODO logistyki finału: retencja, usunięcie konta, eksport danych osoby, rejestr czynności.

**Retencja** (:func:`purge_expired`) jest krótka i twarda: po ``ends_on + retention_days`` (domyślnie
30 dni) znikają wszystkie wiersze członków delegacji edycji – paszporty, dane o zdrowiu, kontakty
alarmowe, przyloty, pokoje, odhaczenia – razem ze zdjęciami w storage, a z rejestru listów
zapraszających znikają migawki osób (numer, kraj, data i liczba osób zostają: rejestr pism wychodzących
jest dokumentacją organizatora, a nie danymi osób). To jest inny termin niż retencja danych uczestników
(``accounts.retention``) i ma być inny: dane konta służą wynikom i dyplomom przez lata, a numer
paszportu – wyłącznie temu jednemu wyjazdowi.

**Usunięcie konta** (:func:`erase_for_user`) woła ``apps.accounts.delegation_services.erase_for_user``
– jedno wejście dla anonimizacji i dla skasowania konta (``accounts.profile``), już wołane w obu
drogach. Goście nie mają kont: ich dane usuwa opiekun albo oficer (``services.remove_guest``) i retencja.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import DelegationMember, FinalEvent, InvitationLetter

logger = logging.getLogger(__name__)


@transaction.atomic
def purge_event(event: FinalEvent, *, now=None) -> int:
    """Usuwa dane członków delegacji jednej edycji i migawki listów. Zwraca liczbę usuniętych osób."""
    from apps.core.models import audit

    from .services import delete_members

    moment = now or timezone.now()
    removed = delete_members(DelegationMember.objects.filter(delegation__edition=event.edition))
    letters = InvitationLetter.objects.filter(edition=event.edition, content_purged_at__isnull=True)
    purged_letters = letters.update(content="", content_purged_at=moment)
    # Powód unieważnienia bywa opisem osoby („odmowa wizy”) – po retencji znika (VISA-01 L3).
    InvitationLetter.objects.filter(edition=event.edition).exclude(revoke_reason="").update(revoke_reason="")
    event.purged_at = moment
    event.save(update_fields=["purged_at"])
    audit(None, "logistics.purged", event, {"members": removed, "letters": purged_letters})
    logger.info(
        "Retencja logistyki finału: edycja %s – usunięto %s wierszy, wyczyszczono %s listów.",
        event.edition_id,
        removed,
        purged_letters,
    )
    return removed


def purge_expired(now=None) -> int:
    """Zadanie dobowe: finały, którym minął termin retencji, a dane jeszcze są."""
    today = timezone.localdate(now) if now else timezone.localdate()
    total = 0
    for event in FinalEvent.objects.filter(purged_at__isnull=True, ends_on__isnull=False).select_related(
        "edition"
    ):
        due = event.purge_due_on()
        if due is not None and due < today:
            total += purge_event(event, now=now)
    return total


def _members_of_user(user):
    return DelegationMember.objects.filter(Q(participant__user=user) | Q(user=user))


def erase_for_user(user) -> int:
    """Usunięcie albo anonimizacja konta: dane pobytu tej osoby, jej zdjęcie i jej wiersze w listach."""
    from .services import delete_members

    # Migawki listów sprząta ``delete_members`` – ta sama droga dla każdego usunięcia członka (M4).
    return delete_members(_members_of_user(user))


def export_section(user) -> list[dict]:
    """Sekcja eksportu danych konta (art. 15 i 20 RODO): dane pobytu **tej** osoby, odszyfrowane.

    Osoba ma prawo zobaczyć, co o niej wpisał opiekun drużyny – łącznie z numerem paszportu i danymi
    o zdrowiu. Plik dostaje wyłącznie ona sama (eksport jest z jej konta).
    """
    rows = []
    for member in _members_of_user(user).select_related(
        "delegation__country", "delegation__competition", "room"
    ):
        rows.append(
            {
                "konkurs": member.delegation.competition.slug,
                "delegacja": member.delegation.country.name,
                "rola": str(member.role_label),
                "imie_nazwisko_z_paszportu": member.passport_name,
                "obywatelstwo": member.nationality,
                "data_urodzenia": member.date_of_birth,
                "numer_paszportu": member.passport_number,
                "paszport_wazny_do": member.passport_expiry,
                "przyjazd": {
                    "dzien": member.arrival_date.isoformat() if member.arrival_date else None,
                    "godzina": member.arrival_time.strftime("%H:%M") if member.arrival_time else None,
                    "srodek": member.arrival_mode,
                    "numer": member.arrival_number,
                    "miejsce": member.arrival_place,
                },
                "wyjazd": {
                    "dzien": member.departure_date.isoformat() if member.departure_date else None,
                    "godzina": member.departure_time.strftime("%H:%M") if member.departure_time else None,
                    "srodek": member.departure_mode,
                    "numer": member.departure_number,
                    "miejsce": member.departure_place,
                },
                "nocleg": member.needs_accommodation,
                "plec": member.gender,
                "preferencja_wspollokatora": member.roommate_preference,
                "uwagi_zakwaterowanie": member.accommodation_notes,
                "pokoj": str(member.room) if member.room_id else None,
                "dieta": member.diet,
                "uwagi_dieta": member.diet_notes,
                "alergie": member.allergies,
                "uwagi_medyczne": member.medical_notes,
                "zgoda_dane_o_zdrowiu": (
                    timezone.localtime(member.health_consent_at).isoformat()
                    if member.health_consent_at
                    else None
                ),
                "rozmiar_koszulki": member.tshirt_size,
                "kontakt_alarmowy": member.emergency_name,
                "telefon_alarmowy": member.emergency_phone,
                "zdjecie_do_identyfikatora": bool(member.photo_key),
                # Obecność i listy wizowe – też dane o tej osobie (L11).
                "odhaczenia": [
                    {"punkt": row.checkpoint.name, "czas": timezone.localtime(row.at).isoformat()}
                    for row in member.check_ins.select_related("checkpoint").order_by("at")
                ],
                "listy_zapraszajace": _letters_of(member),
                "wnioski_o_list_zapraszajacy": letter_requests_section(member),
            }
        )
    return rows


def letter_requests_section(member) -> list[dict]:
    """Wnioski o list zapraszający tej osoby (VISA-01): stan, język, daty, powód odrzucenia, numer listu.

    Bez tożsamości opiekuna ani oficera – to dane pracowników organizatora i opiekuna, nie tej osoby
    (ta sama granica, co „Recenzent A/B” przy ocenach). Dane z samego listu są już w sekcji wyżej.
    """

    def moment(value):
        return timezone.localtime(value).isoformat() if value else None

    return [
        {
            "stan": row.status,
            "jezyk_listu": row.language,
            "zlozony": moment(row.requested_at),
            "rozstrzygniety": moment(row.decided_at),
            "powod_odrzucenia": row.reject_reason or None,
            "numer_listu": row.letter.number if row.letter_id else None,
            "list_uniewazniony": bool(row.letter_id and row.letter.revoked_at),
        }
        for row in member.letter_requests.select_related("letter").order_by("requested_at", "id")
    ]


def _letters_of(member) -> list[dict]:
    """Listy, na których ta osoba jest (imienne i delegacji) – numer, data i jej wiersz z migawki."""
    from .letters import people_of

    result = []
    for letter in InvitationLetter.objects.filter(delegation_id=member.delegation_id).order_by("issued_at"):
        mine = [person for person in people_of(letter) if person.get("member_id") == member.pk]
        if mine:
            result.append(
                {
                    "numer": letter.number,
                    "wystawiono": timezone.localtime(letter.issued_at).isoformat(),
                    "dane_na_liscie": mine[0],
                }
            )
    return result
