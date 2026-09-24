"""Logika zaświadczeń o statusie ucznia: wgranie skanu, skan antywirusowy, decyzja, filtr paczek.

Widoki tylko orkiestrują – każda reguła („czy wolno wgrać ponownie”, „czy wolno zaakceptować plik
przed skanem”, „kto jest uczniem z potwierdzonym statusem”) ma tu jedno miejsce. Flagi konkursu ten
moduł **nie** czyta (poza :func:`wants_verified_only`, która jest bramką parametru paczki): o tym,
czy ekran w ogóle istnieje, rozstrzyga widok (404), a serwis ma działać tak samo w teście, komendzie
i zadaniu.

**Pliki w storage.** Skan idzie do tego samego prywatnego storage, co rozwiązania
(``apps.submissions.storage``: produkcyjnie bucket ``submissions``), pod osobnym prefiksem
``student-status/``. Ten sam bucket, bo ma już wszystko, czego skan potrzebuje: konto serwisowe
ograniczone do jednego bucketu, brak publicznego odczytu i miejsce w kopii zapasowej. Osobny prefiks,
bo klucze rozwiązań zaczynają się identyfikatorem edycji – ``student-status/…`` nie może się z nimi
zderzyć i da się go wskazać jednym poleceniem przy sprzątaniu albo audycie bucketu.

**Usunięcie pliku idzie po commicie** (``transaction.on_commit``). Kolejność odwrotna – najpierw
kasowanie obiektu, potem transakcja, która może się wycofać – zostawiałaby wiersz wskazujący plik,
którego już nie ma. Odwrotny błąd (transakcja zatwierdzona, kasowanie obiektu nieudane) zostawia
najwyżej osierocony obiekt w buckecie, bez wiersza, który by go wskazywał i pokazywał komukolwiek.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone
from rest_framework import status as http

from apps.core.api import DomainError
from apps.core.models import audit
from apps.submissions.storage import get_submission_storage, sanitize_segment

from . import notifications
from .models import (
    STATE_LABELS,
    STATE_MISSING,
    CertificateStatus,
    ScanStatus,
    StudentStatusCertificate,
    enabled,
)
from .validators import validate_scan

logger = logging.getLogger(__name__)

#: Prefiks kluczy skanów w storage – patrz docstring modułu.
KEY_PREFIX = "student-status"

#: Najdłuższy powód odrzucenia. Powód czyta uczestnik w panelu i w liście: ma być zdaniem albo
#: dwoma („brak pieczątki szkoły”, „nieczytelne zdjęcie – zrób je przy świetle dziennym”), a nie
#: korespondencją – ta ma swoje miejsce w zgłoszeniach.
MAX_REASON_LENGTH = 1000

#: Powody odrzucenia nadawane przez system, nie przez koordynatora. Uczestnik ma dostać zdanie,
#: po którym wie, co zrobić, a nie nazwę sygnatury wirusa (tej nie potrzebuje i nie powinien jej
#: dostawać: bywa podpowiedzią dla kogoś, kto próbuje obejść skaner).
INFECTED_REASON = (
    "Plik został odrzucony przez skaner antywirusowy i usunięty. Zeskanuj albo sfotografuj "
    "zaświadczenie ponownie i wgraj nowy plik."
)
SCAN_ERROR_REASON = (
    "Nie udało się sprawdzić pliku – nie dotarł w całości na serwer. Wgraj zaświadczenie ponownie."
)

#: Parametr zakresu paczki ZIP (koordynator i komitet) i jego dwie wartości.
SCOPE_PARAM = "students"
SCOPE_ALL = "all"
SCOPE_VERIFIED = "verified"

HASH_CHUNK_SIZE = 1024 * 1024


# --- odczyt ---------------------------------------------------------------------------------------


def current_certificate(participant, edition) -> StudentStatusCertificate | None:
    """Wiersz bieżący pary (uczestnik, edycja) albo ``None`` – czyli „brak zaświadczenia”."""
    if participant is None or edition is None:
        return None
    return StudentStatusCertificate.objects.filter(
        participant=participant, edition=edition, is_current=True
    ).first()


def state_of(certificate: StudentStatusCertificate | None) -> str:
    """Stan widoku: ``MISSING`` albo stan wiersza bieżącego (oczekuje / zaakceptowane / odrzucone)."""
    return STATE_MISSING if certificate is None else certificate.status


def status_summary(participant, edition) -> dict:
    """Stan zaświadczenia w kształcie, którego potrzebują panel uczestnika i karta koordynatora.

    Jedno zapytanie. Klucze są stałe, żeby szablon nie musiał sprawdzać, czy wiersz w ogóle jest.
    """
    certificate = current_certificate(participant, edition)
    state = state_of(certificate)
    return {
        "edition": edition,
        "certificate": certificate,
        "state": state,
        "state_label": STATE_LABELS[state],
        "accepted": state == CertificateStatus.ACCEPTED,
        # Wgrać wolno w każdym stanie poza „zaakceptowane”: zaakceptowany papier jest zamkniętą
        # sprawą, a jego podmiana bez udziału koordynatora dawałaby „zaakceptowany” status plikowi,
        # którego nikt nie widział. Gdy trzeba go jednak zmienić, koordynator odrzuca go z powodem.
        "can_upload": state != CertificateStatus.ACCEPTED,
    }


def history(participant, edition) -> list[StudentStatusCertificate]:
    """Wszystkie wersje pary (uczestnik, edycja), od najnowszej – bieżąca też."""
    return list(
        StudentStatusCertificate.objects.filter(participant=participant, edition=edition)
        .select_related("decided_by")
        .order_by("-version")
    )


def accepted_participant_ids(edition) -> set[int]:
    """Uczestnicy edycji z **zaakceptowanym** zaświadczeniem – materiał filtra paczek ZIP."""
    return set(
        StudentStatusCertificate.objects.filter(
            edition=edition, is_current=True, status=CertificateStatus.ACCEPTED
        ).values_list("participant_id", flat=True)
    )


def accepted_pairs(participant_ids) -> set[tuple[int, int]]:
    """Pary (uczestnik, edycja) z zaakceptowanym zaświadczeniem – dla paczki recenzenta.

    Paczka recenzenta bywa przekrojem przez kilka etapów, a w teorii i przez kilka edycji (praca
    z zeszłorocznego finału w reklamacji), więc filtrem jest para, a nie sam uczestnik: status
    z tego roku nie potwierdza pracy z zeszłego.
    """
    ids = list(participant_ids)
    if not ids:
        return set()
    return set(
        StudentStatusCertificate.objects.filter(
            participant_id__in=ids, is_current=True, status=CertificateStatus.ACCEPTED
        ).values_list("participant_id", "edition_id")
    )


def wants_verified_only(value, competition) -> bool:
    """Czy paczka ZIP ma zawierać wyłącznie prace uczniów z potwierdzonym statusem.

    Wartość przychodzi z adresu albo z formularza, więc jest danymi od klienta. Brak parametru
    i ``all`` to „wszystkie prace” – dokładnie to, co paczka robiła przed tym wydaniem. ``verified``
    przy **wyłączonej** fladze nie jest cicho zamieniany na „wszystkie”: zakładka w przeglądarce
    z filtrem, który przestał istnieć, dawałaby wtedy paczkę z pracami, których pobierający nie
    zamawiał, a on sądziłby, że są odsiane. Odpowiedzią jest odmowa (404 z powodem w widoku).
    """
    raw = (value or SCOPE_ALL).strip()
    if raw == SCOPE_ALL:
        return False
    if raw == SCOPE_VERIFIED and enabled(competition):
        return True
    raise DomainError(
        "Filtr „tylko uczniowie z potwierdzonym statusem ucznia” nie jest dostępny w tym konkursie.",
        "STUDENT_STATUS_SCOPE_UNAVAILABLE",
        http.HTTP_404_NOT_FOUND,
    )


# --- storage ------------------------------------------------------------------------------------


def build_object_key(certificate: StudentStatusCertificate, *, competition_id, participant_code, ext) -> str:
    """``student-status/{konkurs}/{edycja}/{kod uczestnika}/{uuid}/{sha256}.{ext}``.

    Wszystkie segmenty pochodzą z bazy albo z treści pliku – nigdy z nazwy pliku od uczestnika,
    więc ``../../evil.pdf`` jest co najwyżej niczym (nazwy w ogóle nie zapisujemy).
    """
    parts = [
        KEY_PREFIX,
        sanitize_segment(competition_id),
        sanitize_segment(certificate.edition_id),
        sanitize_segment(participant_code),
        sanitize_segment(certificate.uuid),
    ]
    return "/".join(parts) + f"/{certificate.sha256}.{ext}"


def _sha256_of(upload) -> str:
    digest = hashlib.sha256()
    upload.seek(0)
    for chunk in iter(lambda: upload.read(HASH_CHUNK_SIZE), b""):
        digest.update(chunk)
    upload.seek(0)
    return digest.hexdigest()


def open_scan(certificate: StudentStatusCertificate):
    """Otwarty strumień pliku albo ``None``, gdy pliku nie ma (ani w bazie, ani w storage).

    Oba backendy storage mówią „nie ma obiektu” inaczej (``FileNotFoundError`` lokalnie,
    ``ClientError`` z ``NoSuchKey`` w S3) – sprowadza je do jednego ``None`` ta sama funkcja, co skan
    antywirusowy rozwiązań. Widok zamienia ``None`` na 404, a nie na 500.
    """
    from apps.submissions.tasks import MissingStorageObject, _open_object

    if not certificate.object_key:
        return None
    try:
        return _open_object(get_submission_storage(), certificate.object_key)
    except MissingStorageObject:
        logger.warning("Brak obiektu w storage dla zaświadczenia %s.", certificate.pk)
        return None


def _delete_objects_on_commit(keys: list[str]) -> None:
    """Kasuje obiekty ze storage **po** zatwierdzeniu transakcji – uzasadnienie w docstringu modułu.

    Błąd storage nie wywraca operacji, która go wywołała (anonimizacja konta, akceptacja nowej
    wersji): wiersz już nie wskazuje obiektu, więc nikt go nie zobaczy, a ślad zostaje w logu.
    """
    keys = [key for key in keys if key]
    if not keys:
        return

    def _delete() -> None:
        storage = get_submission_storage()
        for key in keys:
            try:
                storage.delete(key)
            except Exception:  # noqa: BLE001 - powód w docstringu: osierocony obiekt, a nie 500
                logger.exception("Nie udało się usunąć skanu zaświadczenia %s ze storage.", key)

    transaction.on_commit(_delete)


def _remove_file(certificate: StudentStatusCertificate, *, now=None) -> None:
    """Zdejmuje plik wiersza: klucz pusty w bazie teraz, obiekt ze storage po commicie."""
    if not certificate.object_key:
        return
    key = certificate.object_key
    certificate.object_key = ""
    certificate.file_removed_at = now or timezone.now()
    certificate.save(update_fields=["object_key", "file_removed_at"])
    _delete_objects_on_commit([key])


# --- wgranie ----------------------------------------------------------------------------------------


@transaction.atomic
def upload_scan(participant, edition, upload, *, actor=None, request=None) -> StudentStatusCertificate:
    """Przyjmuje skan zaświadczenia: blokada → reguły → walidacja → storage → skan antywirusowy.

    Blokadą jest wiersz **profilu uczestnika** (``SELECT … FOR UPDATE``): dwa równoległe wgrania
    (podwójne kliknięcie, dwie karty przeglądarki) szeregują się na nim, więc dostają wersje 1 i 2,
    a nie kolizję więzu „jeden bieżący”. To ta sama technika, co przy wersjach rozwiązań.

    Poprzednia wersja przestaje być bieżąca; jeżeli nikt jej jeszcze nie rozpatrzył, dostaje stan
    „zastąpione”, a jej plik znika ze storage. Wersja **odrzucona** zostaje odrzuconą – z powodem –
    bo to jest informacja dla uczestnika, dlaczego wgrywa drugi raz.
    """
    from apps.accounts.models import Participant

    if participant.competition_id != edition.competition_id:
        # Profil jednego konkursu i edycja drugiego – to jest błąd wołającego, a nie stan danych.
        raise DomainError(
            "Ta edycja nie należy do konkursu uczestnika.", "EDITION_MISMATCH", http.HTTP_404_NOT_FOUND
        )
    Participant.objects.select_for_update().filter(pk=participant.pk).values_list("pk", flat=True).first()
    current = current_certificate(participant, edition)
    if current is not None and current.status == CertificateStatus.ACCEPTED:
        raise DomainError(
            "Twoje zaświadczenie zostało już zaakceptowane – nie trzeba wgrywać go ponownie.",
            "STUDENT_STATUS_ALREADY_ACCEPTED",
            http.HTTP_409_CONFLICT,
        )

    ext, mime = validate_scan(upload)
    sha256 = _sha256_of(upload)
    now = timezone.now()
    previous_version = (
        StudentStatusCertificate.objects.filter(participant=participant, edition=edition).aggregate(
            top=Max("version")
        )["top"]
        or 0
    )
    if current is not None:
        current.is_current = False
        fields = ["is_current"]
        if current.status == CertificateStatus.PENDING:
            current.status = CertificateStatus.SUPERSEDED
            fields.append("status")
        current.save(update_fields=fields)
        _remove_file(current, now=now)

    certificate = StudentStatusCertificate(
        participant=participant,
        edition=edition,
        version=previous_version + 1,
        is_current=True,
        status=CertificateStatus.PENDING,
        sha256=sha256,
        mime=mime,
        size_bytes=getattr(upload, "size", 0) or 0,
        scan_status=ScanStatus.PENDING,
        uploaded_at=now,
    )
    certificate.object_key = build_object_key(
        certificate,
        competition_id=edition.competition_id,
        participant_code=participant.public_code,
        ext=ext,
    )
    get_submission_storage().put(certificate.object_key, upload, mime)
    certificate.save()

    def _enqueue_scan(pk: int = certificate.pk) -> None:
        from .tasks import scan_certificate_file

        scan_certificate_file.delay(pk)

    transaction.on_commit(_enqueue_scan)
    # W ``diff`` są wyłącznie liczby: nazwa pliku od uczestnika bywa jego nazwiskiem, a dziennik
    # zdarzeń czytają osoby, które nie muszą znać danych uczestników.
    audit(
        actor or participant.user,
        "student_status.uploaded",
        certificate,
        {"edition_id": edition.pk, "version": certificate.version, "size": certificate.size_bytes},
        request=request,
    )
    return certificate


# --- skan antywirusowy ------------------------------------------------------------------------------


def _locked(pk: int) -> StudentStatusCertificate | None:
    return (
        StudentStatusCertificate.objects.select_for_update(of=("self",))
        .select_related("participant", "participant__user", "edition", "edition__competition")
        .filter(pk=pk)
        .first()
    )


def _reject_by_system(certificate: StudentStatusCertificate, reason: str, *, now) -> None:
    """Odrzucenie bez udziału koordynatora (skaner). Dotyczy wyłącznie wersji nierozpatrzonej."""
    if not certificate.is_current or certificate.status != CertificateStatus.PENDING:
        return
    certificate.status = CertificateStatus.REJECTED
    certificate.rejection_reason = reason
    certificate.decided_at = now
    certificate.decided_by = None
    certificate.save(update_fields=["status", "rejection_reason", "decided_at", "decided_by"])
    notifications.notify_rejected(certificate)


def apply_scan_verdict(pk: int, verdict: str) -> StudentStatusCertificate | None:
    """Zapisuje wynik skanu. Plik zainfekowany jest odrzucany i **usuwany** – nie ma kwarantanny.

    Kwarantanna ma sens przy pracy, którą trzeba umieć odtworzyć w sporze o ocenę. Skan
    zaświadczenia nie jest przedmiotem żadnego sporu: uczestnik wgrywa nowy, a zainfekowanego pliku
    nie ma komu i po co pokazywać.
    """
    with transaction.atomic():
        certificate = _locked(pk)
        if certificate is None or certificate.scan_status != ScanStatus.PENDING:
            return certificate
        now = timezone.now()
        infected = verdict == ScanStatus.INFECTED
        certificate.scan_status = ScanStatus.INFECTED if infected else ScanStatus.CLEAN
        certificate.scanned_at = now
        certificate.save(update_fields=["scan_status", "scanned_at"])
        if infected:
            audit(None, "student_status.infected", certificate, {"version": certificate.version})
            _remove_file(certificate, now=now)
            _reject_by_system(certificate, INFECTED_REASON, now=now)
    return certificate


def apply_scan_error(pk: int) -> StudentStatusCertificate | None:
    """Skan nie ma jak się udać (brak obiektu w storage) – plik odrzucony z prośbą o ponowienie."""
    with transaction.atomic():
        certificate = _locked(pk)
        if certificate is None or certificate.scan_status != ScanStatus.PENDING:
            return certificate
        now = timezone.now()
        certificate.scan_status = ScanStatus.ERROR
        certificate.scanned_at = now
        certificate.save(update_fields=["scan_status", "scanned_at"])
        _remove_file(certificate, now=now)
        _reject_by_system(certificate, SCAN_ERROR_REASON, now=now)
    return certificate


# --- decyzja koordynatora -----------------------------------------------------------------------------


def _decidable(certificate: StudentStatusCertificate) -> StudentStatusCertificate:
    """Wiersz pod blokadą, o ile jest **bieżący** – decyzja o historii nie ma czego zmienić."""
    locked = _locked(certificate.pk)
    if locked is None or not locked.is_current:
        raise DomainError(
            "Uczestnik wgrał w międzyczasie nowszy plik – odśwież listę i rozpatrz nowszą wersję.",
            "STUDENT_STATUS_NOT_CURRENT",
            http.HTTP_409_CONFLICT,
        )
    return locked


@transaction.atomic
def accept(certificate: StudentStatusCertificate, *, actor, request=None) -> StudentStatusCertificate:
    """Akceptacja zaświadczenia. Wyłącznie pliku po **czystym** skanie.

    Akceptacja pliku, którego nie przeskanowano, byłaby akceptacją pliku, którego koordynator nie
    mógł obejrzeć (podgląd jest dostępny dopiero po skanie) – czyli podpisem pod niczym. Odrzucone
    wolno zaakceptować: koordynator, który kliknął „Odrzuć” przy złym wierszu, poprawia pomyłkę
    jednym przyciskiem, a nie prośbą do uczestnika o ponowne wgranie tego samego pliku.
    """
    locked = _decidable(certificate)
    if locked.status == CertificateStatus.ACCEPTED:
        return locked
    if not locked.is_clean:
        raise DomainError(
            "Plik nie przeszedł jeszcze skanu antywirusowego – zaakceptować można go dopiero po nim.",
            "STUDENT_STATUS_NOT_SCANNED",
            http.HTTP_409_CONFLICT,
        )
    previous = locked.status
    locked.status = CertificateStatus.ACCEPTED
    locked.rejection_reason = ""
    locked.decided_at = timezone.now()
    locked.decided_by = actor
    locked.save(update_fields=["status", "rejection_reason", "decided_at", "decided_by"])
    audit(
        actor,
        "student_status.accepted",
        locked,
        {"version": locked.version, "edition_id": locked.edition_id, "previous": previous},
        request=request,
    )
    notifications.notify_accepted(locked)
    return locked


@transaction.atomic
def reject(
    certificate: StudentStatusCertificate, reason: str, *, actor, request=None
) -> StudentStatusCertificate:
    """Odrzucenie z powodem. Powód jest obowiązkowy – bez niego uczestnik nie wie, co poprawić.

    Zaakceptowane wolno odrzucić (pomyłka, zaświadczenie cudzej szkoły wychwycone później) – to jest
    jedyna droga, żeby uczestnik mógł wgrać papier ponownie. Treść powodu **nie** trafia do dziennika
    zdarzeń: bywa zdaniem o konkretnej osobie („nazwisko na pieczątce się nie zgadza”), a dziennik
    ma być bez danych osobowych; zostaje w wierszu, który czyta uczestnik i koordynator.
    """
    text = (reason or "").strip()
    if not text:
        raise DomainError(
            "Podaj powód odrzucenia – uczestnik zobaczy go w panelu i w wiadomości e-mail.",
            "REASON_REQUIRED",
            http.HTTP_400_BAD_REQUEST,
        )
    if len(text) > MAX_REASON_LENGTH:
        raise DomainError(
            f"Powód odrzucenia może mieć najwyżej {MAX_REASON_LENGTH} znaków.",
            "REASON_TOO_LONG",
            http.HTTP_400_BAD_REQUEST,
        )
    locked = _decidable(certificate)
    previous = locked.status
    locked.status = CertificateStatus.REJECTED
    locked.rejection_reason = text
    locked.decided_at = timezone.now()
    locked.decided_by = actor
    locked.save(update_fields=["status", "rejection_reason", "decided_at", "decided_by"])
    audit(
        actor,
        "student_status.rejected",
        locked,
        {"version": locked.version, "edition_id": locked.edition_id, "previous": previous},
        request=request,
    )
    notifications.notify_rejected(locked)
    return locked


# --- lista koordynatora ---------------------------------------------------------------------------


#: Filtry listy koordynatora – klucz w adresie (``?status=…``) i stan widoku, który wybiera.
FILTERS: dict[str, str] = {
    "oczekujace": CertificateStatus.PENDING,
    "zaakceptowane": CertificateStatus.ACCEPTED,
    "odrzucone": CertificateStatus.REJECTED,
    "brak": STATE_MISSING,
}


@dataclass(frozen=True)
class Row:
    """Jeden wiersz listy: uczestnik edycji i jego zaświadczenie bieżące (albo brak)."""

    participant: object
    certificate: StudentStatusCertificate | None

    @property
    def state(self) -> str:
        return state_of(self.certificate)

    @property
    def state_label(self) -> str:
        return STATE_LABELS[self.state]


def edition_participants(edition):
    """Uczestnicy edycji: zapisani do któregokolwiek jej etapu **albo** z wgranym zaświadczeniem.

    Ta sama definicja „uczestnika edycji”, co w komunikatach organizatora
    (``apps.accounts.messaging.resolve_recipients``): ktoś, kto się do niej zapisał, a nie ktoś, kto
    kiedyś założył konto. Druga połowa warunku jest dla osoby, która wgrała zaświadczenie przed
    zapisem do etapu – jej plik musi dać się rozpatrzyć, nawet jeśli do zawodów jeszcze nie stanęła.

    Bez kont po anonimizacji (v0.34.0, ``exclude_anonymised``): ich zaświadczenia znikają razem
    z kontem (:func:`erase_for_user`), więc na liście stałyby wyłącznie jako „brak zaświadczenia”
    i zawyżały licznik osób, od których koordynator czeka na papier – od kogoś, kto już nie istnieje.
    """
    from apps.accounts.models import Participant

    return (
        Participant.objects.filter(competition_id=edition.competition_id)
        .exclude_anonymised()
        .filter(Q(stage_entries__stage__edition=edition) | Q(student_status_certificates__edition=edition))
        .distinct()
    )


def coordinator_rows(edition, *, state: str = "", query: str = "") -> tuple[list[Row], dict[str, int]]:
    """Wiersze listy koordynatora i liczniki czterech stanów – dwa zapytania niezależnie od liczby osób.

    Liczniki są liczone z **całej** edycji, a nie z przefiltrowanej listy: to one odpowiadają na
    pytanie „ile mam jeszcze do przejrzenia”, a filtr jest odpowiedzią na „pokaż mi je”.
    """
    participants = list(
        edition_participants(edition).select_related("user").order_by("user__last_name", "public_code")
    )
    current = {
        row.participant_id: row
        for row in StudentStatusCertificate.objects.filter(edition=edition, is_current=True).select_related(
            "decided_by"
        )
    }
    rows = [Row(participant, current.get(participant.pk)) for participant in participants]
    counts = {key: sum(1 for row in rows if row.state == value) for key, value in FILTERS.items()}
    if state in FILTERS:
        rows = [row for row in rows if row.state == FILTERS[state]]
    needle = (query or "").strip().lower()
    if needle:
        rows = [
            row
            for row in rows
            if needle in row.participant.public_code.lower()
            or needle in (row.participant.user.email or "").lower()
            or needle in row.participant.user.get_full_name().lower()
            or needle in (row.participant.school or "").lower()
        ]
    return rows, counts


# --- RODO: usunięcie konta i retencja -----------------------------------------------------------------


def erase_for_user(user) -> int:
    """Kasuje zaświadczenia **wszystkich** profili uczestnika tego konta – wiersze i pliki.

    Woła to ``accounts.profile`` przy anonimizacji konta (żądanie z art. 17, usunięcie przez
    koordynatora, automat retencji) i przed skasowaniem konta bez śladu w zawodach. Wiersze znikają
    razem z plikami, a nie zostają „bez pliku” jak przy zastąpieniu: stan „zaakceptowane” przy
    zanonimizowanym profilu nie jest już niczyim statusem ucznia, a dokumentacją zawodów jest praca
    i jej ocena, nie papier ze szkoły. Ślad zostaje w dzienniku zdarzeń (wgranie, decyzje), który
    danych osobowych nie ma.
    """
    rows = StudentStatusCertificate.objects.filter(participant__user=user)
    keys = list(rows.exclude(object_key="").values_list("object_key", flat=True))
    deleted, _ = rows.delete()
    _delete_objects_on_commit(keys)
    return deleted


@transaction.atomic
def purge_expired_scans(now=None) -> int:
    """Usuwa **pliki** zaświadczeń edycji, którym upłynął termin retencji danych uczestników.

    Termin jest ten sam, co przy anonimizacji kont (``accounts.retention.expired_editions``: ostatni
    deadline etapu edycji + ``Edition.data_retention_months``) – rejestr czynności ma mieć jedną
    odpowiedź na pytanie „jak długo”. Osobny przebieg jest jednak potrzebny, bo automat anonimizacji
    **omija** konta osób startujących w późniejszej edycji: uczeń trzeciej klasy wraca za rok, jego
    konto zostaje – ale zeszłoroczny skan z pieczątką nie ma już żadnego celu. Wiersz zostaje (stan
    i decyzja bez pliku), bo historia decyzji danych osobowych nie niesie.
    """
    from apps.accounts.retention import expired_editions

    edition_ids = [edition.pk for edition in expired_editions(now)]
    if not edition_ids:
        return 0
    moment = now or timezone.now()
    rows = list(
        StudentStatusCertificate.objects.filter(edition_id__in=edition_ids)
        .exclude(object_key="")
        .select_for_update()
    )
    keys = [row.object_key for row in rows]
    StudentStatusCertificate.objects.filter(pk__in=[row.pk for row in rows]).update(
        object_key="", file_removed_at=moment
    )
    _delete_objects_on_commit(keys)
    if rows:
        logger.info(
            "Retencja skanów zaświadczeń: usunięto %s plików z %s edycji.", len(rows), len(edition_ids)
        )
    return len(rows)
