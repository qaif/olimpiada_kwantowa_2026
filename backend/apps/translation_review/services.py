"""Reguły przeglądu tłumaczeń: kto co może, propozycje, głosy, zatwierdzanie i zgłoszenia.

Każda czynność sprawdza uprawnienie **tutaj**, a nie w szablonie – widok, który zapomni zapytać,
dostanie ``DomainError`` (403) z serwisu, a nie cichy zapis. Każda zmiana zostawia wpis audytu
bez treści zgłoszeń (tekst tłumaczenia nie jest daną osobową, ale komentarz w zgłoszeniu może nią
być – w audycie zostaje sam identyfikator).

Poziomy roli (L10N-01 § 2): tłumacz proponuje i głosuje, recenzent dodatkowo zatwierdza, odrzuca,
cofa nakładkę, potwierdza obecne tłumaczenie i zamyka zgłoszenia. Superkoordynator jest
recenzentem każdego języka. Koordynator konkursu nadaje wyłącznie poziom tłumacza i wyłącznie
osobom związanym ze swoim konkursem, bo zatwierdzona poprawka działa na **całej** platformie.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Case, Count, When
from django.http import Http404
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import CompetitionRole, Membership, Participant, User
from apps.accounts.services import has_role
from apps.accounts.super_coordinator import is_super_coordinator
from apps.core.api import DomainError
from apps.core.models import audit

from . import catalogs, runtime
from .models import (
    GrantLevel,
    ReportStatus,
    SuggestionStatus,
    TranslationOverride,
    TranslationReport,
    TranslationSuggestion,
    TranslationVote,
    TranslatorGrant,
)
from .validation import clean_translation

#: Atrybut na obiekcie użytkownika z uprawnieniami tłumacza – jedno zapytanie na żądanie.
GRANTS_ATTR = "_translation_review_grants"
#: Flaga „to konto jest tłumaczem” dla odnośnika w stopce (L10N-01 § 8). W cache'u, bo stopka
#: jest na każdej stronie, a pytanie o rolę kosztowałoby zapytanie przy każdym odsłonięciu.
TRANSLATOR_FLAG_KEY = "translation-review:translator:{user_id}"
TRANSLATOR_FLAG_SECONDS = 600
#: Limity długości pól zgłoszenia (te same, co w modelu).
PAGE_MAX = 300
PHRASE_MAX = 300
COMMENT_MAX = 2000


class TranslationDenied(DomainError):
    status_code = 403
    default_code = "TRANSLATION_FORBIDDEN"
    default_detail = "Brak uprawnień do tłumaczeń tego języka."


class TranslationInvalid(DomainError):
    default_code = "TRANSLATION_INVALID"
    default_detail = "Tłumaczenie nie spełnia reguł."


# --- uprawnienia ---------------------------------------------------------------------------------


def user_grants(user) -> dict[str, str]:
    """``{język: poziom}`` tego konta. Konto nieaktywne nie ma uprawnień (jak w ``has_role``)."""
    if not user or not getattr(user, "is_authenticated", False) or not user.is_active:
        return {}
    cached = getattr(user, GRANTS_ATTR, None)
    if cached is None:
        cached = dict(TranslatorGrant.objects.filter(user=user).values_list("language", "level"))
        setattr(user, GRANTS_ATTR, cached)
    return cached


def forget_user(user) -> None:
    if hasattr(user, GRANTS_ATTR):
        delattr(user, GRANTS_ATTR)
    cache.delete(TRANSLATOR_FLAG_KEY.format(user_id=user.pk))


def _known(language: str) -> bool:
    return language in catalogs.review_languages()


def can_translate(user, language: str) -> bool:
    if not _known(language):
        return False
    return is_super_coordinator(user) or language in user_grants(user)


def can_review(user, language: str) -> bool:
    if not _known(language):
        return False
    return is_super_coordinator(user) or user_grants(user).get(language) == GrantLevel.REVIEWER


def languages_for(user) -> list[str]:
    """Języki, które ta osoba może przeglądać – w kolejności ``LANGUAGES``."""
    if is_super_coordinator(user):
        return catalogs.review_languages()
    granted = user_grants(user)
    return [code for code in catalogs.review_languages() if code in granted]


def is_translator(user) -> bool:
    """Czy pokazać „Zgłoś tłumaczenie” w stopce. Z cache'u (unieważnianego przy nadaniu i odebraniu)."""
    if not user or not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    key = TRANSLATOR_FLAG_KEY.format(user_id=user.pk)
    flag = cache.get(key)
    if flag is None:
        flag = bool(languages_for(user))
        cache.set(key, flag, TRANSLATOR_FLAG_SECONDS)
    return flag


def _require(allowed: bool) -> None:
    if not allowed:
        raise TranslationDenied(_("Nie masz uprawnień do tłumaczeń tego języka."))


# --- nadawanie roli -----------------------------------------------------------------------------


def multilingual(competition) -> bool:
    return competition is not None and len(competition.ui_languages) > 1


def can_manage_grants(user, competition) -> bool:
    """Superkoordynator zawsze; koordynator – w konkursie z więcej niż jednym językiem interfejsu."""
    if is_super_coordinator(user):
        return True
    return multilingual(competition) and has_role(user, competition, CompetitionRole.COORDINATOR)


def is_linked(user, competition) -> bool:
    """Czy konto ma cokolwiek wspólnego z konkursem: członkostwo albo profil uczestnika."""
    if competition is None:
        return False
    return (
        Membership.objects.filter(user=user, competition=competition).exists()
        or Participant.objects.for_competition(competition).filter(user=user).exists()
    )


def grants_visible_to(actor, competition):
    queryset = TranslatorGrant.objects.select_related("user", "granted_by").order_by(
        "language", "user__email"
    )
    if is_super_coordinator(actor):
        return queryset
    if competition is None:
        return queryset.none()
    members = Membership.objects.filter(competition=competition).values("user_id")
    participants = Participant.objects.for_competition(competition).values("user_id")
    return queryset.filter(user_id__in=members.union(participants))


@transaction.atomic
def grant(*, actor, competition, email: str, language: str, level: str, request=None) -> TranslatorGrant:
    if not can_manage_grants(actor, competition):
        raise TranslationDenied(_("Nie możesz nadawać ról tłumaczy."))
    if not _known(language):
        raise TranslationInvalid(_("Nieznany język."))
    if level not in GrantLevel.values:
        raise TranslationInvalid(_("Nieznany poziom uprawnień."))
    superuser = is_super_coordinator(actor)
    if level == GrantLevel.REVIEWER and not superuser:
        raise TranslationDenied(_("Rolę recenzenta tłumaczeń nadaje wyłącznie superkoordynator."))
    user = User.objects.filter(email__iexact=(email or "").strip(), is_active=True).first()
    # Jedna odpowiedź na „nie ma konta” i „konto spoza konkursu” – formularz nie może służyć do
    # sprawdzania, czy ktoś ma konto na platformie.
    if user is None or (not superuser and not is_linked(user, competition)):
        raise TranslationInvalid(_("Nie znaleziono aktywnego konta z tym adresem w tym konkursie."))
    existing = TranslatorGrant.objects.select_for_update().filter(user=user, language=language).first()
    if existing is not None and existing.level == GrantLevel.REVIEWER and not superuser:
        raise TranslationDenied(_("Tę osobę może zmienić wyłącznie superkoordynator."))
    if existing is None:
        existing = TranslatorGrant.objects.create(user=user, language=language, level=level, granted_by=actor)
    else:
        existing.level = level
        existing.granted_by = actor
        existing.granted_at = timezone.now()
        existing.save(update_fields=["level", "granted_by", "granted_at"])
    forget_user(user)
    audit(
        actor,
        "translation.grant",
        existing,
        {"user_id": user.pk, "language": language, "level": level},
        request=request,
    )
    return existing


@transaction.atomic
def revoke(*, actor, competition, grant_obj: TranslatorGrant, request=None) -> None:
    if not can_manage_grants(actor, competition):
        raise TranslationDenied(_("Nie możesz odbierać ról tłumaczy."))
    if not is_super_coordinator(actor):
        if grant_obj.level == GrantLevel.REVIEWER or not is_linked(grant_obj.user, competition):
            raise TranslationDenied(_("Tę osobę może zmienić wyłącznie superkoordynator."))
    diff = {"user_id": grant_obj.user_id, "language": grant_obj.language, "level": grant_obj.level}
    audit(actor, "translation.revoke", grant_obj, diff, request=request)
    user = grant_obj.user
    grant_obj.delete()
    forget_user(user)


# --- napisy -------------------------------------------------------------------------------------


@dataclass
class StringView:
    """Wiersz ekranu: napis z katalogu + stan z bazy."""

    row: catalogs.Row
    pivot: str
    override: str | None
    pending: int

    @property
    def current(self) -> str:
        return self.override if self.override is not None else self.row.translation

    @property
    def status(self) -> str:
        if self.override is not None or self.row.reviewed:
            return "reviewed"
        return "machine" if self.row.translation else "untranslated"


STATUSES = ("untranslated", "machine", "reviewed", "pending")


def row_or_404(language: str, key: str) -> catalogs.Row:
    row = catalogs.index(language).by_key.get(key) if _known(language) else None
    if row is None:
        raise Http404("Nie ma takiego napisu.")
    return row


def _pivot(language: str, key: str) -> str:
    if language == "en" or "en" not in catalogs.review_languages():
        return ""
    row = catalogs.index("en").by_key.get(key)
    if row is None:
        return ""
    override = (
        TranslationOverride.objects.filter(language="en", key=key).values_list("text", flat=True).first()
    )
    return override if override is not None else row.translation


def strings(language: str, *, status: str = "", query: str = "") -> list[StringView]:
    """Napisy języka z filtrami. Trzy zapytania niezależnie od liczby napisów."""
    rows = catalogs.index(language).rows
    pivot_rows = (
        catalogs.index("en").by_key if language != "en" and "en" in catalogs.review_languages() else {}
    )
    overrides = dict(TranslationOverride.objects.filter(language=language).values_list("key", "text"))
    pivot_overrides = (
        dict(TranslationOverride.objects.filter(language="en").values_list("key", "text"))
        if pivot_rows
        else {}
    )
    pending = dict(
        TranslationSuggestion.objects.filter(language=language, status=SuggestionStatus.PENDING)
        .values("key")
        .annotate(count=Count("id"))
        .values_list("key", "count")
    )
    needle = query.strip().casefold()
    result = []
    for row in rows:
        pivot_row = pivot_rows.get(row.key)
        pivot = pivot_overrides.get(row.key, pivot_row.translation if pivot_row else "")
        view = StringView(
            row=row, pivot=pivot, override=overrides.get(row.key), pending=pending.get(row.key, 0)
        )
        if status == "pending" and not view.pending:
            continue
        if status in ("untranslated", "machine", "reviewed") and view.status != status:
            continue
        if needle and not any(needle in text.casefold() for text in (row.source, view.current, pivot)):
            continue
        result.append(view)
    return result


def string_view(language: str, key: str) -> StringView:
    row = row_or_404(language, key)
    override = (
        TranslationOverride.objects.filter(language=language, key=key).values_list("text", flat=True).first()
    )
    pending = TranslationSuggestion.objects.filter(
        language=language, key=key, status=SuggestionStatus.PENDING
    ).count()
    return StringView(row=row, pivot=_pivot(language, key), override=override, pending=pending)


def suggestions_for(language: str, key: str):
    return (
        TranslationSuggestion.objects.filter(language=language, key=key)
        .annotate(
            vote_count=Count("votes"),
            # Oczekujące na górze – to one są pracą; rozstrzygnięte zostają niżej jako historia.
            decided=Case(When(status=SuggestionStatus.PENDING, then=0), default=1),
        )
        .order_by("decided", "-vote_count", "-created_at")
    )


def _clean(row: catalogs.Row, text: str) -> str:
    try:
        return clean_translation(
            text, msgid=row.msgid, msgid_plural=row.msgid_plural, plural_index=row.plural_index
        )
    except ValidationError as exc:
        raise TranslationInvalid(" ".join(exc.messages)) from exc


def _ref(row: catalogs.Row, language: str) -> dict:
    return {
        "language": language,
        "key": row.key,
        "msgctxt": row.msgctxt or "",
        "msgid": row.msgid,
        "plural_index": row.plural_index,
    }


@transaction.atomic
def suggest(*, user, language: str, key: str, text: str, approve_now: bool = False, request=None):
    """Nowa propozycja (albo poprawka własnej, jeszcze nierozstrzygniętej).

    Jedna oczekująca propozycja na osobę i napis: kolejna od tej samej osoby **zastępuje** treść
    poprzedniej, zamiast dokładać pozycję w kolejce recenzenta. Propozycja identyczna z cudzą,
    oczekującą – to głos na tamtą, a nie druga pozycja.
    """
    _require(can_translate(user, language))
    if approve_now:
        _require(can_review(user, language))
    row = row_or_404(language, key)
    cleaned = _clean(row, text)
    current = string_view(language, key).current
    if cleaned == current and not approve_now:
        raise TranslationInvalid(_("Ta propozycja jest identyczna z obecnym tłumaczeniem."))
    pending = TranslationSuggestion.objects.select_for_update().filter(
        language=language, key=key, status=SuggestionStatus.PENDING
    )
    same = pending.filter(text=cleaned).exclude(author=user).first()
    if same is not None and not approve_now:
        TranslationVote.objects.get_or_create(suggestion=same, user=user)
        return same
    suggestion = pending.filter(author=user).first()
    if suggestion is None:
        suggestion = TranslationSuggestion.objects.create(author=user, text=cleaned, **_ref(row, language))
    else:
        suggestion.text = cleaned
        suggestion.created_at = timezone.now()
        suggestion.save(update_fields=["text", "created_at"])
        suggestion.votes.all().delete()  # głosy oddano na poprzednią treść
    audit(user, "translation.suggested", suggestion, {"language": language, "key": key}, request=request)
    if approve_now:
        approve(user=user, suggestion=suggestion, request=request)
    return suggestion


@transaction.atomic
def vote(*, user, suggestion: TranslationSuggestion, request=None) -> bool:
    """Przełącza głos. Zwraca ``True``, gdy głos jest oddany po tej operacji."""
    _require(can_translate(user, suggestion.language))
    if suggestion.status != SuggestionStatus.PENDING:
        raise TranslationInvalid(_("Na rozstrzygniętą propozycję nie można już głosować."))
    if suggestion.author_id == user.pk:
        raise TranslationInvalid(_("Nie można głosować na własną propozycję."))
    deleted, _rows = TranslationVote.objects.filter(suggestion=suggestion, user=user).delete()
    if deleted:
        return False
    TranslationVote.objects.create(suggestion=suggestion, user=user)
    return True


def _decide(suggestion: TranslationSuggestion, user, status: str) -> None:
    suggestion.status = status
    suggestion.decided_by = user
    suggestion.decided_at = timezone.now()
    suggestion.save(update_fields=["status", "decided_by", "decided_at"])


@transaction.atomic
def approve(*, user, suggestion: TranslationSuggestion, request=None) -> TranslationOverride:
    """Zatwierdza propozycję: nakładka działa od razu na całej platformie (po ≤ kilku sekundach)."""
    _require(can_review(user, suggestion.language))
    suggestion = TranslationSuggestion.objects.select_for_update().get(pk=suggestion.pk)
    if suggestion.status != SuggestionStatus.PENDING:
        raise TranslationInvalid(_("Ta propozycja została już rozstrzygnięta."))
    # Walidacja jeszcze raz, wobec katalogu z **tej** chwili: między propozycją a decyzją mogło
    # wejść wydanie, które zmieniło napis źródłowy albo go usunęło.
    row = row_or_404(suggestion.language, suggestion.key)
    text = _clean(row, suggestion.text)
    override, _created = TranslationOverride.objects.update_or_create(
        language=suggestion.language,
        key=suggestion.key,
        defaults={
            "msgctxt": row.msgctxt or "",
            "msgid": row.msgid,
            "plural_index": row.plural_index,
            "text": text,
            "approved_by": user,
            "approved_at": timezone.now(),
            "suggestion": suggestion,
        },
    )
    _decide(suggestion, user, SuggestionStatus.APPROVED)
    TranslationSuggestion.objects.filter(
        language=suggestion.language, key=suggestion.key, status=SuggestionStatus.PENDING
    ).update(status=SuggestionStatus.SUPERSEDED, decided_by=user, decided_at=timezone.now())
    language = suggestion.language
    transaction.on_commit(lambda: runtime.publish(language))
    audit(
        user, "translation.approved", override, {"language": language, "key": suggestion.key}, request=request
    )
    return override


@transaction.atomic
def reject(*, user, suggestion: TranslationSuggestion, request=None) -> None:
    _require(can_review(user, suggestion.language))
    suggestion = TranslationSuggestion.objects.select_for_update().get(pk=suggestion.pk)
    if suggestion.status != SuggestionStatus.PENDING:
        raise TranslationInvalid(_("Ta propozycja została już rozstrzygnięta."))
    _decide(suggestion, user, SuggestionStatus.REJECTED)
    audit(
        user,
        "translation.rejected",
        suggestion,
        {"language": suggestion.language, "key": suggestion.key},
        request=request,
    )


@transaction.atomic
def confirm(*, user, language: str, key: str, request=None) -> TranslationOverride:
    """„Obecne tłumaczenie jest dobre” – zapisuje je jako przejrzane (eksport doda znacznik)."""
    _require(can_review(user, language))
    row = row_or_404(language, key)
    if not row.translation:
        raise TranslationInvalid(_("Ten napis nie ma jeszcze tłumaczenia – zaproponuj je."))
    if TranslationOverride.objects.filter(language=language, key=key).exists():
        raise TranslationInvalid(_("To tłumaczenie jest już przejrzane."))
    # Tekst z katalogu jest zaufany (przeszedł recenzję kodu), więc nie przechodzi walidacji
    # poprawek – nakładka jest jego dokładną kopią i niczego w interfejsie nie zmienia.
    override = TranslationOverride.objects.create(
        text=row.translation, approved_by=user, **_ref(row, language)
    )
    transaction.on_commit(lambda: runtime.publish(language))
    audit(user, "translation.confirmed", override, {"language": language, "key": key}, request=request)
    return override


@transaction.atomic
def revert(*, user, language: str, key: str, request=None) -> None:
    """Usuwa nakładkę – wraca tłumaczenie z katalogu."""
    _require(can_review(user, language))
    override = TranslationOverride.objects.filter(language=language, key=key).first()
    if override is None:
        raise Http404("Nie ma zatwierdzonego tłumaczenia tego napisu.")
    audit(user, "translation.reverted", override, {"language": language, "key": key}, request=request)
    override.delete()
    transaction.on_commit(lambda: runtime.publish(language))


# --- zgłoszenia ze stopki -----------------------------------------------------------------------


def clean_page(page: str) -> str:
    """Sama ścieżka strony: bez schematu, hosta, parametrów i fragmentu.

    Parametry bywają tokenami (link aktywacyjny, reset hasła), a ``//host`` w odnośniku na ekranie
    recenzenta prowadziłby poza serwis – dlatego ścieżka musi zaczynać się od jednego ukośnika.
    Odwrotny ukośnik odpada w całości: przeglądarki czytają ``/\\host`` jak ``//host``, a ta ścieżka
    jest też adresem powrotu po wysłaniu zgłoszenia.
    """
    path = urlsplit((page or "").strip()).path if page else ""
    if (
        not path.startswith("/")
        or path.startswith("//")
        or "\\" in path
        or any(ord(char) < 32 for char in path)
    ):
        return ""
    return path[:PAGE_MAX]


@transaction.atomic
def report(*, user, language: str, page: str, phrase: str, comment: str, request=None) -> TranslationReport:
    _require(can_translate(user, language))
    comment = (comment or "").strip()
    if not comment:
        raise TranslationInvalid(_("Opisz, co jest nie tak z tłumaczeniem."))
    if len(comment) > COMMENT_MAX or len((phrase or "").strip()) > PHRASE_MAX:
        raise TranslationInvalid(_("Zgłoszenie jest za długie."))
    created = TranslationReport.objects.create(
        reporter=user,
        language=language,
        page=clean_page(page),
        phrase=(phrase or "").strip(),
        comment=comment,
    )
    audit(user, "translation.reported", created, {"language": language}, request=request)
    return created


def reports_for(language: str, *, status: str = ReportStatus.OPEN):
    return TranslationReport.objects.filter(language=language, status=status).order_by("-created_at")


@transaction.atomic
def close_report(*, user, report_obj: TranslationReport, request=None) -> None:
    _require(can_review(user, report_obj.language))
    if report_obj.status == ReportStatus.CLOSED:
        return
    report_obj.status = ReportStatus.CLOSED
    report_obj.closed_by = user
    report_obj.closed_at = timezone.now()
    report_obj.save(update_fields=["status", "closed_by", "closed_at"])
    audit(user, "translation.report_closed", report_obj, {"language": report_obj.language}, request=request)


# --- RODO ---------------------------------------------------------------------------------------


def export_section(user) -> dict:
    """Dane tej osoby w przeglądzie tłumaczeń – do eksportu danych konta (art. 15/20 RODO)."""
    return {
        "uprawnienia": [
            {"jezyk": language, "poziom": level}
            for language, level in TranslatorGrant.objects.filter(user=user).values_list("language", "level")
        ],
        "propozycje": [
            {
                "jezyk": item.language,
                "napis": item.msgid,
                "tlumaczenie": item.text,
                "stan": item.status,
                "kiedy": item.created_at.isoformat(),
            }
            for item in TranslationSuggestion.objects.filter(author=user).order_by("created_at")
        ],
        "glosy": TranslationVote.objects.filter(user=user).count(),
        "zgloszenia": [
            {
                "jezyk": item.language,
                "strona": item.page,
                "napis": item.phrase,
                "uwaga": item.comment,
                "stan": item.status,
                "kiedy": item.created_at.isoformat(),
            }
            for item in TranslationReport.objects.filter(reporter=user).order_by("created_at")
        ],
    }


def erase_for_user(user) -> int:
    """Anonimizacja konta: uprawnienia, głosy i zgłoszenia znikają; propozycje zostają bez autora.

    Propozycja zatwierdzona jest już częścią interfejsu, a oczekująca – pracą dla recenzenta;
    żadna z nich nie mówi nic o osobie, gdy nie ma przy niej konta.
    """
    removed = TranslatorGrant.objects.filter(user=user).delete()[0]
    removed += TranslationVote.objects.filter(user=user).delete()[0]
    removed += TranslationReport.objects.filter(reporter=user).delete()[0]
    TranslationSuggestion.objects.filter(author=user).update(author=None)
    forget_user(user)
    return removed
