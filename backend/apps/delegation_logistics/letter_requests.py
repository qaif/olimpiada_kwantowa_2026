"""Wnioski opiekunów o listy zapraszające i decyzje oficera logistyki (VISA-01 § 3).

Przepływ: opiekun drużyny składa wniosek o list **imienny** dla osoby ze swojej delegacji
(:func:`request_letters`) → oficer logistyki zatwierdza (= wystawia list przez
``letters.issue_letter`` LOG-01) albo odrzuca z powodem (:func:`approve`, :func:`reject`, także
hurtowo) → opiekunowie delegacji dostają list e-mail w swoim języku (:func:`notify`).

Granice bezpieczeństwa (te same, co w ``services`` LOG-01, i z tego samego powodu):

- **zakres delegacji** – opiekun wskazuje osobę identyfikatorem, a serwis wybiera ją z querysetu
  **jego** delegacji (``services.member_for_leader``): osoba innego kraju to 404, a nie wniosek,
- **rola w serwisie** – zatwierdzenie, odrzucenie i unieważnienie sprawdzają przydział oficera
  (``access.require_officer``) tutaj, nie tylko w widoku: zatwierdzenie wystawia list z numerem
  paszportu, czyli dotyka danych, które widzi wyłącznie oficer,
- **audyt bez wolnego tekstu** – wpisy niosą identyfikatory, numery listów i liczniki; powód
  odrzucenia bywa opisem osoby („paszport nieważny”, „odmowa poprzedniej wizy”) i do dziennika
  zdarzeń, który nie ma retencji, nie trafia,
- **poczta bez danych paszportowych** – list do opiekuna wymienia imiona i nazwiska z konta, numery
  listów i powody odrzucenia.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from django.db import IntegrityError, transaction
from django.http import Http404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import status

from apps.core.api import DomainError
from apps.core.models import audit

from . import access
from .letter_texts import letter_languages
from .letters import _identity_complete, issue_letter, letters_to_supersede, revoke_letter
from .models import InvitationLetter, LetterRequest, LetterRequestStatus, LetterScope
from .services import member_for_leader, require_not_purged

logger = logging.getLogger(__name__)

#: Szablony listu do opiekunów o decyzji – jeden list na opiekuna na decyzję (także hurtową).
LETTER_DECISION_SUBJECT_TEMPLATE = "delegation_logistics/email/letter_decision_subject.txt"
LETTER_DECISION_BODY_TEMPLATE = "delegation_logistics/email/letter_decision_body.txt"

#: Najdłuższy powód odrzucenia – tyle, ile pole modelu. Zdanie albo dwa, nie korespondencja.
REASON_MAX = 500


# --- opiekun drużyny ---------------------------------------------------------------------------------


def request_letters(leader, member_ids, *, language: str, request=None) -> dict:
    """Wnioski o list imienny dla wskazanych osób delegacji opiekuna. Zwraca ``{created, skipped}``.

    Osoba bez kompletnego dokumentu podróży to odmowa **całego** zgłoszenia z listą nazwisk – wniosek,
    którego oficer nie może zatwierdzić, byłby tylko drugą rundą korespondencji. Osoba z wnioskiem już
    oczekującym jest pomijana (``skipped``), a nie odmową: dwóch opiekunów jednej drużyny klikających
    naraz chce tego samego.
    """
    if language not in {code for code, _name in letter_languages(leader.delegation.competition)}:
        # Wybór spoza listy ekranu: spreparowany formularz albo język wyłączony od chwili otwarcia
        # strony (szablon z bazy wymusza angielski – L7).
        raise DomainError(
            _("Nieobsługiwany język listu."), "LETTER_LANGUAGE_INVALID", status.HTTP_400_BAD_REQUEST
        )
    delegation = leader.delegation
    require_not_purged(delegation.edition)
    ids = sorted({int(pk) for pk in member_ids})
    if not ids:
        raise DomainError(_("Zaznacz co najmniej jedną osobę."), "NO_MEMBERS", status.HTTP_400_BAD_REQUEST)
    members = [member_for_leader(leader, pk) for pk in ids]
    incomplete = [member.full_name for member in members if not _identity_complete(member)]
    if incomplete:
        raise DomainError(
            _("Uzupełnij dane dokumentu podróży: %(names)s.") % {"names": ", ".join(incomplete)},
            "IDENTITY_INCOMPLETE",
            status.HTTP_409_CONFLICT,
        )
    created, skipped = [], []
    for member in members:
        try:
            with transaction.atomic():
                row = LetterRequest.objects.create(
                    delegation=delegation,
                    member=member,
                    language=language,
                    requested_by=leader.user,
                )
        except IntegrityError:
            skipped.append(member)
            continue
        audit(
            leader.user,
            "logistics.letter_requested",
            row,
            {"member": member.pk, "delegation": delegation.pk, "language": language},
            request=request,
        )
        created.append(row)
    return {"created": created, "skipped": skipped}


def request_for_leader(leader, pk: int) -> LetterRequest:
    """Wniosek **delegacji tego opiekuna** albo 404 – zakres z querysetu."""
    row = LetterRequest.objects.filter(delegation=leader.delegation, pk=pk).select_related("member").first()
    if row is None:
        raise Http404("Nie ma takiego wniosku w tej delegacji.")
    return row


def withdraw(leader, row: LetterRequest, *, request=None) -> LetterRequest:
    """Opiekun wycofuje wniosek, którego oficer jeszcze nie rozstrzygnął."""
    if row.delegation_id != leader.delegation_id:
        raise Http404("Nie ma takiego wniosku w tej delegacji.")
    with transaction.atomic():
        locked = LetterRequest.objects.select_for_update().get(pk=row.pk)
        if locked.status != LetterRequestStatus.PENDING:
            raise DomainError(
                _("Ten wniosek został już rozpatrzony."), "REQUEST_DECIDED", status.HTTP_409_CONFLICT
            )
        locked.status = LetterRequestStatus.WITHDRAWN
        locked.decided_at = timezone.now()
        locked.save(update_fields=["status", "decided_at"])
    audit(
        leader.user,
        "logistics.letter_request_withdrawn",
        locked,
        {"member": locked.member_id},
        request=request,
    )
    return locked


def leader_rows(delegation, members) -> list[dict]:
    """Wiersze tabeli „Listy zapraszające” opiekuna: osoba, kompletność dokumentu, ostatni wniosek.

    Dwa zapytania niezależnie od liczby osób – wnioski i listy delegacji czytamy raz i rozkładamy
    po osobach w Pythonie.
    """
    latest: dict[int, LetterRequest] = {}
    for row in (
        LetterRequest.objects.filter(delegation=delegation)
        .select_related("letter")
        .order_by("-requested_at", "-id")
    ):
        latest.setdefault(row.member_id, row)
    valid: dict[int, list[InvitationLetter]] = defaultdict(list)
    for letter in InvitationLetter.objects.filter(
        delegation=delegation, scope=LetterScope.PERSON, revoked_at__isnull=True, member__isnull=False
    ).order_by("-issued_at"):
        valid[letter.member_id].append(letter)
    return [
        {
            "member": member,
            "complete": _identity_complete(member),
            "request": latest.get(member.pk),
            "letters": valid.get(member.pk, []),
            # Listy, które nowy list unieważni (zmienione dane istotne) – opiekun ma to wiedzieć,
            # zanim poprosi o nowy list osobie, której stary list leży już w konsulacie (M1).
            "would_revoke": [letter.number for letter in letters_to_supersede(member)]
            if valid.get(member.pk)
            else [],
        }
        for member in members
    ]


# --- oficer logistyki --------------------------------------------------------------------------------


def requests_of(competition, edition, *, country: str = "", state: str = ""):
    """Wnioski edycji z filtrami kraju (kod regionu) i stanu – lista oficera i eksport CSV."""
    rows = (
        LetterRequest.objects.for_competition(competition)
        .filter(delegation__edition=edition)
        .select_related(
            "delegation__country",
            "member__participant__user",
            "member__user",
            "member__guest",
            "letter",
            "requested_by",
            "decided_by",
        )
        .order_by("delegation__country__name", "-requested_at", "-id")
    )
    if country:
        rows = rows.filter(delegation__country__code=country)
    if state in LetterRequestStatus.values:
        rows = rows.filter(status=state)
    return rows


def request_for_competition(competition, pk: int) -> LetterRequest:
    row = (
        LetterRequest.objects.for_competition(competition)
        .select_related("delegation", "member")
        .filter(pk=pk)
        .first()
    )
    if row is None:
        raise Http404("Nie ma takiego wniosku w tym konkursie.")
    return row


def requests_by_ids(competition, ids) -> list[LetterRequest]:
    """Wnioski konkursu o tych identyfikatorach – cudze identyfikatory po prostu odpadają."""
    clean = [int(pk) for pk in ids if str(pk).isdigit()]
    return list(
        LetterRequest.objects.for_competition(competition)
        .select_related("delegation", "member")
        .filter(pk__in=clean)
        .order_by("id")
    )


def pending_count(competition, edition) -> int:
    return (
        LetterRequest.objects.for_competition(competition)
        .filter(delegation__edition=edition, status=LetterRequestStatus.PENDING)
        .count()
    )


def would_revoke(rows) -> dict[int, list[str]]:
    """Numery listów, które zatwierdzenie danego wniosku unieważni – podgląd na ekranie oficera (M1)."""
    return {
        row.pk: [letter.number for letter in letters_to_supersede(row.member)]
        for row in rows
        if row.is_pending
    }


def approve(competition, rows, *, actor, request=None) -> dict:
    """Zatwierdza wnioski: każdy osobno wystawia list imienny. Zwraca ``{approved, failed}``.

    Każdy wniosek w osobnej transakcji – w decyzji hurtowej osoba bez kompletnego dokumentu (opiekun
    zdążył wyczyścić pole) albo finał bez dat nie może zatrzymać listów pozostałych. Wniosek, którego
    nie dało się zatwierdzić, zostaje **oczekujący**, a ``failed`` niesie powód dla ekranu.
    """
    access.require_officer(actor, competition)
    approved, failed = [], []
    for row in rows:
        try:
            with transaction.atomic():
                locked = LetterRequest.objects.select_for_update().select_related("delegation").get(pk=row.pk)
                if locked.delegation.competition_id != competition.pk:
                    raise Http404("Nie ma takiego wniosku w tym konkursie.")
                if locked.status != LetterRequestStatus.PENDING:
                    raise DomainError(
                        "Wniosek został już rozpatrzony.", "REQUEST_DECIDED", status.HTTP_409_CONFLICT
                    )
                letter = issue_letter(
                    competition,
                    locked.delegation,
                    member=locked.member,
                    actor=actor,
                    request=request,
                    language=locked.language,
                )
                # Wcześniejsze listy z innymi danymi istotnymi unieważnia już ``issue_letter`` (M1).
                superseded = len(letter.superseded)
                locked.status = LetterRequestStatus.APPROVED
                locked.decided_by = actor
                locked.decided_at = timezone.now()
                locked.letter = letter
                locked.save(update_fields=["status", "decided_by", "decided_at", "letter"])
        except DomainError as exc:
            failed.append((row, str(exc.detail)))
            continue
        audit(
            actor,
            "logistics.letter_request_approved",
            locked,
            {"letter": letter.number, "member": locked.member_id, "superseded": superseded},
            request=request,
        )
        approved.append(locked)
    notify(competition, approved, request=request)
    return {"approved": approved, "failed": failed}


def reject(competition, rows, *, reason: str, actor, request=None) -> list[LetterRequest]:
    """Odrzuca wnioski z jednym powodem (obowiązkowym). Rozstrzygnięte wcześniej są pomijane."""
    access.require_officer(actor, competition)
    reason = (reason or "").strip()
    if not reason:
        raise DomainError("Podaj powód odrzucenia.", "REJECT_REASON_REQUIRED", status.HTTP_400_BAD_REQUEST)
    rejected = []
    now = timezone.now()
    for row in rows:
        with transaction.atomic():
            locked = LetterRequest.objects.select_for_update().select_related("delegation").get(pk=row.pk)
            if locked.delegation.competition_id != competition.pk:
                raise Http404("Nie ma takiego wniosku w tym konkursie.")
            if locked.status != LetterRequestStatus.PENDING:
                continue
            locked.status = LetterRequestStatus.REJECTED
            locked.reject_reason = reason[:REASON_MAX]
            locked.decided_by = actor
            locked.decided_at = now
            locked.save(update_fields=["status", "reject_reason", "decided_by", "decided_at"])
        audit(
            actor, "logistics.letter_request_rejected", locked, {"member": locked.member_id}, request=request
        )
        rejected.append(locked)
    notify(competition, rejected, request=request)
    return rejected


def revoke(competition, letter: InvitationLetter, *, reason: str, actor, request=None) -> InvitationLetter:
    """Unieważnienie listu przez oficera – rola sprawdzana tu, skład w ``letters.revoke_letter``."""
    access.require_officer(actor, competition)
    if letter.competition_id != competition.pk:
        raise Http404("Nie ma takiego listu.")
    return revoke_letter(letter, reason=reason, actor=actor, request=request)


# --- powiadomienia --------------------------------------------------------------------------------------


def notify(competition, decided, *, request=None) -> int:
    """List do czynnych opiekunów każdej delegacji, której dotyczy decyzja – w języku odbiorcy.

    Jeden list na opiekuna na wywołanie: decyzja hurtowa o czterdziestu wnioskach z ośmiu krajów to
    osiem listów do każdego z opiekunów tych krajów, a nie czterdzieści. Zwraca liczbę wysłanych listów.
    """
    if not decided:
        return 0
    from apps.accounts.activation import absolute_url, queue_mail
    from apps.accounts.delegations import DelegationLeader
    from apps.accounts.preferences import language_for
    from apps.tenancy import branding

    by_delegation: dict[int, list[LetterRequest]] = defaultdict(list)
    for row in decided:
        by_delegation[row.delegation_id].append(row)
    link = absolute_url(reverse("web:delegation-logistics"), request, competition)
    sent = 0
    for rows in by_delegation.values():
        delegation = rows[0].delegation
        approved = [
            {"name": row.member.full_name, "number": row.letter.number if row.letter_id else ""}
            for row in rows
            if row.status == LetterRequestStatus.APPROVED
        ]
        rejected = [
            {"name": row.member.full_name, "reason": row.reject_reason}
            for row in rows
            if row.status == LetterRequestStatus.REJECTED
        ]
        leaders = DelegationLeader.objects.active().filter(delegation=delegation).select_related("user")
        for leader in leaders:
            with language_for(leader.user, competition):
                context = {
                    "first_name": leader.user.first_name,
                    "country": delegation.country.name,
                    "approved": approved,
                    "rejected": rejected,
                    "link": link,
                    "brand": branding.brand_names(competition),
                }
                subject = (
                    render_to_string(LETTER_DECISION_SUBJECT_TEMPLATE, context).strip().replace("\n", " ")
                )
                body = render_to_string(LETTER_DECISION_BODY_TEMPLATE, context)
            queue_mail(subject, body, leader.user.email, competition=competition)
            sent += 1
    return sent


# --- eksport --------------------------------------------------------------------------------------------

#: Nagłówek CSV wniosków. Po polsku – plik otwiera oficer, a jego ekrany są po polsku. **Bez** danych
#: paszportowych: to jest lista spraw do załatwienia, a pełny eksport z paszportami jest osobny (LOG-01,
#: „Eksport pełny” – z audytem).
EXPORT_HEADER = [
    "kraj",
    "osoba",
    "rola",
    "stan wniosku",
    "język listu",
    "złożony",
    "złożył(a)",
    "rozstrzygnięty",
    "powód odrzucenia",
    "numer listu",
    "stan listu",
]


def export_dataset(rows):
    from apps.core.exports import Dataset

    def moment(value):
        return timezone.localtime(value).strftime("%Y-%m-%d %H:%M") if value else ""

    data = []
    for row in rows:
        letter = row.letter
        data.append(
            [
                row.delegation.country.name,
                row.member.full_name,
                str(row.member.role_label),
                row.get_status_display(),
                row.language,
                moment(row.requested_at),
                row.requested_by.email if row.requested_by_id else "",
                moment(row.decided_at),
                row.reject_reason,
                letter.number if letter else "",
                ("unieważniony" if letter.revoked_at else "ważny") if letter else "",
            ]
        )
    return Dataset(
        header=EXPORT_HEADER,
        rows=iter(data),
        count=len(data),
        title="Wnioski o listy zapraszające",
        filename="wnioski-listy-zapraszajace",
    )
