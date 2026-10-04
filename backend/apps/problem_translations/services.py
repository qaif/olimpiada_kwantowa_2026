"""Czynności tłumaczeń zadań: okno, źródło, szkic, wysłanie, przegląd, uczeń, eksport (TR-01).

Widoki tylko orkiestrują – **każda** reguła dostępu stoi tutaj, bo do tych samych danych prowadzą
trzy ekrany (opiekun, koordynator, uczeń) i pakiet do druku. Granice, każda z powodem:

- **poufność przed otwarciem etapu.** Źródło i tłumaczenia zadania widzi koordynator, a z opiekunów
  wyłącznie ten, kto ma wiersz ``DelegationLeader`` w **bieżącej** edycji konkursu żądania
  (``delegation_services.leader_for`` – aktywne konto, rola z wiersza, nie z grupy), zadeklarował
  język i wchodzi **w otwartym oknie** etapu (:func:`problem_for_leader`). Poza oknem – 404, tak samo
  jak dla każdego innego: istnienie zadania nie jest informacją,
- **zakres kraju.** Opiekun dochodzi do tłumaczenia wyłącznie przez parę (zadanie, język swojej
  delegacji); w trybie osobnym właścicielem jest jego delegacja, we wspólnym – nikt (``NULL``),
  a język musi być zadeklarowany przez jego delegację. Identyfikator tłumaczenia z adresu nie istnieje
  po stronie opiekuna – nie ma czego podmienić,
- **uczeń czyta migawkę.** Uczniowi i eksportowi oddajemy ``approved_revision`` – nigdy roboczy tekst,
- **audyt wglądu.** Każde otwarcie źródła, pobranie pliku, odczyt ucznia i eksport zapisuje
  :func:`record_view`; zmiany stanu – własne wpisy. Wpisy niosą identyfikatory i kody języków,
  bez treści zadania (dziennik czytają osoby, które treści przed zawodami znać nie powinny).
"""

from __future__ import annotations

import difflib
import hashlib
import logging
import uuid
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.http import Http404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import status

from apps.accounts import delegation_services as delegations
from apps.accounts.delegations import Delegation, DelegationLeader
from apps.accounts.models import CompetitionRole, Participant
from apps.core.api import DomainError
from apps.core.models import audit

from . import languages
from .models import (
    BODY_MAX_LENGTH,
    DelegationLanguage,
    ProblemSource,
    RevisionDecision,
    SharingMode,
    SourceRevision,
    StudentLanguage,
    Translation,
    TranslationKind,
    TranslationRevision,
    TranslationStatus,
    TranslationWindow,
    source_fingerprint,
)

logger = logging.getLogger(__name__)

#: Limit pliku przetłumaczonego arkusza. Arkusz zadania to kilka stron, także zeskanowanych.
MAX_PDF_MB = 20
#: Najwięcej języków na delegację (TR-01 § 1).
MAX_LANGUAGES = 2
TITLE_MAX_LENGTH = 300

MAIL_TEMPLATES = {
    "returned": "problem_translations/mail/returned",
    "approved": "problem_translations/mail/approved",
    "outdated": "problem_translations/mail/outdated",
}


def _error(message: str, code: str, http_status: int = status.HTTP_409_CONFLICT) -> DomainError:
    return DomainError(message, code, http_status)


# --- bramki -----------------------------------------------------------------------------------


def require_enabled(competition) -> None:
    """Tłumaczenia istnieją wyłącznie w konkursie z delegacjami – w innym 404 (jak DEL-01)."""
    delegations.require_delegations(competition)


def is_coordinator(user, competition) -> bool:
    from apps.accounts.services import has_role

    return has_role(user, competition, CompetitionRole.COORDINATOR)


def current_stages(competition):
    """Etapy bieżącej edycji, które mają zadania do tłumaczenia (bez piaskownicy treningowej)."""
    from apps.competitions.models import Stage, StageKind
    from apps.competitions.services import current_edition

    edition = current_edition(competition)
    if edition is None:
        return Stage.objects.none()
    return (
        Stage.objects.for_competition(competition)
        .filter(edition=edition)
        .exclude(kind=StageKind.TRAINING)
        .select_related("edition")
        .order_by("opens_at", "id")
    )


def stage_for(competition, pk: int):
    """Etap bieżącej edycji konkursu żądania albo 404 (cudzy konkurs, stara edycja, trening)."""
    stage = current_stages(competition).filter(pk=pk).first()
    if stage is None:
        raise Http404("Nie ma takiego etapu w bieżącej edycji.")
    return stage


def window_for(stage) -> TranslationWindow | None:
    return TranslationWindow.objects.filter(stage=stage).select_related("stage").first()


def sharing_mode_of(stage) -> str:
    window = window_for(stage)
    return window.sharing_mode if window is not None else SharingMode.SEPARATE


def languages_of(delegation: Delegation | None) -> list[str]:
    """Kody języków delegacji w kolejności – pierwszy jest domyślny dla uczniów."""
    if delegation is None:
        return []
    return list(
        DelegationLanguage.objects.filter(delegation=delegation)
        .order_by("position")
        .values_list("code", flat=True)
    )


def leader_access(user, competition) -> DelegationLeader | None:
    """Opiekun z delegacją w bieżącej edycji albo ``None`` – jedyna droga od konta do tłumaczeń.

    Stan delegacji (``ACTIVE``/``CLOSED``) **nie** odbiera dostępu: ``CLOSED`` znaczy w DEL-01
    „lista uczniów ostateczna” i zwykle zapada przed nocą tłumaczeń. Dostęp odbiera usunięcie
    opiekuna z delegacji (znika wiersz) albo dezaktywacja konta – obie rzeczy sprawdza ``leader_for``.
    """
    return delegations.leader_for(user, competition)


def problem_for_leader(leader: DelegationLeader, pk: int):
    """Zadanie etapu bieżącej edycji opiekuna, **w otwartym oknie** tłumaczeń, albo 404."""
    from apps.competitions.models import Problem

    problem = (
        Problem.objects.for_competition(leader.delegation.competition)
        .select_related("stage", "stage__edition")
        .filter(pk=pk, stage__edition=leader.delegation.edition)
        .first()
    )
    if problem is None:
        raise Http404("Nie ma takiego zadania.")
    window = window_for(problem.stage)
    if window is None or not window.is_open():
        raise Http404("Okno tłumaczeń tego etapu jest zamknięte.")
    if not languages_of(leader.delegation):
        raise Http404("Delegacja nie zadeklarowała języka.")
    return problem


def require_language(leader: DelegationLeader, language: str) -> None:
    if language not in languages_of(leader.delegation):
        raise Http404("Ta delegacja nie tłumaczy na ten język.")


def _owner(leader: DelegationLeader, problem) -> Delegation | None:
    return leader.delegation if sharing_mode_of(problem.stage) == SharingMode.SEPARATE else None


def find_translation(leader: DelegationLeader, problem, language: str) -> Translation | None:
    require_language(leader, language)
    return Translation.objects.filter(
        problem=problem, language=language, delegation=_owner(leader, problem)
    ).first()


# --- audyt wglądu -----------------------------------------------------------------------------


def record_view(user, action: str, obj, *, request=None, **details) -> None:
    """Wpis „kto i kiedy zobaczył” – patrz docstring modułu. Akcje z prefiksem ``translation.``."""
    audit(user, f"translation.{action}", obj, details or None, request=request)


# --- okno tłumaczeń (koordynator) ---------------------------------------------------------------


def set_window(stage, *, opens_at, closes_at, sharing_mode: str, actor, request=None) -> TranslationWindow:
    """Zapis okna. Tryb współdzielenia wolno zmienić tylko w etapie bez żadnego tłumaczenia.

    Zmiana trybu przy istniejących tłumaczeniach zostawiłaby wiersze osobne obok wspólnych – uczeń
    dostałby jedno albo drugie zależnie od tego, które zapytanie trafi pierwsze.
    """
    window = window_for(stage) or TranslationWindow(stage=stage)
    before = (
        {
            "opens_at": window.opens_at.isoformat(),
            "closes_at": window.closes_at.isoformat(),
            "mode": window.sharing_mode,
        }
        if window.pk
        else None
    )
    if (
        window.pk
        and sharing_mode != window.sharing_mode
        and Translation.objects.filter(problem__stage=stage).exists()
    ):
        raise _error(
            "Trybu tłumaczeń nie można zmienić – w etapie są już tłumaczenia.", "TRANSLATION_MODE_LOCKED"
        )
    window.opens_at, window.closes_at, window.sharing_mode = opens_at, closes_at, sharing_mode
    window.updated_by = actor
    try:
        window.full_clean()
    except ValidationError as exc:
        raise _error(
            " ".join(exc.messages), "TRANSLATION_WINDOW_INVALID", status.HTTP_400_BAD_REQUEST
        ) from exc
    window.save()
    audit(
        actor,
        "translation.window_set",
        stage,
        {
            "before": before,
            "after": {
                "opens_at": opens_at.isoformat(),
                "closes_at": closes_at.isoformat(),
                "mode": sharing_mode,
            },
        },
        request=request,
    )
    return window


# --- wersja oficjalna -----------------------------------------------------------------------------


def ensure_source(problem) -> ProblemSource:
    """Wiersz wersji oficjalnej – zakładany leniwie, przy pierwszym czytelniku (wersja 1)."""
    source = ProblemSource.objects.filter(problem=problem).first()
    if source is not None:
        return source
    try:
        with transaction.atomic():
            source = ProblemSource.objects.create(
                problem=problem, fingerprint=source_fingerprint(problem, "")
            )
            _snapshot(source, problem, actor=None)
    except IntegrityError:
        source = ProblemSource.objects.get(problem=problem)
    return source


def _snapshot(source: ProblemSource, problem, *, actor) -> None:
    SourceRevision.objects.create(
        source=source,
        version=source.version,
        title=problem.title,
        body_md=source.body_md,
        pdf_name=problem.statement_pdf.name or "",
        created_by=actor,
    )


def update_source_text(problem, body_md: str, *, actor, request=None) -> ProblemSource:
    """Koordynator zmienia tekst wersji oficjalnej – zmiana podnosi wersję (patrz ``_bump``)."""
    body_md = (body_md or "").replace("\r\n", "\n")
    if len(body_md) > BODY_MAX_LENGTH:
        raise _error("Tekst jest za długi.", "TRANSLATION_TOO_LONG", status.HTTP_400_BAD_REQUEST)
    with transaction.atomic():
        source = ProblemSource.objects.select_for_update().get(pk=ensure_source(problem).pk)
        if source.body_md == body_md:
            return source
        source.body_md = body_md
        _bump(source, problem, actor=actor, request=request)
    return source


def sync_source(problem, *, actor=None) -> None:
    """Wołane z sygnału ``post_save`` zadania: nowy PDF albo tytuł to nowa wersja oficjalna.

    Zadanie bez wiersza źródła nie ma tłumaczeń (wiersz powstaje przy pierwszym), więc nie ma kogo
    ostrzegać – wersja 1 powstanie później, od razu z nowym plikiem.
    """
    with transaction.atomic():
        source = ProblemSource.objects.select_for_update().filter(problem=problem).first()
        if source is None:
            return
        if source_fingerprint(problem, source.body_md) == source.fingerprint:
            return
        _bump(source, problem, actor=actor, request=None)


def _bump(source: ProblemSource, problem, *, actor, request) -> None:
    """Nowa wersja oficjalna: numer, migawka, znacznik „nieaktualne” i list do tłumaczy."""
    now = timezone.now()
    source.version += 1
    source.fingerprint = source_fingerprint(problem, source.body_md)
    source.updated_at = now
    source.updated_by = actor if getattr(actor, "is_authenticated", False) else None
    source.save()
    _snapshot(source, problem, actor=source.updated_by)
    affected = list(
        Translation.objects.filter(
            problem=problem, source_version__lt=source.version, outdated_since__isnull=True
        )
    )
    Translation.objects.filter(pk__in=[t.pk for t in affected]).update(outdated_since=now)
    audit(
        actor,
        "translation.source_changed",
        problem,
        {"version": source.version, "outdated_translations": len(affected)},
        request=request,
    )
    if affected:
        transaction.on_commit(lambda: _notify_outdated(problem, source.version, affected))


# --- języki delegacji i uczniów (opiekun) ----------------------------------------------------------


def declare_languages(leader: DelegationLeader, codes: list[str], *, actor, request=None) -> list[str]:
    """Jeden albo dwa języki delegacji; pierwszy jest domyślny dla uczniów.

    Języka, w którym delegacja ma już **wysłane** tłumaczenie (jest choć jedna wersja), nie wolno
    usunąć – komisja mogła je zatwierdzić, a uczniowie mieć je przypisane.
    """
    cleaned: list[str] = []
    for code in codes:
        if code and code not in cleaned:
            cleaned.append(code)
    if not 1 <= len(cleaned) <= MAX_LANGUAGES:
        raise _error(
            _("Wybierz jeden albo dwa języki."), "TRANSLATION_LANGUAGES_COUNT", status.HTTP_400_BAD_REQUEST
        )
    unknown = [code for code in cleaned if not languages.is_known(code)]
    if unknown:
        raise _error(_("Nieznany język."), "TRANSLATION_LANGUAGE_UNKNOWN", status.HTTP_400_BAD_REQUEST)
    delegation = leader.delegation
    with transaction.atomic():
        Delegation.objects.select_for_update().get(pk=delegation.pk)
        current = languages_of(delegation)
        removed = [code for code in current if code not in cleaned]
        if (
            removed
            and TranslationRevision.objects.filter(
                translation__delegation=delegation, translation__language__in=removed
            ).exists()
        ):
            raise _error(
                _("Nie można usunąć języka, w którym delegacja wysłała już tłumaczenie."),
                "TRANSLATION_LANGUAGE_IN_USE",
            )
        DelegationLanguage.objects.filter(delegation=delegation).delete()
        DelegationLanguage.objects.bulk_create(
            [
                DelegationLanguage(delegation=delegation, code=code, position=i)
                for i, code in enumerate(cleaned, 1)
            ]
        )
        if removed:
            StudentLanguage.objects.filter(participant__delegation=delegation, code__in=removed).delete()
    if current != cleaned:
        audit(
            actor,
            "translation.languages_declared",
            delegation,
            {"before": current, "after": cleaned},
            request=request,
        )
    return cleaned


def student_language(participant: Participant) -> str | None:
    """Język ucznia: nadpisanie opiekuna (o ile język jest wciąż zadeklarowany) albo język 1 delegacji."""
    declared = languages_of(participant.delegation) if participant.delegation_id else []
    if not declared:
        return None
    override = StudentLanguage.objects.filter(participant=participant).values_list("code", flat=True).first()
    return override if override in declared else declared[0]


def student_overrides(delegation: Delegation) -> dict[int, str]:
    return dict(
        StudentLanguage.objects.filter(participant__delegation=delegation).values_list(
            "participant_id", "code"
        )
    )


def set_student_language(
    leader: DelegationLeader, participant_pk: int, code: str, *, actor, request=None
) -> None:
    """Nadpisanie języka ucznia **tej** delegacji (``student_of`` → 404 dla cudzego). Pusty kod = domyślny."""
    participant = delegations.student_of(leader, participant_pk)
    if code and code not in languages_of(leader.delegation):
        raise _error(
            _("Ten język nie jest językiem delegacji."),
            "TRANSLATION_LANGUAGE_UNKNOWN",
            status.HTTP_400_BAD_REQUEST,
        )
    if code:
        StudentLanguage.objects.update_or_create(
            participant=participant, defaults={"code": code, "set_by": actor, "set_at": timezone.now()}
        )
    else:
        StudentLanguage.objects.filter(participant=participant).delete()
    audit(actor, "translation.student_language_set", participant, {"language": code or None}, request=request)


# --- praca opiekuna nad tłumaczeniem --------------------------------------------------------------


def _locked(translation: Translation) -> DomainError:
    if translation.status == TranslationStatus.APPROVED:
        return _error(_("Tłumaczenie jest zatwierdzone i zablokowane."), "TRANSLATION_LOCKED")
    return _error(
        _("Tłumaczenie czeka na decyzję komisji – cofnij wysłanie, żeby je poprawić."), "TRANSLATION_LOCKED"
    )


def _for_update(leader: DelegationLeader, problem, language: str, *, actor) -> Translation:
    """Tłumaczenie pod blokadą wiersza – zakładane przy pierwszym zapisie (nie przy otwarciu edytora).

    Blokada, bo w trybie wspólnym ten sam wiersz piszą opiekunowie kilku krajów naraz: autozapis
    jednego nie może się przepleść z „Wyślij” drugiego.
    """
    require_language(leader, language)
    owner = _owner(leader, problem)
    source = ensure_source(problem)
    try:
        with transaction.atomic():
            Translation.objects.get_or_create(
                problem=problem,
                language=language,
                delegation=owner,
                defaults={"source_version": source.version, "updated_by": actor},
            )
    except IntegrityError:
        pass  # równoległy pierwszy zapis drugiego opiekuna – wiersz już jest
    return Translation.objects.select_for_update().get(problem=problem, language=language, delegation=owner)


@transaction.atomic
def save_draft(
    leader: DelegationLeader, problem, language: str, *, title: str, body_md: str, actor
) -> Translation:
    """Autozapis szkicu tekstowego. Bez audytu – to zapis co kilka sekund, nie wgląd ani decyzja."""
    body_md = (body_md or "").replace("\r\n", "\n")
    if len(body_md) > BODY_MAX_LENGTH:
        raise _error(_("Tekst jest za długi."), "TRANSLATION_TOO_LONG", status.HTTP_400_BAD_REQUEST)
    translation = _for_update(leader, problem, language, actor=actor)
    if not translation.is_editable_status:
        raise _locked(translation)
    translation.title = (title or "").strip()[:TITLE_MAX_LENGTH]
    translation.body_md = body_md
    translation.kind = TranslationKind.TEXT
    translation.updated_at = timezone.now()
    translation.updated_by = actor
    translation.save(update_fields=["title", "body_md", "kind", "updated_at", "updated_by"])
    return translation


def _scan(data: bytes) -> None:
    """Skan antywirusowy **przed** zapisem. Brak clamd = odmowa: plik trafi do uczniów (fail-closed)."""
    import io

    from apps.submissions.antivirus import VERDICT_INFECTED, ClamAVError, scan_stream

    try:
        verdict, _signature = scan_stream(io.BytesIO(data), size=len(data))
    except ClamAVError as exc:
        logger.warning("Skan PDF-u tłumaczenia niedostępny: %s", exc)
        raise _error(
            _("Nie udało się sprawdzić pliku programem antywirusowym. Spróbuj ponownie za chwilę."),
            "TRANSLATION_SCAN_UNAVAILABLE",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    if verdict == VERDICT_INFECTED:
        raise _error(
            _("Plik został odrzucony przez program antywirusowy."),
            "TRANSLATION_FILE_INFECTED",
            status.HTTP_400_BAD_REQUEST,
        )


def upload_pdf(
    leader: DelegationLeader, problem, language: str, upload, *, title: str = "", actor, request=None
) -> Translation:
    """Przetłumaczony arkusz PDF zamiast tekstu: walidacja treści, liczba stron, skan, zapis prywatny."""
    from apps.submissions.validators import validate_upload

    from .pdf import MAX_PAGES, PdfUnreadable, page_count

    validate_upload(upload, ["pdf"], MAX_PDF_MB)
    data = upload.read()
    try:
        pages = page_count(data)
    except PdfUnreadable as exc:
        raise _error(
            _("Nie można odczytać pliku PDF (uszkodzony albo zaszyfrowany)."),
            "TRANSLATION_PDF_UNREADABLE",
            status.HTTP_400_BAD_REQUEST,
        ) from exc
    if pages > MAX_PAGES:
        raise _error(_("Plik ma za dużo stron."), "TRANSLATION_PDF_TOO_LONG", status.HTTP_400_BAD_REQUEST)
    _scan(data)
    with transaction.atomic():
        translation = _for_update(leader, problem, language, actor=actor)
        if not translation.is_editable_status:
            raise _locked(translation)
        previous = translation.pdf.name
        translation.pdf.save(f"{uuid.uuid4().hex}.pdf", ContentFile(data), save=False)
        translation.pdf_sha256 = hashlib.sha256(data).hexdigest()
        translation.kind = TranslationKind.PDF
        if title:
            translation.title = title.strip()[:TITLE_MAX_LENGTH]
        translation.updated_at = timezone.now()
        translation.updated_by = actor
        translation.save()
        audit(
            actor,
            "translation.pdf_uploaded",
            translation,
            {"language": language, "pages": pages, "sha256": translation.pdf_sha256},
            request=request,
        )
    if previous and not TranslationRevision.objects.filter(pdf=previous).exists():
        # Plik, którego nie wskazuje żadna wysłana wersja, jest szkicem – nie trzymamy tajnych śmieci.
        transaction.on_commit(lambda: translation.pdf.storage.delete(previous))
    return translation


@transaction.atomic
def submit(leader: DelegationLeader, problem, language: str, *, actor, request=None) -> TranslationRevision:
    """„Wyślij do akceptacji”: migawka (nowa wersja), stan ``SUBMITTED``, wersja źródła bieżąca."""
    translation = _for_update(leader, problem, language, actor=actor)
    if not translation.is_editable_status:
        raise _locked(translation)
    if not translation.has_content:
        raise _error(_("Tłumaczenie jest puste."), "TRANSLATION_EMPTY", status.HTTP_400_BAD_REQUEST)
    source = ensure_source(problem)
    number = (translation.revisions.aggregate(top=Max("number"))["top"] or 0) + 1
    revision = TranslationRevision.objects.create(
        translation=translation,
        number=number,
        kind=translation.kind,
        title=translation.title,
        body_md=translation.body_md if translation.kind == TranslationKind.TEXT else "",
        pdf=translation.pdf.name if translation.kind == TranslationKind.PDF else "",
        pdf_sha256=translation.pdf_sha256 if translation.kind == TranslationKind.PDF else "",
        source_version=source.version,
        submitted_by=actor,
    )
    translation.status = TranslationStatus.SUBMITTED
    translation.source_version = source.version
    translation.outdated_since = None
    translation.review_comment = ""
    translation.updated_at = timezone.now()
    translation.updated_by = actor
    translation.save()
    audit(
        actor,
        "translation.submitted",
        translation,
        {"revision": number, "language": language, "source_version": source.version},
        request=request,
    )
    return revision


@transaction.atomic
def withdraw(leader: DelegationLeader, problem, language: str, *, actor, request=None) -> Translation:
    """Cofnięcie wysłania przed decyzją komisji – wersja zostaje w historii jako „wycofana”."""
    translation = _for_update(leader, problem, language, actor=actor)
    if translation.status != TranslationStatus.SUBMITTED:
        raise _error(_("Tylko wysłane tłumaczenie można cofnąć."), "TRANSLATION_NOT_SUBMITTED")
    latest = translation.revisions.order_by("-number").first()
    if latest is not None and not latest.decision:
        latest.decision = RevisionDecision.WITHDRAWN
        latest.decided_at = timezone.now()
        latest.decided_by = actor
        latest.save(update_fields=["decision", "decided_at", "decided_by"])
    translation.status = TranslationStatus.DRAFT
    translation.save(update_fields=["status"])
    audit(actor, "translation.withdrawn", translation, {"language": language}, request=request)
    return translation


@transaction.atomic
def reopen(leader: DelegationLeader, problem, language: str, *, actor, request=None) -> Translation:
    """Zatwierdzone, ale **nieaktualne** tłumaczenie wraca do szkicu – uczniowie dalej widzą zatwierdzone."""
    translation = _for_update(leader, problem, language, actor=actor)
    if translation.status != TranslationStatus.APPROVED or not translation.is_outdated:
        raise _error(
            _("Zatwierdzone tłumaczenie można otworzyć ponownie tylko po zmianie wersji oficjalnej."),
            "TRANSLATION_LOCKED",
        )
    translation.status = TranslationStatus.DRAFT
    translation.save(update_fields=["status"])
    audit(actor, "translation.reopened", translation, {"language": language}, request=request)
    return translation


# --- komisja (koordynator) -------------------------------------------------------------------------


def translation_for_coordinator(competition, pk: int) -> Translation:
    translation = (
        Translation.objects.for_competition(competition)
        .select_related("problem", "problem__stage", "delegation", "delegation__country", "approved_revision")
        .filter(pk=pk)
        .first()
    )
    if translation is None:
        raise Http404("Nie ma takiego tłumaczenia.")
    return translation


def latest_revision(translation: Translation) -> TranslationRevision | None:
    return translation.revisions.order_by("-number").first()


@transaction.atomic
def approve(translation: Translation, *, actor, request=None) -> TranslationRevision:
    translation = Translation.objects.select_for_update().get(pk=translation.pk)
    if translation.status != TranslationStatus.SUBMITTED:
        raise _error(
            "Zatwierdzić można tylko tłumaczenie wysłane do akceptacji.", "TRANSLATION_NOT_SUBMITTED"
        )
    revision = latest_revision(translation)
    source = ensure_source(translation.problem)
    if revision is None or revision.source_version < source.version:
        raise _error(
            "Wersja oficjalna zmieniła się po wysłaniu – zwróć tłumaczenie do aktualizacji.",
            "TRANSLATION_OUTDATED",
        )
    now = timezone.now()
    revision.decision, revision.decided_at, revision.decided_by = RevisionDecision.APPROVED, now, actor
    revision.save(update_fields=["decision", "decided_at", "decided_by"])
    translation.status = TranslationStatus.APPROVED
    translation.approved_revision = revision
    translation.review_comment = ""
    translation.save(update_fields=["status", "approved_revision", "review_comment"])
    audit(actor, "translation.approved", translation, {"revision": revision.number}, request=request)
    transaction.on_commit(lambda: _notify(translation, "approved", request=request))
    return revision


@transaction.atomic
def return_translation(translation: Translation, comment: str, *, actor, request=None) -> Translation:
    """Zwrot do poprawy z komentarzem. Z zatwierdzonego też – uczniowie zachowują zatwierdzoną wersję."""
    comment = (comment or "").strip()
    if not comment:
        raise _error(
            "Zwrot wymaga komentarza dla tłumaczy.",
            "TRANSLATION_COMMENT_REQUIRED",
            status.HTTP_400_BAD_REQUEST,
        )
    translation = Translation.objects.select_for_update().get(pk=translation.pk)
    if translation.status not in (TranslationStatus.SUBMITTED, TranslationStatus.APPROVED):
        raise _error("Zwrócić można tłumaczenie wysłane albo zatwierdzone.", "TRANSLATION_NOT_SUBMITTED")
    revision = latest_revision(translation)
    if translation.status == TranslationStatus.SUBMITTED and revision is not None and not revision.decision:
        revision.decision, revision.decided_at, revision.decided_by = (
            RevisionDecision.RETURNED,
            timezone.now(),
            actor,
        )
        revision.comment = comment[:4000]
        revision.save(update_fields=["decision", "decided_at", "decided_by", "comment"])
    translation.status = TranslationStatus.RETURNED
    translation.review_comment = comment[:4000]
    translation.save(update_fields=["status", "review_comment"])
    audit(
        actor,
        "translation.returned",
        translation,
        {"revision": revision.number if revision else None},
        request=request,
    )
    transaction.on_commit(lambda: _notify(translation, "returned", request=request, comment=comment))
    return translation


# --- różnice ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DiffLine:
    tag: str  # "equal" | "insert" | "delete" | "skip"
    text: str


#: Ile niezmienionych wierszy zostawić wokół zmiany – reszta zwija się do „…”.
DIFF_CONTEXT = 2


def diff_lines(old: str, new: str) -> list[DiffLine]:
    """Różnice wierszami (``difflib``), z kontekstem – do szablonu, który sam ucieka treść."""
    a, b = (old or "").splitlines(), (new or "").splitlines()
    out: list[DiffLine] = []
    for group in difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(DIFF_CONTEXT):
        if out:
            out.append(DiffLine("skip", "…"))
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                out.extend(DiffLine("equal", line) for line in a[i1:i2])
                continue
            if tag in ("replace", "delete"):
                out.extend(DiffLine("delete", line) for line in a[i1:i2])
            if tag in ("replace", "insert"):
                out.extend(DiffLine("insert", line) for line in b[j1:j2])
    return out


def revision_diff(revision: TranslationRevision) -> tuple[TranslationRevision | None, list[DiffLine]]:
    """Różnica wersji wobec poprzedniej wysłanej (tekst; przy PDF – tylko fakt zmiany pliku)."""
    previous = revision.translation.revisions.filter(number__lt=revision.number).order_by("-number").first()
    if previous is None:
        return None, []
    old = (
        f"# {previous.title}\n{previous.body_md}"
        if previous.kind == TranslationKind.TEXT
        else f"[PDF {previous.pdf_sha256[:12]}]"
    )
    new = (
        f"# {revision.title}\n{revision.body_md}"
        if revision.kind == TranslationKind.TEXT
        else f"[PDF {revision.pdf_sha256[:12]}]"
    )
    return previous, diff_lines(old, new)


def source_diff(problem, since_version: int) -> list[DiffLine]:
    """Co zmieniło się w wersji oficjalnej od ``since_version`` – dla tłumacza nieaktualnego tekstu."""
    source = ensure_source(problem)
    old = SourceRevision.objects.filter(source=source, version=since_version).first()
    new = SourceRevision.objects.filter(source=source, version=source.version).first()
    if old is None or new is None:
        return []

    def flat(rev: SourceRevision) -> str:
        return f"# {rev.title}\n[PDF: {rev.pdf_name.rsplit('/', 1)[-1] or '—'}]\n{rev.body_md}"

    return diff_lines(flat(old), flat(new))


# --- uczeń -----------------------------------------------------------------------------------------


def approved_for_student(participant: Participant | None, problem) -> TranslationRevision | None:
    """Zatwierdzona wersja zadania w języku ucznia – wyłącznie po otwarciu etapu, inaczej ``None``."""
    if participant is None or not participant.delegation_id:
        return None
    stage = problem.stage
    if not stage.has_opened() or participant.delegation.edition_id != stage.edition_id:
        return None
    language = student_language(participant)
    if language is None:
        return None
    owner = participant.delegation if sharing_mode_of(stage) == SharingMode.SEPARATE else None
    translation = (
        Translation.objects.filter(problem=problem, language=language, delegation=owner)
        .select_related("approved_revision")
        .first()
    )
    if translation is None or translation.approved_revision is None:
        return None
    return translation.approved_revision


def problem_for_student(participant: Participant, pk: int):
    """Zadanie otwartego etapu edycji ucznia z zatwierdzonym tłumaczeniem – albo 404."""
    from apps.competitions.models import Problem

    problem = (
        Problem.objects.for_competition(participant.competition)
        .select_related("stage", "stage__edition")
        .filter(pk=pk)
        .first()
    )
    if problem is None:
        raise Http404("Nie ma takiego zadania.")
    revision = approved_for_student(participant, problem)
    if revision is None:
        raise Http404("Brak zatwierdzonego tłumaczenia.")
    return problem, revision


# --- eksport do druku ------------------------------------------------------------------------------


@dataclass
class ExportVariant:
    language: str
    delegation: Delegation | None
    approved: int
    total: int

    @property
    def label(self) -> str:
        name = languages.english_name(self.language)
        return f"{name} – {self.delegation.country.name}" if self.delegation is not None else name


def export_variants(stage) -> list[ExportVariant]:
    """Warianty pakietu: język (tryb wspólny) albo para język–delegacja (tryb osobny)."""
    problems = list(stage.problems.all())
    keys: dict[tuple[str, int | None], int] = {}
    owners: dict[int, Delegation] = {}
    for translation in Translation.objects.filter(problem__stage=stage).select_related("delegation__country"):
        key = (translation.language, translation.delegation_id)
        keys.setdefault(key, 0)
        if translation.approved_revision_id:
            keys[key] += 1
        if translation.delegation_id:
            owners[translation.delegation_id] = translation.delegation
    variants = [
        ExportVariant(language, owners.get(owner) if owner else None, approved, len(problems))
        for (language, owner), approved in keys.items()
    ]
    return sorted(
        variants,
        key=lambda v: (languages.english_name(v.language), v.delegation.country.name if v.delegation else ""),
    )


def export_rows(stage, language: str, delegation: Delegation | None) -> list[tuple]:
    """``(zadanie, zatwierdzona wersja | None)`` po numerach zadań – wspólne dla PDF-u i widoku druku."""
    translations = {
        t.problem_id: t
        for t in Translation.objects.filter(
            problem__stage=stage, language=language, delegation=delegation
        ).select_related("approved_revision")
    }
    rows = []
    for problem in stage.problems.order_by("number"):
        translation = translations.get(problem.pk)
        rows.append((problem, translation.approved_revision if translation else None))
    return rows


def export_delegation(stage, delegation_pk: str | None) -> Delegation | None:
    """Delegacja z parametru eksportu – wyłącznie z konkursu i edycji etapu (inaczej 404)."""
    if not delegation_pk:
        return None
    delegation = (
        Delegation.objects.filter(pk=delegation_pk, edition=stage.edition).select_related("country").first()
        if str(delegation_pk).isdigit()
        else None
    )
    if delegation is None:
        raise Http404("Nie ma takiej delegacji.")
    return delegation


def _read(field) -> bytes:
    with field.open("rb") as handle:
        return handle.read()


def heading_for(problem, revision: TranslationRevision) -> str:
    return f"{problem.number}. {revision.title or problem.title}"


def export_pdf(stage, language: str, delegation: Delegation | None, *, actor, request=None) -> bytes:
    from .pdf import BundleItem, bundle

    rows = [(problem, revision) for problem, revision in export_rows(stage, language, delegation) if revision]
    if not rows:
        raise _error(
            "Brak zatwierdzonych tłumaczeń w tym języku.",
            "TRANSLATION_EXPORT_EMPTY",
            status.HTTP_404_NOT_FOUND,
        )
    items = [
        BundleItem(
            heading=heading_for(problem, revision),
            pdf=_read(revision.pdf) if revision.kind == TranslationKind.PDF else None,
            body_md=revision.body_md,
        )
        for problem, revision in rows
    ]
    footer = f"{stage.display_name} · {languages.english_name(language)}"
    if delegation is not None:
        footer += f" · {delegation.country.name}"
    data = bundle(items, footer)
    record_view(
        actor,
        "exported",
        stage,
        request=request,
        language=language,
        delegation=delegation.pk if delegation else None,
    )
    return data


# --- pliki z znakiem wodnym (opiekun) -------------------------------------------------------------


def watermark_lines(leader: DelegationLeader) -> list[str]:
    country = leader.delegation.country
    stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M")
    return [f"{(country.code or '').upper()} · CONFIDENTIAL", f"{country.name} · {stamp} · #{leader.user_id}"]


def source_pdf_for_leader(leader: DelegationLeader, problem, *, request=None) -> bytes:
    from .pdf import watermark

    if not problem.statement_pdf:
        raise Http404("Zadanie nie ma pliku PDF.")
    data = watermark(_read(problem.statement_pdf), watermark_lines(leader))
    record_view(leader.user, "source_downloaded", problem, request=request, delegation=leader.delegation_id)
    return data


def translation_pdf_for_leader(leader: DelegationLeader, translation: Translation, *, request=None) -> bytes:
    from .pdf import watermark

    if not translation.pdf:
        raise Http404("Tłumaczenie nie ma pliku PDF.")
    data = watermark(_read(translation.pdf), watermark_lines(leader))
    record_view(leader.user, "file_downloaded", translation, request=request, delegation=leader.delegation_id)
    return data


# --- listy ----------------------------------------------------------------------------------------


def _recipients(translation: Translation):
    """Opiekunowie, których dotyczy tłumaczenie: jego delegacji albo (wspólne) wszystkich delegacji języka."""
    from apps.accounts.models import User

    if translation.delegation_id:
        condition = Q(delegation_leaderships__delegation_id=translation.delegation_id)
    else:
        condition = Q(
            delegation_leaderships__delegation__translation_languages__code=translation.language,
            delegation_leaderships__edition_id=translation.problem.stage.edition_id,
        )
    return User.objects.filter(condition, is_active=True).distinct()


def _send(user, competition, template: str, context: dict, *, request=None) -> None:
    from apps.accounts.activation import absolute_url, queue_mail
    from apps.accounts.preferences import language_for
    from apps.tenancy import branding

    context = {**context, "link": absolute_url(reverse("web:delegation-translations"), request, competition)}
    with language_for(user, competition):
        context["brand"] = branding.brand_names(competition)
        subject = render_to_string(f"{template}_subject.txt", context).strip().replace("\n", " ")
        body = render_to_string(f"{template}_body.txt", context)
    queue_mail(subject, body, user.email, competition=competition)


def _notify(translation: Translation, event: str, *, request=None, comment: str = "") -> None:
    problem = translation.problem
    competition = problem.stage.edition.competition
    context = {
        "number": problem.number,
        "language": languages.english_name(translation.language),
        "comment": comment,
    }
    for user in _recipients(translation):
        _send(user, competition, MAIL_TEMPLATES[event], context, request=request)


def _notify_outdated(problem, version: int, translations: list[Translation]) -> None:
    """Jeden list na opiekuna – z listą języków, których dotyczy zmiana."""
    competition = problem.stage.edition.competition
    per_user: dict[int, tuple] = {}
    for translation in translations:
        for user in _recipients(translation):
            entry = per_user.setdefault(user.pk, (user, set()))
            entry[1].add(languages.english_name(translation.language))
    for user, names in per_user.values():
        context = {"number": problem.number, "version": version, "languages": ", ".join(sorted(names))}
        _send(user, competition, MAIL_TEMPLATES["outdated"], context)


# --- RODO -----------------------------------------------------------------------------------------


def export_section(user) -> dict:
    """Sekcja eksportu danych konta: język przypisany uczniowi i tłumaczenia, które konto wysłało.

    Bez treści tłumaczeń – to materiał zawodów, a nie dane o osobie; autorstwo (kiedy i co wysłał)
    jest daną osobową opiekuna.
    """
    languages_rows = [
        {
            "konkurs": row.participant.competition.slug,
            "jezyk": row.code,
            "ustawiono": timezone.localtime(row.set_at).isoformat(),
        }
        for row in StudentLanguage.objects.filter(participant__user=user).select_related(
            "participant__competition"
        )
    ]
    submitted = [
        {
            "zadanie": revision.translation.problem_id,
            "jezyk": revision.translation.language,
            "wersja": revision.number,
            "wyslano": timezone.localtime(revision.submitted_at).isoformat(),
        }
        for revision in TranslationRevision.objects.filter(submitted_by=user).select_related("translation")
    ]
    return {"jezyk_ucznia": languages_rows, "wyslane_tlumaczenia": submitted}
