"""Publiczna weryfikacja listu zapraszającego po kodzie z papieru (VISA-01 § 5).

Strona ``/visa/verify/<kod>/`` jest dla konsulatu: urzędnik skanuje QR albo przepisuje kod i ma się
dowiedzieć, czy list jest prawdziwy i nadal ważny. Słownik, a nie obiekt ``InvitationLetter``, z tego
samego powodu, co ``apps.results.certificates.verify``: strona jest publiczna, więc **tu** zapada
decyzja, co opuszcza system. Szablon, który dostałby wiersz rejestru, mógłby sięgnąć po migawkę
z numerami paszportów przy pierwszej nieuważnej zmianie.

Co wychodzi: numer, data wystawienia, stan (ważny / unieważniony z datą), wydarzenie z migawki (nazwa,
miasto, daty) oraz imię i nazwisko z paszportu i obywatelstwo każdej osoby z listu. Czego nie ma:
numeru i ważności paszportu, daty urodzenia, roli, powodu unieważnienia, kto wystawił. Imię i nazwisko
**jest**, inaczej niż na stronie dyplomu: konsulat musi dopasować list do osoby przed okienkiem, a kod
zna wyłącznie ten, komu list pokazano (59 bitów losu i limit żądań – zgadywanie nie ma sensu).

Zakres konkursu żądania: kod listu innego konkursu daje „nie znaleziono”, tak samo jak kod zmyślony.
"""

from __future__ import annotations

import re

from django.utils import timezone

from apps.accounts.countries import country_name

from .letters import people_of
from .models import InvitationLetter

#: Kod normalizujemy do samych liter i cyfr: na papierze stoi ``ABCD-EFGH-JKMN``, przez telefon
#: dyktuje się go ze spacjami, a w skanie OCR potrafi dołożyć kropkę.
NON_CODE = re.compile(r"[^A-Z0-9]")


def normalise(code: str) -> str:
    return NON_CODE.sub("", (code or "").upper())[:32]


def verify(competition, code: str) -> dict | None:
    """Dane strony weryfikacji albo ``None``, gdy w tym konkursie takiego listu nie ma."""
    cleaned = normalise(code)
    if not cleaned or competition is None:
        return None
    letter = (
        InvitationLetter.objects.for_competition(competition)
        .filter(verification_code=cleaned)
        .select_related("edition")
        .first()
    )
    if letter is None:
        return None
    people = [
        {
            "name": person.get("name", ""),
            "nationality": country_name((person.get("nationality") or "").lower())
            or (person.get("nationality") or ""),
        }
        for person in people_of(letter)
    ]
    return {
        "number": letter.number,
        "code": letter.display_code,
        "issued_on": timezone.localtime(letter.issued_at).date(),
        "valid": letter.revoked_at is None,
        "revoked_on": timezone.localtime(letter.revoked_at).date() if letter.revoked_at else None,
        "event_name": letter.event_name,
        "event_city": letter.event_city,
        "event_starts_on": letter.event_starts_on,
        "event_ends_on": letter.event_ends_on,
        "people": people,
        # Migawka usunięta (retencja, usunięcie konta) – list był, ale danych osób już nie ma. Strona
        # mówi to wprost, żeby pusta lista nie wyglądała na wadę dokumentu.
        "purged": not people,
    }
