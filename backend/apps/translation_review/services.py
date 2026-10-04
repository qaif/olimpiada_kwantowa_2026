"""Reguły przeglądu tłumaczeń: kto co może, propozycje, głosy, zatwierdzanie i zgłoszenia.

Każda czynność sprawdza uprawnienie **tutaj**, a nie w szablonie – widok, który zapomni zapytać,
dostanie ``DomainError`` (403) z serwisu, a nie cichy zapis. Każda zmiana zostawia wpis audytu
bez treści zgłoszeń (tekst tłumaczenia nie jest daną osobową, ale komentarz w zgłoszeniu może nią
być – w audycie zostaje sam identyfikator).

Poziomy roli (L10N-01 § 2): tłumacz proponuje i głosuje, recenzent dodatkowo zatwierdza, odrzuca,
cofa nakładkę, potwierdza obecne tłumaczenie i zamyka zgłoszenia. Superkoordynator jest
recenzentem każdego języka. Koordynator konkursu nadaje wyłącznie poziom tłumacza, wyłącznie
osobom związanym ze swoim konkursem i wyłącznie w językach interfejsu tego konkursu, bo zatwierdzona
poprawka działa na **całej** platformie. Nadanie koordynatora należy do konkursu
(``TranslatorGrant.competition``) i działa tylko, dopóki osoba jest z konkursem związana.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Case, Count, Exists, OuterRef, Q, When
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
    OverrideKind,
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
        cached = {}
        for language, level in active_grants().filter(user=user).values_list("language", "level"):
            # Kilka nadań jednego języka (np. z dwóch konkursów) – liczy się wyższy poziom.
            if cached.get(language) != GrantLevel.REVIEWER:
                cached[language] = level
        setattr(user, GRANTS_ATTR, cached)
    return cached


def active_grants():
    """Nadania, które **działają**: platformowe oraz konkursowe osoby nadal związanej z konkursem.

    Jedno zapytanie (dwa ``EXISTS``) zamiast sygnałów przy usuwaniu członkostwa czy profilu: związek
    z konkursem zrywa się kilkoma drogami (odebranie roli, wypisanie z delegacji, usunięcie konta),
    a każda zapomniana droga zostawiałaby rolę tłumacza osobie, która już do konkursu nie należy.
    """
    member = Membership.objects.filter(user=OuterRef("user"), competition=OuterRef("competition"))
    participant = Participant.objects.filter(user=OuterRef("user"), competition=OuterRef("competition"))
    return TranslatorGrant.objects.filter(Q(competition__isnull=True) | Exists(member) | Exists(participant))


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


def grantable_languages(actor, competition) -> list[str]:
    """Języki, w których ta osoba może nadać rolę: superkoordynator – wszystkie, koordynator – języki
    interfejsu **swojego** konkursu (tłumacz hindi w konkursie bez hindi nie ma czego oglądać)."""
    if is_super_coordinator(actor):
        return catalogs.review_languages()
    allowed = set(competition.ui_languages) if competition is not None else set()
    return [code for code in catalogs.review_languages() if code in allowed]


def grants_visible_to(actor, competition):
    """Superkoordynator – wszystkie nadania; koordynator – nadania **swojego konkursu**.

    Także osób, które z konkursu już odeszły (takie nadanie nie działa – ``active_grants`` – ale
    koordynator ma je widzieć i móc usunąć), i także nadane przez innego koordynatora.
    """
    queryset = TranslatorGrant.objects.select_related("user", "granted_by").order_by(
        "language", "user__email"
    )
    if is_super_coordinator(actor):
        return queryset
    if competition is None:
        return queryset.none()
    return queryset.filter(competition=competition)


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
    if language not in grantable_languages(actor, competition):
        raise TranslationDenied(_("Ten język nie jest językiem interfejsu tego konkursu."))
    user = User.objects.filter(email__iexact=(email or "").strip(), is_active=True).first()
    # Jedna odpowiedź na „nie ma konta” i „konto spoza konkursu” – formularz nie może służyć do
    # sprawdzania, czy ktoś ma konto na platformie.
    if user is None or (not superuser and not is_linked(user, competition)):
        raise TranslationInvalid(_("Nie znaleziono aktywnego konta z tym adresem w tym konkursie."))
    # Superkoordynator nadaje platformowo (bez konkursu), koordynator – w swoim konkursie.
    scope = None if superuser else competition
    existing = (
        TranslatorGrant.objects.select_for_update()
        .filter(user=user, language=language, competition=scope)
        .first()
    )
    if existing is None:
        existing = TranslatorGrant.objects.create(
            user=user, language=language, level=level, granted_by=actor, competition=scope
        )
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
        # Koordynator odbiera wyłącznie nadania **swojego konkursu** – także te, których osoba już
        # nie używa, bo odeszła z konkursu – nigdy nadań platformowych.
        if competition is None or grant_obj.competition_id != competition.pk:
            raise TranslationDenied(_("Tę osobę może zmienić wyłącznie superkoordynator."))
    diff = {"user_id": grant_obj.user_id, "language": grant_obj.language, "level": grant_obj.level}
    audit(actor, "translation.revoke", grant_obj, diff, request=request)
    user = grant_obj.user
    grant_obj.delete()
    forget_user(user)


# --- napisy -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OverrideState:
    """To, co o napisie wie baza: rodzaj decyzji, jej tekst i ``msgstr`` katalogu w chwili decyzji."""

    kind: str
    text: str
    base_text: str


def override_applies(row: catalogs.Row, state: OverrideState | None) -> bool:
    """Czy nakładka ma dziś trafić do gettext – **jedna** reguła dla runtime'u i ekranów.

    Nie trafia: potwierdzenie („obecne jest dobre” – nic nie zmienia, a przykrywałoby przyszłe
    wydania), poprawka już obecna w katalogu (wdrożona, czeka na ``--prune``) i poprawka
    **nieaktualna** – katalog zmienił ``msgstr`` od chwili decyzji, więc recenzent oceniał inny tekst
    niż ten, który dziś jest w repozytorium. Wtedy wygrywa katalog, a ekran prosi o ponowny przegląd.
    """
    if state is None or state.kind != OverrideKind.CHANGE or state.text == row.translation:
        return False
    return state.base_text == row.translation


def override_stale(row: catalogs.Row, state: OverrideState | None) -> bool:
    if state is None or state.text == row.translation:
        return False
    return state.base_text != row.translation


@dataclass
class StringView:
    """Wiersz ekranu: napis z katalogu + stan z bazy."""

    row: catalogs.Row
    pivot: str
    state: OverrideState | None
    pending: int

    @property
    def override(self) -> str | None:
        """Tekst nakładki, która **działa** w serwisie (``None`` – serwis pokazuje katalog)."""
        return self.state.text if override_applies(self.row, self.state) else None

    @property
    def stale(self) -> bool:
        return override_stale(self.row, self.state)

    @property
    def current(self) -> str:
        return self.override if self.override is not None else self.row.translation

    @property
    def status(self) -> str:
        if (self.state is not None and not self.stale) or self.row.reviewed:
            return "reviewed"
        return "machine" if self.row.translation else "untranslated"


def _states(language: str, key: str | None = None) -> dict[str, OverrideState]:
    queryset = TranslationOverride.objects.filter(language=language)
    if key is not None:
        queryset = queryset.filter(key=key)
    return {
        row_key: OverrideState(kind, text, base)
        for row_key, kind, text, base in queryset.values_list("key", "kind", "text", "base_text")
    }


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
    state = _states("en", key).get(key)
    return state.text if override_applies(row, state) else row.translation


def strings(language: str, *, status: str = "", query: str = "") -> list[StringView]:
    """Napisy języka z filtrami. Trzy zapytania niezależnie od liczby napisów."""
    rows = catalogs.index(language).rows
    pivot_rows = (
        catalogs.index("en").by_key if language != "en" and "en" in catalogs.review_languages() else {}
    )
    states = _states(language)
    pivot_states = _states("en") if pivot_rows else {}
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
        pivot = ""
        if pivot_row is not None:
            pivot_state = pivot_states.get(row.key)
            pivot = pivot_state.text if override_applies(pivot_row, pivot_state) else pivot_row.translation
        view = StringView(row=row, pivot=pivot, state=states.get(row.key), pending=pending.get(row.key, 0))
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
    pending = TranslationSuggestion.objects.filter(
        language=language, key=key, status=SuggestionStatus.PENDING
    ).count()
    return StringView(
        row=row, pivot=_pivot(language, key), state=_states(language, key).get(key), pending=pending
    )


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
        _add_vote(same, user)
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
def vote(*, user, suggestion: TranslationSuggestion, support: bool | None = None, request=None) -> bool:
    """Ustawia głos (``support``) albo – bez niego – przełącza. Zwraca stan głosu po operacji.

    Formularz wysyła **stan docelowy**, a nie „przełącz”: podwójne kliknięcie „Popieram” to dwa razy
    „ma być głos”, a nie głos i jego cofnięcie. Wyścig dwóch równoległych żądań kończy ``_add_vote``.
    """
    _require(can_translate(user, suggestion.language))
    if suggestion.status != SuggestionStatus.PENDING:
        raise TranslationInvalid(_("Na rozstrzygniętą propozycję nie można już głosować."))
    if suggestion.author_id == user.pk:
        raise TranslationInvalid(_("Nie można głosować na własną propozycję."))
    if support is None:
        support = not TranslationVote.objects.filter(suggestion=suggestion, user=user).exists()
    if support:
        _add_vote(suggestion, user)
    else:
        TranslationVote.objects.filter(suggestion=suggestion, user=user).delete()
    return support


def _add_vote(suggestion: TranslationSuggestion, user) -> None:
    """Głos bez wyścigu: podwójne kliknięcie to dwa równoległe POST-y, a drugi trafia na więz
    unikalności. Punkt zapisu (``atomic``) zamyka błąd w sobie – głos i tak już jest."""
    try:
        with transaction.atomic():
            TranslationVote.objects.create(suggestion=suggestion, user=user)
    except IntegrityError:
        pass


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
            "kind": OverrideKind.CHANGE,
            "base_text": row.translation,
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
    existing = TranslationOverride.objects.filter(language=language, key=key).first()
    if existing is not None and not override_stale(
        row, OverrideState(existing.kind, existing.text, existing.base_text)
    ):
        raise TranslationInvalid(_("To tłumaczenie jest już przejrzane."))
    # Potwierdzenie to **znacznik**, a nie tekst: nie trafia do gettext nigdy (``override_applies``),
    # a eksport dopisuje przy nim sam komentarz ``# l10n-reviewed``. Tekst katalogu zapamiętujemy, żeby
    # było widać, co potwierdzono – i żeby zmiana w repozytorium unieważniła potwierdzenie.
    if existing is not None:
        existing.delete()  # nieaktualna decyzja (katalog się zmienił) – zastępuje ją ta
    override = TranslationOverride.objects.create(
        text=row.translation,
        base_text=row.translation,
        kind=OverrideKind.CONFIRMATION,
        approved_by=user,
        **_ref(row, language),
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
