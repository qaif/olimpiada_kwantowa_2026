"""Kształt ładunku każdego zdarzenia webhooka – jedno miejsce na cały kontrakt wychodzący.

Po co osobny moduł, skoro ``emit()`` przyjmuje dowolny słownik: bo ten słownik **jest** kontraktem
z odbiorcą. Gdyby powstawał przy każdym wołaniu w serwisie domenowym, pola rozjechałyby się przy
pierwszej zmianie w którymkolwiek z czterech miejsc, a zmiana kontraktu byłaby niewidoczna
w przeglądzie kodu – wyglądałaby jak poprawka w publikacji wyników.

Dzięki temu wpięcie w serwis domenowy to dwie linijki (import i wołanie), a cała wiedza o tym,
co wychodzi na zewnątrz, siedzi tutaj i w ``docs/API.md``.

Zasada dla każdego ładunku: **identyfikatory i kody publiczne, nigdy dane osobowe**. Kod publiczny
uczestnika jest w tym systemie pseudonimem z definicji (pojawia się w ogłoszonych wynikach), więc
jego obecność w ładunku niczego nie odsłania; imię, nazwisko czy adres e-mail – odsłoniłyby.
"""

from __future__ import annotations

import logging

from .models import (
    EVENT_APPEAL_DECIDED,
    EVENT_REGISTRATION_CREATED,
    EVENT_RESULTS_PUBLISHED,
    EVENT_STAGE_CLOSED,
    EVENT_SUBMISSION_RECEIVED,
)
from .webhooks import emit

logger = logging.getLogger(__name__)


def results_published(publication, *, rows: int) -> None:
    """Ogłoszono wyniki etapu. Ładunek mówi „gdzie i ile”, a nie „kto z iloma punktami”.

    Tabela jest publiczna i dostępna pod ``/api/v1/stages/<id>/results/`` – odbiorca, który jej
    potrzebuje, pobierze ją kluczem z zakresem ``read:results``. Wysyłanie kilkuset wierszy
    w powiadomieniu zamieniłoby webhook w kanał przesyłu danych, a ponowienie po awarii –
    w drugą kopię tabeli sprzed decyzji komisji.
    """
    stage = publication.stage
    emit(
        EVENT_RESULTS_PUBLISHED,
        {
            "publication_id": publication.pk,
            "stage_id": stage.pk,
            "edition_id": stage.edition_id,
            "anonymization": publication.anonymization,
            "rows": rows,
            "published_at": publication.published_at.isoformat(),
        },
        edition=stage.edition_id,
    )


def stage_closed(stage, *, locked: int, manual: bool) -> None:
    """Etap zamknięty – ręcznie przez koordynatora albo przez upływ deadline'u."""
    emit(
        EVENT_STAGE_CLOSED,
        {
            "stage_id": stage.pk,
            "edition_id": stage.edition_id,
            "closed_at": stage.closed_at.isoformat() if stage.closed_at else None,
            "locked_submissions": locked,
            "manual": manual,
        },
        edition=stage.edition_id,
    )


def submission_received(submission) -> None:
    """Uczestnik oddał pracę. Bez pliku, bez nazwy pliku i bez nazwiska – sama metryczka.

    Nazwa pliku jest tekstem od użytkownika i bywa nazwiskiem („Kowalski_zad1.pdf”), więc
    w ładunku jej nie ma. Rozmiar i skrót też nie: odbiorca ma wiedzieć, **że** praca wpłynęła.
    """
    entry = submission.entry
    emit(
        EVENT_SUBMISSION_RECEIVED,
        {
            "submission_id": submission.pk,
            "stage_id": entry.stage_id,
            "edition_id": entry.stage.edition_id,
            "problem_number": submission.problem.number,
            "participant_code": entry.participant.public_code,
            "version": submission.version,
            "is_late": submission.is_late,
            "submitted_at": submission.submitted_at.isoformat(),
        },
        edition=entry.stage.edition_id,
    )


def appeal_decided(appeal, decision) -> None:
    """Komisja rozstrzygnęła reklamację. Uzasadnienia w ładunku nie ma – to tekst o człowieku."""
    submission = appeal.submission
    entry = submission.entry
    emit(
        EVENT_APPEAL_DECIDED,
        {
            "appeal_id": appeal.pk,
            "submission_id": submission.pk,
            "stage_id": entry.stage_id,
            "edition_id": entry.stage.edition_id,
            "participant_code": entry.participant.public_code,
            "status": appeal.status,
            "score_changed": decision.new_score is not None,
            "decided_at": decision.decided_at.isoformat(),
        },
        edition=entry.stage.edition_id,
    )


def registration_created(participant, edition=None) -> None:
    """Zarejestrował się uczestnik. Z konta wychodzi **wyłącznie** kod publiczny i województwo.

    Województwo jest tu dlatego, że po nie sięgają integracje kuratoriów (ile zgłoszeń z regionu),
    a samo w sobie nie identyfikuje nikogo – to jedna z szesnastu wartości. Szkoły nie ma:
    „szkoła + województwo + rocznik” bywa już wskazaniem na konkretnego ucznia.

    Edycja jest przekazywana przez wołającego, bo rejestracja konta nie należy do żadnego etapu:
    uczestnik zakłada konto **do olimpiady**, a zapis do etapu jest osobną czynnością.
    """
    emit(
        EVENT_REGISTRATION_CREATED,
        {
            "participant_code": participant.public_code,
            "edition_id": edition.pk if edition is not None else None,
            "voivodeship": participant.district,
            "grade": participant.grade,
            "created_at": participant.gdpr_consent_at.isoformat() if participant.gdpr_consent_at else None,
        },
        edition=edition,
    )
