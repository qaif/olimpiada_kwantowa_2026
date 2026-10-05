"""Zapis zgód uzupełnianych po zalogowaniu (``docs/tasks/CONS-01.md`` § 3).

Dlaczego nie ``accounts.services.record_consents``: tamten serwis jest zapisem **kompletu** zgód
z formularza rejestracji i przepisuje wszystkie projekcje na profilu – także ``guardian_consent``
i ``publish_full_name`` na wartość z formularza. Wołany z ekranu, który pokazuje wyłącznie brakujące
zgody, wyzerowałby zgodę na publikację nazwiska i potwierdzenie opiekuna, których ten ekran nie
dotyczy. Tu zapisujemy **tylko** braki i projekcje przestawiamy wyłącznie „w górę”.

Dowód jest ten sam, co przy zgodzie opiekuna online: ``ConsentRecord`` z wersją dokumentu, czasem,
drogą (``panel``) i adresem IP (``client_ip`` – reguła zaufania do proxy wspólna z audytem). Język
interfejsu i skrót SHA-256 treści, którą uczestnik widział, idą do **jednego** wpisu audytu
``participant.consents_completed`` – ``ConsentRecord`` nie ma na nie kolumn, a dokładanie ich
wspólnemu modelowi dla jednej drogi zapisu byłoby migracją bez drugiego czytelnika.
"""

from __future__ import annotations

import hashlib

from django.db import transaction
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from rest_framework import status

from apps.accounts.consents import ConsentKind, ConsentSource, organizer_name, plain_text
from apps.accounts.models import ConsentRecord, Participant
from apps.core.api import DomainError
from apps.core.models import audit, client_ip

from . import state


def text_digest(consent, organizer: str) -> str:
    """SHA-256 treści oświadczenia **bez znaczników** – tej, którą czyta człowiek przy polu wyboru.

    Bez adresu odnośnika: dokument bywa przeniesiony w drzewie stron albo dostaje PDF, a treść
    oświadczenia się przez to nie zmienia. Skrót ma odpowiadać na pytanie „jakie zdanie
    zaakceptowano”, a nie „pod jakim adresem wisiał wtedy plik”.
    """
    return hashlib.sha256(plain_text(consent, organizer=organizer).encode("utf-8")).hexdigest()


def participant_missing(participant: Participant):
    """``(brakujące zgody, pary (rodzaj, wersja) aktywnych wpisów)`` – liczone z bazy, bez cache'a.

    Ekran i zapis czytają stan wprost: cache służy bramce, a tu decyzja ma zapaść na danych
    z tej chwili (podwójne kliknięcie, potwierdzenie opiekuna przed sekundą).
    """
    records = frozenset(
        ConsentRecord.objects.filter(participant=participant, withdrawn_at__isnull=True)
        .order_by()
        .values_list("kind", "document_version")
    )
    consents = state.consents_for(participant.competition)
    return state.missing_from(consents, participant.birth_date, participant.birth_year, records), records


@transaction.atomic
def complete_consents(
    participant: Participant, given_kinds: set[str], *, request=None
) -> list[ConsentRecord]:
    """Zapisuje brakujące zgody uczestnika. Odmowa, gdy któraś z brakujących nie jest zaznaczona.

    Blokada wiersza profilu i ponowne policzenie braków w transakcji: drugi, równoległy POST tego
    samego formularza nie dopisze drugiego kompletu wpisów, tylko zobaczy, że nie ma już czego
    uzupełniać. Pusta lista braków nie jest błędem – zwracamy pustą listę.
    """
    locked = Participant.objects.select_for_update().select_related("competition").get(pk=participant.pk)
    missing, records = participant_missing(locked)
    if not missing:
        return []
    for consent in missing:
        if consent.kind not in given_kinds:
            raise DomainError(
                consent.missing_message or _("Ta zgoda jest wymagana."),
                "CONSENT_REQUIRED",
                status.HTTP_400_BAD_REQUEST,
            )

    now = timezone.now()
    ip = client_ip(request)
    created = ConsentRecord.objects.bulk_create(
        [
            ConsentRecord(
                participant=locked,
                kind=consent.kind,
                document_version=consent.version,
                given_at=now,
                source=ConsentSource.PANEL,
                ip_address=ip,
            )
            for consent in missing
        ]
    )

    # Projekcje wyłącznie „w górę”: ten ekran nie zbiera zgód dobrowolnych i nie cofa niczego.
    fields = []
    kinds = {consent.kind for consent in missing}
    if ConsentKind.TERMS in kinds:
        locked.terms_accepted_at = now
        fields.append("terms_accepted_at")
    if ConsentKind.PRIVACY in kinds:
        locked.gdpr_consent_at = now
        fields.append("gdpr_consent_at")
    if ConsentKind.GUARDIAN in kinds and not locked.guardian_consent:
        locked.guardian_consent = True
        fields.append("guardian_consent")
    if fields:
        locked.save(update_fields=fields)

    organizer = organizer_name(locked.competition)
    previous = state.previous_versions(records)
    audit(
        locked.user,
        "participant.consents_completed",
        locked,
        {
            "source": ConsentSource.PANEL,
            "language": get_language() or "",
            "consents": {
                consent.kind: {
                    "version": consent.version,
                    "text_sha256": text_digest(consent, organizer),
                    "previous_version": previous.get(consent.kind, []),
                }
                for consent in missing
            },
        },
        request=request,
    )
    # ``bulk_create`` nie wysyła sygnałów, a projekcji mogło nie być do zapisania (np. sama zgoda
    # opiekuna przy ``guardian_consent`` już ``True``) – unieważniamy stan wprost.
    state.forget_state(locked.competition_id, locked.user_id)
    transaction.on_commit(lambda: state.forget_state(locked.competition_id, locked.user_id))
    return created
