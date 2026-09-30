"""Reguły Wiadomości: kto z kim może pisać, co kto widzi, co robi moderacja.

Widoki (``apps.web.views.chat``, ``apps.web.views.coordinator_chat``) nie powtarzają żadnej z tych
reguł – ta sama zasada, co w ``apps.forum.services``. Każda funkcja pisząca sprawdza rolę **sama**
(``DomainError`` 403), a nie ufa, że widok zrobił to przed nią: recenzent, członek komisji,
opiekun szkolny i supervisor nie piszą i nie odbierają wiadomości, i ta odmowa ma stać w kodzie
domeny, nie w szablonie, który chowa przycisk.

**Tryb rozmów między uczestnikami ma dwie wartości** – zapisaną (``ChatSettings.peer_mode``)
i obowiązującą (:func:`effective_peer_mode`). Różnica jest ta sama, co na forum, i z tego samego
powodu: dopóki trwa nietreningowy etap przyjmujący rozwiązania, ``POST`` i ``NONE`` zamieniają się
w ``PRE``. Rozmowa 1:1 bez świadków w czasie zawodów jest najprostszą drogą do zmowy, a wiadomość
przeczytana przez moderatora kwadrans po wysłaniu jest już przeczytana także przez adresata.
``OFF`` zostaje ``OFF`` – wymuszenie ma zaostrzać, nie otwierać.

**Czego koordynator nie widzi.** Treść rozmowy między uczestnikami trafia do koordynatora wyłącznie
przez kolejkę moderacji: wiadomości czekające (``PRE``), nieprzejrzane (``POST``) i zgłoszone przez
odbiorcę. Nie ma widoku „przeglądaj wszystkie rozmowy” i nie ma go dopisywać: w trybie ``NONE``
uczestnik przeczytał nad formularzem, że organizator widzi **tylko zgłoszone** wiadomości, i ta
obietnica ma być prawdą także w kodzie (:func:`moderator_visible_q`).

**Rozmowy szyfrowane** (§ 11) przyjmują wiadomości tylko wtedy, gdy szyfrowanie jest włączone
**i** obowiązujący tryb to ``NONE`` (:func:`e2e_writable`). W czasie etapu wymuszającego
premoderację są więc tylko do odczytu: organizator nie przeczyta szyfrogramu, więc nie może go
zaakceptować. Serwer nie zna treści, ale pilnuje kształtu – jawny tekst w rozmowie szyfrowanej
i szyfrogram w jawnej są odrzucane, a szyfrogram musi być zrobiony **bieżącymi** kluczami obu stron.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging

from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, Max, OuterRef, Q, Subquery
from django.utils import timezone
from rest_framework import status as http

from apps.core.api import DomainError
from apps.core.models import audit

from . import notifications
from .models import (
    MAX_BODY_LENGTH,
    MAX_CIPHERTEXT_LENGTH,
    MAX_KDF_ITERATIONS,
    MAX_REASON_LENGTH,
    MIN_KDF_ITERATIONS,
    MODERATED_MODES,
    ChatBlock,
    ChatKey,
    ChatNotificationSettings,
    ChatProfile,
    ChatSettings,
    Conversation,
    ConversationKind,
    ConversationMember,
    Message,
    MessageReport,
    MessageStatus,
    PeerMode,
    SenderRole,
)

logger = logging.getLogger(__name__)

#: Akcje audytu – napisy w jednym miejscu, bo po nich filtruje ekran dziennika zdarzeń.
AUDIT_MESSAGE_APPROVED = "chat.message_approved"
AUDIT_MESSAGE_REJECTED = "chat.message_rejected"
AUDIT_MESSAGE_HIDDEN = "chat.message_hidden"
AUDIT_MESSAGE_REVIEWED = "chat.message_reviewed"
AUDIT_REPORT_RESOLVED = "chat.report_resolved"
AUDIT_SETTINGS_CHANGED = "chat.settings_changed"

#: Odmowa, która **nie zdradza powodu**. Zablokowany uczestnik, konto usunięte i osoba, która
#: wypisała się z katalogu, dostają to samo zdanie: „zablokował cię” byłoby informacją, której
#: blokujący nie chciał przekazywać, a różne zdania dla różnych powodów pozwalałyby ją wywnioskować.
CANNOT_SEND = "Nie można wysłać wiadomości do tej osoby."

PEER_OFF = "Organizator wyłączył rozmowy między uczestnikami – ta rozmowa jest teraz tylko do odczytu."
BLOCKED_BY_ME = "Zablokowałeś tę osobę. Odblokuj ją, jeśli chcesz napisać."
NEEDS_KEY = "Ta rozmowa jest szyfrowana – skonfiguruj szyfrowanie, żeby w niej pisać."

#: Stany wiadomości, które odbiorca **w ogóle** widzi w wątku. Ukryta zostaje na swoim miejscu
#: z informacją, że zdjął ją moderator – wycięcie jej bez śladu robiłoby z odpowiedzi drugiej
#: strony odpowiedź na nic.
DELIVERED_STATUSES = (MessageStatus.PUBLISHED, MessageStatus.HIDDEN)


# --- ustawienia i tryb -----------------------------------------------------------------------------


def settings_for(competition) -> ChatSettings:
    """Ustawienia tego konkursu. Brak wiersza znaczy „domyślne” – obiekt bywa niezapisany.

    Ta sama umowa, co ``apps.forum.services.settings_for``: odczyt nie zakłada wiersza w środku
    ``GET``-a.
    """
    row = ChatSettings.objects.for_competition(competition).first() if competition is not None else None
    return row if row is not None else ChatSettings(competition=competition)


def is_enabled(competition) -> bool:
    return competition is not None and settings_for(competition).enabled


def forcing_stage(competition, now=None):
    """Etap, który **teraz** wymusza premoderację – reguła forum, importowana, nie przepisana."""
    from apps.forum.services import stage_forcing_pre_moderation

    return stage_forcing_pre_moderation(competition, now)


def mode_and_stage(competition, now=None, *, row: ChatSettings | None = None) -> tuple[PeerMode, object]:
    """Tryb obowiązujący **teraz** razem z etapem, który go wymusił (albo ``None``).

    Najpierw ustawienie, potem etap – odwrotnie niż na forum, bo tu jest tryb, którego wymuszenie
    nie dotyczy (``OFF``, ``PRE``), a pytanie o etapy kosztuje zapytania, których on nie potrzebuje.
    Etap wraca razem z trybem, bo ekran i odmowa zapisu mówią, **który** etap wstrzymał rozmowę.
    """
    mode = PeerMode((row or settings_for(competition)).peer_mode)
    if mode in (PeerMode.POST, PeerMode.NONE):
        stage = forcing_stage(competition, now)
        if stage is not None:
            return PeerMode.PRE, stage
    return mode, None


def effective_peer_mode(competition, now=None, *, row: ChatSettings | None = None) -> PeerMode:
    """Tryb rozmów między uczestnikami obowiązujący **teraz** (uzasadnienie w docstringu modułu)."""
    return mode_and_stage(competition, now, row=row)[0]


def e2e_writable(row: ChatSettings, mode: PeerMode) -> bool:
    """Czy rozmowy szyfrowane przyjmują teraz wiadomości: szyfrowanie włączone **i** tryb ``NONE``.

    Obowiązujący tryb, nie zapisany: w czasie etapu wymuszającego premoderację rozmowa szyfrowana
    staje się tylko do odczytu – organizator nie może przeczytać szyfrogramu, więc nie może go też
    zaakceptować, a wpuszczenie go bez akceptacji byłoby dziurą dokładnie w tej regule (§ 11.1).
    """
    return bool(row.e2e_enabled) and mode == PeerMode.NONE


@transaction.atomic
def save_settings(
    *, competition, actor, enabled: bool, peer_mode: str, e2e_enabled: bool = False, request=None
) -> ChatSettings:
    """Zapisuje ustawienia modułu. Wiersz powstaje przy pierwszym zapisie; zmiana idzie do audytu.

    Szyfrowanie end-to-end wymaga trybu ``NONE`` i ta reguła stoi **tutaj**, nie w formularzu:
    rozmowa, której treści serwer nie zna, w trybie z moderacją byłaby obietnicą moderacji bez
    możliwości jej wykonania.
    """
    if peer_mode not in PeerMode.values:
        raise DomainError("Nieznany tryb rozmów.", "CHAT_MODE_UNKNOWN", http.HTTP_400_BAD_REQUEST)
    row, _ = ChatSettings.objects.get_or_create(competition=competition)
    if e2e_enabled and peer_mode != PeerMode.NONE:
        if row.e2e_enabled:
            raise DomainError(
                "Najpierw wyłącz szyfrowanie – rozmów, których organizator nie może przeczytać, nie da "
                "się moderować.",
                "CHAT_E2E_BLOCKS_MODE",
                http.HTTP_400_BAD_REQUEST,
            )
        raise DomainError(
            "Szyfrowanie end-to-end można włączyć tylko w trybie „bez moderacji”.",
            "CHAT_E2E_NEEDS_NONE",
            http.HTTP_400_BAD_REQUEST,
        )
    before = {"enabled": row.enabled, "peer_mode": row.peer_mode, "e2e_enabled": row.e2e_enabled}
    row.enabled = bool(enabled)
    row.peer_mode = peer_mode
    row.e2e_enabled = bool(e2e_enabled)
    row.updated_at = timezone.now()
    row.save(update_fields=["enabled", "peer_mode", "e2e_enabled", "updated_at"])
    audit(
        actor,
        AUDIT_SETTINGS_CHANGED,
        row,
        {
            "before": before,
            "enabled": row.enabled,
            "peer_mode": row.peer_mode,
            "e2e_enabled": row.e2e_enabled,
        },
        request=request,
    )
    return row


# --- dostęp ----------------------------------------------------------------------------------------


def _forbidden() -> DomainError:
    return DomainError(
        "Wiadomości są dostępne dla uczestników i organizatora tego konkursu.",
        "CHAT_FORBIDDEN",
        http.HTTP_403_FORBIDDEN,
    )


def _not_found(what: str = "Nie ma takiej rozmowy.") -> DomainError:
    return DomainError(what, "CHAT_NOT_FOUND", http.HTTP_404_NOT_FOUND)


def chat_participant(user, competition):
    """Profil uczestnika tej osoby w tym konkursie – o ile ma rolę uczestnika. Inaczej ``None``.

    Dwa warunki naraz, jak w ``ParticipantRequiredMixin``: sam profil bez roli (rola odebrana,
    konto przeniesione do komitetu) nie wystarcza, żeby pisać.
    """
    from apps.accounts.models import CompetitionRole
    from apps.accounts.services import has_role, participant_for

    if competition is None or not has_role(user, competition, CompetitionRole.PARTICIPANT):
        return None
    return participant_for(user, competition)


def is_organizer(user, competition) -> bool:
    from apps.accounts.models import CompetitionRole
    from apps.accounts.services import has_role

    return competition is not None and has_role(user, competition, CompetitionRole.COORDINATOR)


def ensure_participant(user, competition):
    participant = chat_participant(user, competition)
    if participant is None:
        raise _forbidden()
    return participant


def ensure_organizer(user, competition) -> None:
    if not is_organizer(user, competition):
        raise _forbidden()


def ensure_enabled(competition) -> None:
    """Wyłączony moduł to „tu nic nie stoi” (404), a nie odmowa – tak samo jak wyłączone forum."""
    if not is_enabled(competition):
        raise DomainError(
            "Wiadomości są w tym konkursie wyłączone.", "CHAT_DISABLED", http.HTTP_404_NOT_FOUND
        )


# --- katalog i blokady --------------------------------------------------------------------------------


def profile_for(participant) -> ChatProfile:
    """Profil katalogu uczestnika albo niezapisany profil domyślny („poza katalogiem”)."""
    row = ChatProfile.objects.filter(participant=participant).first()
    return row if row is not None else ChatProfile(participant=participant)


def set_discoverable(participant, value: bool) -> ChatProfile:
    row, _ = ChatProfile.objects.get_or_create(participant=participant)
    row.discoverable = bool(value)
    row.updated_at = timezone.now()
    row.save(update_fields=["discoverable", "updated_at"])
    return row


def has_blocked(blocker, blocked) -> bool:
    if blocker is None or blocked is None:
        return False
    return ChatBlock.objects.filter(blocker=blocker, blocked=blocked).exists()


def directory(participant, query: str = ""):
    """Katalog: uczestnicy **tego** konkursu, którzy sami się do niego zapisali.

    Bez siebie, bez kont nieaktywnych i zanonimizowanych, bez osób, które straciły rolę uczestnika,
    i bez tych, którzy **mnie** zablokowali – blokujący nie ma się pojawiać w wynikach osoby, którą
    zablokował, bo pusty wynik po kliknięciu „Napisz” byłby tym samym zdradzeniem blokady.

    Szukamy **wyłącznie po imieniu**. Na ekranie stoi imię i inicjał nazwiska, więc wyszukiwanie po
    nazwisku pozwalałoby sprawdzać nazwiska, których katalog nie pokazuje.
    """
    from apps.accounts.anonymised import anonymised_q
    from apps.accounts.messaging import _role_filter
    from apps.accounts.models import CompetitionRole, User

    competition = participant.competition
    blocked_me = ChatBlock.objects.filter(blocked=participant).values("blocker_id")
    with_role = User.objects.filter(_role_filter(competition, CompetitionRole.PARTICIPANT)).values("pk")
    rows = (
        ChatProfile.objects.for_competition(competition)
        .filter(discoverable=True, participant__user__is_active=True, participant__user_id__in=with_role)
        .exclude(participant=participant)
        .exclude(participant_id__in=blocked_me)
        .exclude(anonymised_q("participant__user"))
        .select_related("participant__user")
    )
    if settings_for(competition).e2e_enabled:
        # Przy szyfrowaniu nowa rozmowa jest zawsze szyfrowana, a do tego obie strony muszą mieć
        # klucz. Osoba bez klucza w katalogu byłaby przyciskiem „Napisz”, który nie może zadziałać.
        rows = rows.filter(participant__chat_key__isnull=False)
    text = (query or "").strip()
    if text:
        rows = rows.filter(participant__user__first_name__icontains=text)
    return rows.order_by("participant__user__first_name", "participant__user__last_name", "pk")


# --- klucze szyfrowania (§ 11) ----------------------------------------------------------------------------


def key_for(participant) -> ChatKey | None:
    if participant is None:
        return None
    return ChatKey.objects.filter(participant=participant).first()


def _b64(value: str, *, label: str, min_bytes: int, max_bytes: int, exact: int | None = None) -> bytes:
    """Pole base64 od przeglądarki: poprawne kodowanie i rozmiar w granicach. Inaczej ``DomainError``."""
    text = (value or "").strip()
    try:
        raw = base64.b64decode(text, validate=True)
    except binascii.Error, ValueError:
        raw = None
    if not text or raw is None:
        raise DomainError(f"{label}: niepoprawne dane.", "CHAT_E2E_BAD_FIELD", http.HTTP_400_BAD_REQUEST)
    if (exact is not None and len(raw) != exact) or not (min_bytes <= len(raw) <= max_bytes):
        raise DomainError(f"{label}: niepoprawny rozmiar.", "CHAT_E2E_BAD_SIZE", http.HTTP_400_BAD_REQUEST)
    return raw


def fingerprint_of(public_key_b64: str) -> str:
    """Odcisk klucza publicznego: SHA-256 z SPKI, szesnastkowo. Liczony zawsze po stronie serwera."""
    return hashlib.sha256(base64.b64decode(public_key_b64)).hexdigest()


def _validate_p256_spki(raw: bytes) -> None:
    """Czy bajty są kluczem publicznym SPKI na krzywej P-256 – ``cryptography`` jest już w zależnościach."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import load_der_public_key

    try:
        key = load_der_public_key(raw)
    except ValueError, TypeError:
        key = None
    if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
        raise DomainError(
            "Klucz publiczny nie jest kluczem P-256.", "CHAT_E2E_BAD_KEY", http.HTTP_400_BAD_REQUEST
        )


@transaction.atomic
def save_key(
    *,
    user,
    competition,
    public_key: str,
    wrapped_private_key: str,
    kdf_salt: str,
    wrap_iv: str,
    kdf_iterations,
    replace: bool = False,
    request=None,
) -> ChatKey:
    """Zapisuje klucz tożsamości uczestnika (pierwszy albo **nowy** – „zapomniałem hasła”).

    Serwer sprawdza kształt, a nie treść: klucza prywatnego nie zobaczy nigdy, więc może tylko
    upewnić się, że owinięta kopia ma rozmiar owiniętego klucza P-256, sól i IV mają swoje długości,
    a PBKDF2 ma co najmniej :data:`~apps.chat.models.MIN_KDF_ITERATIONS` iteracji. Nowy klucz
    **zastępuje** stary: wiadomości zaszyfrowane starym przestają być czytelne dla tej osoby, a druga
    strona zobaczy w wątku notkę o zmianie klucza.
    """
    participant = ensure_participant(user, competition)
    ensure_enabled(competition)
    existing = key_for(participant)
    if existing is None and not settings_for(competition).e2e_enabled:
        raise DomainError(
            "Organizator nie włączył szyfrowanych rozmów.", "CHAT_E2E_DISABLED", http.HTTP_400_BAD_REQUEST
        )
    if existing is not None and not replace:
        raise DomainError(
            "Masz już klucz szyfrowania. Nowy utworzysz przyciskiem „Utwórz nowy klucz”.",
            "CHAT_E2E_KEY_EXISTS",
            http.HTTP_400_BAD_REQUEST,
        )
    spki = _b64(public_key, label="Klucz publiczny", min_bytes=60, max_bytes=120)
    _validate_p256_spki(spki)
    _b64(wrapped_private_key, label="Kopia klucza prywatnego", min_bytes=100, max_bytes=512)
    _b64(kdf_salt, label="Sól", min_bytes=16, max_bytes=48)
    _b64(wrap_iv, label="IV", min_bytes=12, max_bytes=12, exact=12)
    try:
        iterations = int(kdf_iterations)
    except TypeError, ValueError:
        iterations = 0
    if not MIN_KDF_ITERATIONS <= iterations <= MAX_KDF_ITERATIONS:
        raise DomainError(
            f"Za mało iteracji PBKDF2 (minimum {MIN_KDF_ITERATIONS}).",
            "CHAT_E2E_WEAK_KDF",
            http.HTTP_400_BAD_REQUEST,
        )
    values = {
        "public_key": public_key.strip(),
        "wrapped_private_key": wrapped_private_key.strip(),
        "kdf_salt": kdf_salt.strip(),
        "wrap_iv": wrap_iv.strip(),
        "kdf_iterations": iterations,
        "fingerprint": hashlib.sha256(spki).hexdigest(),
        "created_at": timezone.now(),
    }
    row, _ = ChatKey.objects.update_or_create(participant=participant, defaults=values)
    return row


@transaction.atomic
def block(*, participant, conversation: Conversation) -> ChatBlock:
    other = other_participant(conversation, participant)
    if other is None:
        raise DomainError(CANNOT_SEND, "CHAT_NO_OTHER_SIDE", http.HTTP_400_BAD_REQUEST)
    row, _ = ChatBlock.objects.get_or_create(blocker=participant, blocked=other)
    return row


@transaction.atomic
def unblock(*, participant, conversation: Conversation) -> None:
    other = other_participant(conversation, participant)
    if other is not None:
        ChatBlock.objects.filter(blocker=participant, blocked=other).delete()


# --- rozmowy -------------------------------------------------------------------------------------------


def _pair(a, b) -> tuple:
    return (a, b) if a.pk < b.pk else (b, a)


def other_participant(conversation: Conversation, participant):
    """Druga strona rozmowy między uczestnikami albo ``None`` (organizator, konto usunięte)."""
    if not conversation.is_peer:
        return None
    if conversation.participant_low_id == participant.pk:
        return conversation.participant_high
    if conversation.participant_high_id == participant.pk:
        return conversation.participant_low
    return None


def find_peer_conversation(a, b) -> Conversation | None:
    low, high = _pair(a, b)
    return Conversation.objects.filter(
        kind=ConversationKind.PEER, participant_low=low, participant_high=high
    ).first()


def organizer_conversation_of(participant) -> Conversation | None:
    return Conversation.objects.filter(kind=ConversationKind.ORGANIZER, participant=participant).first()


def _organizer_conversation(participant) -> Conversation:
    """Rozmowa uczestnika z organizatorem – istniejąca albo nowa. Wyścig rozstrzyga więz w bazie."""
    existing = organizer_conversation_of(participant)
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            conversation = Conversation.objects.create(
                competition=participant.competition, kind=ConversationKind.ORGANIZER, participant=participant
            )
            ConversationMember.objects.create(conversation=conversation, participant=participant)
            return conversation
    except IntegrityError:
        return organizer_conversation_of(participant)


def _peer_conversation(a, b, *, encrypted: bool = False) -> Conversation:
    """Rozmowa pary uczestników – istniejąca albo nowa. ``encrypted`` liczy się **wyłącznie** przy
    zakładaniu: istniejąca rozmowa zostaje taka, jaka była (``Conversation.is_encrypted``)."""
    existing = find_peer_conversation(a, b)
    if existing is not None:
        return existing
    low, high = _pair(a, b)
    try:
        with transaction.atomic():
            conversation = Conversation.objects.create(
                competition=a.competition,
                kind=ConversationKind.PEER,
                participant_low=low,
                participant_high=high,
                is_encrypted=encrypted,
            )
            ConversationMember.objects.bulk_create(
                [
                    ConversationMember(conversation=conversation, participant=low),
                    ConversationMember(conversation=conversation, participant=high),
                ]
            )
            return conversation
    except IntegrityError:
        return find_peer_conversation(a, b)


def visible_to_participant_q(participant) -> Q:
    """Wiadomości, które ta osoba widzi w rozmowie: doręczone i wszystkie **własne**.

    Własna czekająca ma etykietę „czeka na akceptację”, własna odrzucona – notatkę moderatora.
    Cudzej czekającej i cudzej odrzuconej odbiorca nie widzi wcale.
    """
    return Q(status__in=DELIVERED_STATUSES) | Q(sender_id=participant.user_id)


def visible_messages(conversation: Conversation, participant):
    return (
        Message.objects.filter(conversation=conversation)
        .filter(visible_to_participant_q(participant))
        .order_by("created_at", "id")
    )


def thread_version(conversation: Conversation, participant=None) -> str:
    """Krótki znacznik „stanu” wątku dla odpytywania co 15 s: liczba, ostatnia i ostatnio moderowana.

    Odpytanie z niezmienionym znacznikiem dostaje ``204`` i htmx niczego nie podmienia. Bez tego
    odświeżenie co kwadrans minuty kasowałoby otwarty formularz „Zgłoś” razem z tym, co ktoś w nim
    właśnie pisze. ``participant=None`` – widok organizatora (wszystkie wiadomości rozmowy).
    """
    rows = Message.objects.filter(conversation=conversation)
    if participant is not None:
        rows = rows.filter(visible_to_participant_q(participant))
    state = rows.aggregate(count=Count("pk"), last=Max("pk"), moderated=Max("moderated_at"))
    moderated = int(state["moderated"].timestamp()) if state["moderated"] else 0
    return f"{state['count']}-{state['last'] or 0}-{moderated}"


def participant_inbox(participant):
    """Skrzynka uczestnika: jego strony rozmów razem z ostatnią widoczną dla niego wiadomością.

    Rozmowa bez ani jednej widocznej wiadomości **nie istnieje** na tej liście: rozmowę zaczętą od
    wiadomości czekającej na premoderację widzi nadawca, a odbiorca – dopiero po akceptacji. Bez tego
    warunku sama pozycja „Imię N.” zdradzałaby, że ktoś coś napisał i czeka.

    Przy wiadomości szyfrowanej skrót jest szyfrogramem – odszyfruje go przeglądarka (§ 11), więc
    obok treści jadą IV i klucze obu stron z chwili wysłania.
    """
    last = (
        Message.objects.filter(conversation=OuterRef("conversation"))
        .filter(visible_to_participant_q(participant))
        .order_by("-created_at", "-id")
    )
    return (
        ConversationMember.objects.filter(
            participant=participant, conversation__competition=participant.competition
        )
        .annotate(
            last_body=Subquery(last.values("body")[:1]),
            last_status=Subquery(last.values("status")[:1]),
            last_sender_id=Subquery(last.values("sender_id")[:1]),
            last_created_at=Subquery(last.values("created_at")[:1]),
            last_ciphertext=Subquery(last.values("ciphertext")[:1]),
            last_iv=Subquery(last.values("iv")[:1]),
            last_sender_key=Subquery(last.values("sender_public_key")[:1]),
            last_recipient_key=Subquery(last.values("recipient_public_key")[:1]),
        )
        .filter(last_status__isnull=False)
        .select_related(
            "conversation",
            "conversation__participant_low__user",
            "conversation__participant_high__user",
        )
        .order_by("-conversation__last_message_at", "-conversation_id")
    )


def membership(participant, pk) -> ConversationMember:
    """Strona tej osoby w rozmowie ``pk`` albo 404 – to jest **jedyna** bramka wątku uczestnika.

    404 także wtedy, gdy rozmowa ma wiadomości, ale żadnej z nich ta osoba nie może zobaczyć (patrz
    :func:`participant_inbox`). Rozmowa jeszcze **pusta** (szyfrowaną zakłada się przed pierwszą
    wiadomością, bo klucz rozmowy wyprowadza się z jej identyfikatora) jest dostępna obu stronom –
    nic w niej nie czeka, więc nie ma czego zdradzić. Rozmowa innego konkursu nie ma tu członkostwa
    tej osoby (profil jest per konkurs), więc odpada tym samym warunkiem.
    """
    member = (
        ConversationMember.objects.filter(
            participant=participant, conversation_id=pk, conversation__competition=participant.competition
        )
        .select_related(
            "conversation",
            "conversation__participant_low__user",
            "conversation__participant_high__user",
        )
        .first()
    )
    if member is None:
        raise _not_found()
    conversation = member.conversation
    if (
        not visible_messages(conversation, participant).exists()
        and Message.objects.filter(conversation=conversation).exists()
    ):
        raise _not_found()
    return member


def encrypted_refusal(*, row: ChatSettings, mode: PeerMode, stage=None) -> str:
    """Dlaczego rozmowa szyfrowana jest teraz tylko do odczytu – albo pusty napis (§ 11.1)."""
    if e2e_writable(row, mode):
        return ""
    if mode == PeerMode.OFF:
        return PEER_OFF
    if stage is not None:
        return (
            f"Trwa etap „{stage.display_name}” – rozmowy szyfrowane są wstrzymane, bo organizator nie "
            "może ich moderować. Wrócą po zamknięciu etapu."
        )
    return "Organizator wyłączył szyfrowane rozmowy – ta rozmowa jest teraz tylko do odczytu."


def peer_write_refusal(
    conversation: Conversation, participant, *, mode: PeerMode, row: ChatSettings | None = None, stage=None
) -> str:
    """Dlaczego ta osoba **nie może** teraz pisać w tej rozmowie – albo pusty napis.

    Jedna funkcja dla serwisu (odmowa zapisu) i dla ekranu (zdanie zamiast formularza), żeby
    szablon nie wyprowadzał tej reguły od nowa.
    """
    if not conversation.is_peer:
        return ""
    if mode == PeerMode.OFF:
        return PEER_OFF
    if conversation.is_encrypted:
        refusal = encrypted_refusal(row=row or settings_for(conversation.competition), mode=mode, stage=stage)
        if refusal:
            return refusal
    other = other_participant(conversation, participant)
    if other is None or not other.user.is_active:
        return CANNOT_SEND
    if has_blocked(participant, other):
        return BLOCKED_BY_ME
    if has_blocked(other, participant):
        return CANNOT_SEND
    if conversation.is_encrypted:
        if key_for(participant) is None:
            return NEEDS_KEY
        if key_for(other) is None:
            return CANNOT_SEND
    return ""


def _clean_body(body: str) -> str:
    text = (body or "").strip()
    if not text:
        raise DomainError("Wiadomość nie może być pusta.", "CHAT_BODY_REQUIRED", http.HTTP_400_BAD_REQUEST)
    if len(text) > MAX_BODY_LENGTH:
        raise DomainError(
            f"Wiadomość jest za długa (limit {MAX_BODY_LENGTH} znaków).",
            "CHAT_BODY_TOO_LONG",
            http.HTTP_400_BAD_REQUEST,
        )
    return text


def _clean_reason(value: str, *, label: str) -> str:
    text = (value or "").strip()
    if not text:
        raise DomainError(f"{label} nie może być puste.", "CHAT_REASON_REQUIRED", http.HTTP_400_BAD_REQUEST)
    if len(text) > MAX_REASON_LENGTH:
        raise DomainError(
            f"{label} jest za długie (limit {MAX_REASON_LENGTH} znaków).",
            "CHAT_REASON_TOO_LONG",
            http.HTTP_400_BAD_REQUEST,
        )
    return text


def _mark_sender_side_read(conversation: Conversation, *, participant=None, now) -> None:
    """Kto pisze, ten przeczytał: odpowiedź w wątku gasi nadawcy jego własną kropkę."""
    if participant is not None:
        ConversationMember.objects.filter(conversation=conversation, participant=participant).update(
            unread_since=None, last_read_at=now
        )
    else:
        Conversation.objects.filter(pk=conversation.pk).update(
            organizer_unread_since=None, organizer_last_read_at=now
        )
        conversation.organizer_unread_since = None
        conversation.organizer_last_read_at = now


def _deliver(message: Message, now, request=None) -> None:
    """Doręczenie: rozmowa w górę listy, kropka u odbiorcy, list „masz wiadomość”.

    Woła się to **wyłącznie** dla wiadomości ``PUBLISHED`` – w chwili wysłania albo akceptacji.
    """
    conversation = message.conversation
    Conversation.objects.filter(pk=conversation.pk).update(last_message_at=now)
    conversation.last_message_at = now
    if conversation.is_organizer and message.sender_role == SenderRole.PARTICIPANT:
        Conversation.objects.filter(pk=conversation.pk, organizer_unread_since__isnull=True).update(
            organizer_unread_since=now
        )
    else:
        recipients = ConversationMember.objects.filter(conversation=conversation, unread_since__isnull=True)
        if message.sender_role == SenderRole.PARTICIPANT and message.sender_id is not None:
            recipients = recipients.exclude(participant__user_id=message.sender_id)
        recipients.update(unread_since=now)
    notifications.message_delivered(message, now)


def _encrypted_payload(
    conversation: Conversation,
    participant,
    *,
    body,
    ciphertext,
    iv,
    sender_fingerprint,
    recipient_fingerprint,
) -> dict:
    """Pola wiadomości szyfrowanej – po sprawdzeniu kształtu i aktualności kluczy obu stron.

    Serwer nie widzi treści, więc sprawdza to, co może: że **nie** przyszła treść jawna (formularz
    rozmowy szyfrowanej nie ma pola ``body`` z nazwą – jawny tekst tutaj znaczy klienta, który
    ominął szyfrowanie), że szyfrogram i IV mają swoje rozmiary i że przeglądarka szyfrowała
    **bieżącymi** kluczami obu stron. Nieaktualny odcisk to strona otwarta przed zmianą klucza –
    wiadomość zaszyfrowana starym kluczem byłaby dla drugiej strony nieczytelna od chwili wysłania.
    """
    if (body or "").strip():
        raise DomainError(
            "Ta rozmowa jest szyfrowana – serwer nie przyjmuje jawnej treści.",
            "CHAT_PLAINTEXT_REFUSED",
            http.HTTP_400_BAD_REQUEST,
        )
    if len((ciphertext or "").strip()) > MAX_CIPHERTEXT_LENGTH:
        raise DomainError(
            f"Wiadomość jest za długa (limit {MAX_BODY_LENGTH} znaków).",
            "CHAT_BODY_TOO_LONG",
            http.HTTP_400_BAD_REQUEST,
        )
    _b64(ciphertext, label="Szyfrogram", min_bytes=17, max_bytes=MAX_CIPHERTEXT_LENGTH)
    _b64(iv, label="IV", min_bytes=12, max_bytes=12, exact=12)
    mine = key_for(participant)
    theirs = key_for(other_participant(conversation, participant))
    if mine is None or theirs is None:
        raise DomainError(CANNOT_SEND, "CHAT_E2E_NO_KEY", http.HTTP_400_BAD_REQUEST)
    if sender_fingerprint != mine.fingerprint or recipient_fingerprint != theirs.fingerprint:
        raise DomainError(
            "Klucz szyfrowania zmienił się, odkąd otworzyłeś tę stronę. Odśwież ją i napisz jeszcze raz.",
            "CHAT_E2E_KEY_STALE",
            http.HTTP_400_BAD_REQUEST,
        )
    return {
        "body": "",
        "ciphertext": ciphertext.strip(),
        "iv": iv.strip(),
        "sender_public_key": mine.public_key,
        "recipient_public_key": theirs.public_key,
    }


@transaction.atomic
def send_participant_message(
    *,
    user,
    competition,
    conversation: Conversation,
    body: str = "",
    ciphertext: str = "",
    iv: str = "",
    sender_fingerprint: str = "",
    recipient_fingerprint: str = "",
    request=None,
) -> Message:
    """Wiadomość uczestnika w istniejącej rozmowie (obu rodzajów, jawnej albo szyfrowanej)."""
    participant = ensure_participant(user, competition)
    ensure_enabled(competition)
    member = ConversationMember.objects.filter(conversation=conversation, participant=participant).first()
    if member is None or conversation.competition_id != competition.pk:
        raise _not_found()
    now = timezone.now()
    mode = ""
    status = MessageStatus.PUBLISHED
    if conversation.is_peer:
        row = settings_for(competition)
        mode, stage = mode_and_stage(competition, now, row=row)
        refusal = peer_write_refusal(conversation, participant, mode=mode, row=row, stage=stage)
        if refusal:
            raise DomainError(refusal, "CHAT_CANNOT_WRITE", http.HTTP_400_BAD_REQUEST)
        status = MessageStatus.PENDING if mode == PeerMode.PRE else MessageStatus.PUBLISHED
    if conversation.is_encrypted:
        content = _encrypted_payload(
            conversation,
            participant,
            body=body,
            ciphertext=ciphertext,
            iv=iv,
            sender_fingerprint=sender_fingerprint,
            recipient_fingerprint=recipient_fingerprint,
        )
    else:
        if (ciphertext or "").strip() or (iv or "").strip():
            raise DomainError(
                "Ta rozmowa nie jest szyfrowana – serwer nie przyjmuje szyfrogramu.",
                "CHAT_CIPHERTEXT_REFUSED",
                http.HTTP_400_BAD_REQUEST,
            )
        content = {"body": _clean_body(body)}
    message = Message.objects.create(
        conversation=conversation,
        sender=user,
        sender_role=SenderRole.PARTICIPANT,
        status=status,
        moderation_mode=mode,
        created_at=now,
        **content,
    )
    _mark_sender_side_read(conversation, participant=participant, now=now)
    if status == MessageStatus.PUBLISHED:
        _deliver(message, now, request)
    return message


@transaction.atomic
def write_to_organizer(*, user, competition, body: str, request=None) -> Message:
    """„Napisz do organizatora”: pierwsza wiadomość zakłada rozmowę, kolejne do niej trafiają."""
    participant = ensure_participant(user, competition)
    ensure_enabled(competition)
    text = _clean_body(body)
    conversation = _organizer_conversation(participant)
    return send_participant_message(
        user=user, competition=competition, conversation=conversation, body=text, request=request
    )


def can_start_with(participant, profile: ChatProfile) -> bool:
    """Czy tę osobę da się teraz zaprosić do **nowej** rozmowy – ta sama reguła, co katalog."""
    return directory(participant).filter(pk=profile.pk).exists()


def _start_target(participant, competition, token: str) -> ChatProfile:
    profile = (
        ChatProfile.objects.for_competition(competition)
        .filter(token=token)
        .select_related("participant__user")
        .first()
    )
    if profile is None:
        raise _not_found("Nie ma takiej osoby w katalogu.")
    if profile.participant_id == participant.pk:
        raise DomainError("Nie możesz napisać do siebie.", "CHAT_SELF", http.HTTP_400_BAD_REQUEST)
    return profile


@transaction.atomic
def start_peer_conversation(*, user, competition, token: str, body: str, request=None) -> Message:
    """Pierwsza (jawna) wiadomość do osoby z katalogu. Istniejąca rozmowa z nią – wiadomość w niej.

    Warunek ``discoverable`` dotyczy wyłącznie **zaczęcia** rozmowy (§ 3): kto raz zaczął rozmawiać,
    może odpowiadać także po tym, jak druga strona wypisała się z katalogu. Przy włączonym
    szyfrowaniu nowa rozmowa jest zawsze szyfrowana i zakłada się ją :func:`open_encrypted_conversation`.
    """
    participant = ensure_participant(user, competition)
    ensure_enabled(competition)
    row = settings_for(competition)
    if PeerMode(row.peer_mode) == PeerMode.OFF:
        raise DomainError(PEER_OFF, "CHAT_PEER_OFF", http.HTTP_400_BAD_REQUEST)
    profile = _start_target(participant, competition, token)
    other = profile.participant
    existing = find_peer_conversation(participant, other)
    if existing is None:
        if row.e2e_enabled:
            raise DomainError(
                "Nowe rozmowy są szyfrowane – rozpocznij rozmowę przyciskiem "
                "„Rozpocznij rozmowę szyfrowaną”.",
                "CHAT_E2E_REQUIRED",
                http.HTTP_400_BAD_REQUEST,
            )
        if not can_start_with(participant, profile) or has_blocked(participant, other):
            raise DomainError(CANNOT_SEND, "CHAT_CANNOT_START", http.HTTP_400_BAD_REQUEST)
        _clean_body(body)
        existing = _peer_conversation(participant, other)
    return send_participant_message(
        user=user, competition=competition, conversation=existing, body=body, request=request
    )


@transaction.atomic
def open_encrypted_conversation(*, user, competition, token: str, request=None) -> Conversation:
    """Zakłada **pustą** rozmowę szyfrowaną z osobą z katalogu (albo oddaje istniejącą rozmowę z nią).

    Pusta, bo klucz rozmowy wyprowadza się z jej identyfikatora (HKDF, sól = id rozmowy): pierwszą
    wiadomość przeglądarka zaszyfruje dopiero wtedy, gdy rozmowa istnieje. Warunki są te same, co
    przy rozmowie jawnej, plus dwa szyfrowania: tryb dopuszcza teraz zapis w rozmowach szyfrowanych
    i obie strony mają klucz.
    """
    participant = ensure_participant(user, competition)
    ensure_enabled(competition)
    row = settings_for(competition)
    mode, stage = mode_and_stage(competition, row=row)
    if not row.e2e_enabled:
        raise DomainError(
            "Organizator nie włączył szyfrowanych rozmów.", "CHAT_E2E_DISABLED", http.HTTP_400_BAD_REQUEST
        )
    refusal = encrypted_refusal(row=row, mode=mode, stage=stage)
    if refusal:
        raise DomainError(refusal, "CHAT_CANNOT_WRITE", http.HTTP_400_BAD_REQUEST)
    profile = _start_target(participant, competition, token)
    other = profile.participant
    existing = find_peer_conversation(participant, other)
    if existing is not None:
        return existing
    if key_for(participant) is None:
        raise DomainError(NEEDS_KEY, "CHAT_E2E_NO_KEY", http.HTTP_400_BAD_REQUEST)
    if not can_start_with(participant, profile) or has_blocked(participant, other):
        raise DomainError(CANNOT_SEND, "CHAT_CANNOT_START", http.HTTP_400_BAD_REQUEST)
    return _peer_conversation(participant, other, encrypted=True)


# --- strona organizatora --------------------------------------------------------------------------------


def organizer_conversation(competition, pk) -> Conversation:
    """Rozmowa organizatorska **tego** konkursu albo 404. Rozmowa między uczestnikami – też 404.

    To jest bramka, która trzyma koordynatora z dala od treści rozmów między uczestnikami: pod
    adresem wątku organizatora otwiera się wyłącznie rozmowa, w której organizator **jest stroną**.
    """
    conversation = (
        Conversation.objects.for_competition(competition)
        .filter(pk=pk, kind=ConversationKind.ORGANIZER)
        .select_related("participant__user")
        .first()
    )
    if conversation is None:
        raise _not_found()
    return conversation


def organizer_inbox(competition, *, unread_only: bool = False):
    last = Message.objects.filter(conversation=OuterRef("pk"), status__in=DELIVERED_STATUSES).order_by(
        "-created_at", "-id"
    )
    rows = (
        Conversation.objects.for_competition(competition)
        .filter(kind=ConversationKind.ORGANIZER)
        .annotate(
            last_body=Subquery(last.values("body")[:1]),
            last_status=Subquery(last.values("status")[:1]),
            last_role=Subquery(last.values("sender_role")[:1]),
            last_created_at=Subquery(last.values("created_at")[:1]),
        )
        .select_related("participant__user")
        .order_by("-last_message_at", "-id")
    )
    if unread_only:
        rows = rows.filter(organizer_unread_since__isnull=False)
    return rows


def organizer_messages(conversation: Conversation):
    return (
        Message.objects.filter(conversation=conversation)
        .select_related("sender")
        .order_by("created_at", "id")
    )


@transaction.atomic
def send_organizer_message(
    *, user, competition, conversation: Conversation, body: str, request=None
) -> Message:
    """Odpowiedź zespołu organizatora. Kanał organizatora nie ma moderacji – nigdy."""
    ensure_organizer(user, competition)
    ensure_enabled(competition)
    if not conversation.is_organizer or conversation.competition_id != competition.pk:
        raise _not_found()
    if conversation.participant_id is None:
        raise DomainError(
            "Konto tego uczestnika zostało usunięte – nie ma do kogo pisać.",
            "CHAT_PARTICIPANT_GONE",
            http.HTTP_400_BAD_REQUEST,
        )
    text = _clean_body(body)
    now = timezone.now()
    message = Message.objects.create(
        conversation=conversation,
        sender=user,
        sender_role=SenderRole.ORGANIZER,
        body=text,
        status=MessageStatus.PUBLISHED,
        created_at=now,
    )
    _mark_sender_side_read(conversation, now=now)
    _deliver(message, now, request)
    return message


@transaction.atomic
def organizer_writes_to(*, user, competition, participant, body: str, request=None) -> Message:
    """Koordynator zaczyna (albo kontynuuje) rozmowę z wybranym uczestnikiem z jego karty."""
    ensure_organizer(user, competition)
    ensure_enabled(competition)
    if participant.competition_id != competition.pk:
        raise _not_found("Nie ma takiego uczestnika.")
    text = _clean_body(body)
    conversation = _organizer_conversation(participant)
    return send_organizer_message(
        user=user, competition=competition, conversation=conversation, body=text, request=request
    )


# --- odczyt -----------------------------------------------------------------------------------------------


def mark_read(member: ConversationMember, now=None) -> None:
    """Otwarcie wątku: gasi kropkę i zapisuje odczyt. Bez zapisu, gdy nie ma czego zmieniać.

    Zapis tylko wtedy, gdy coś czeka albo od ostatniego listu nikt nie czytał – odświeżanie wątku
    co kwadrans minuty nie ma robić ``UPDATE`` przy każdym odpytaniu.
    """
    now = now or timezone.now()
    stale = member.notified_at is not None and (
        member.last_read_at is None or member.last_read_at < member.notified_at
    )
    if member.unread_since is None and member.last_read_at is not None and not stale:
        return
    ConversationMember.objects.filter(pk=member.pk).update(unread_since=None, last_read_at=now)
    member.unread_since = None
    member.last_read_at = now


def mark_organizer_read(conversation: Conversation, now=None) -> None:
    now = now or timezone.now()
    notified = conversation.organizer_notified_at
    last = conversation.organizer_last_read_at
    stale = notified is not None and (last is None or last < notified)
    if conversation.organizer_unread_since is None and last is not None and not stale:
        return
    Conversation.objects.filter(pk=conversation.pk).update(
        organizer_unread_since=None, organizer_last_read_at=now
    )
    conversation.organizer_unread_since = None
    conversation.organizer_last_read_at = now


def participant_unread_count(participant) -> int:
    return ConversationMember.objects.filter(participant=participant, unread_since__isnull=False).count()


def nav_state(participant) -> tuple[bool, int]:
    """Pozycja „Wiadomości” w pasku konta: czy moduł działa i ile rozmów czeka – **jednym** zapytaniem.

    Pasek konta jest na każdej stronie serwisu, więc procesor kontekstu nie może płacić dwoma
    zapytaniami (ustawienia konkursu i licznik) za każdą odsłonę uczestnika. Oba odczyty są tu
    podzapytaniami jednego ``SELECT``-a po wierszu profilu, który procesor już zna.
    """
    from apps.accounts.models import Participant

    enabled = ChatSettings.objects.filter(competition_id=OuterRef("competition_id")).values("enabled")[:1]
    unread = (
        ConversationMember.objects.filter(participant_id=OuterRef("pk"), unread_since__isnull=False)
        .order_by()
        .values("participant_id")
        .annotate(n=Count("pk"))
        .values("n")[:1]
    )
    row = (
        Participant.objects.filter(pk=participant.pk)
        .annotate(chat_enabled=Subquery(enabled), chat_unread=Subquery(unread))
        .values_list("chat_enabled", "chat_unread")
        .first()
    )
    if row is None:
        return False, 0
    visible = True if row[0] is None else bool(row[0])
    return visible, (row[1] or 0) if visible else 0


# --- zgłoszenia --------------------------------------------------------------------------------------------


@transaction.atomic
def report_message(
    *, user, competition, message: Message, reason: str, reported_plaintext: str = "", request=None
) -> MessageReport:
    """„Zgłoś” – wyłącznie wiadomość **drugiej strony** w rozmowie między uczestnikami.

    Działa w każdym trybie, także w rozmowie, która po wyłączeniu kanału jest tylko do odczytu:
    zgłoszenie jest drogą bezpieczeństwa, a nie funkcją rozmowy, i nie może zniknąć razem z nią.

    Wiadomość szyfrowana przychodzi z kopią jawną odszyfrowaną w przeglądarce zgłaszającego
    (``reported_plaintext``) – bez niej moderator dostałby szyfrogram, którego nie przeczyta. Serwer
    nie może sprawdzić, że kopia zgadza się z szyfrogramem; mówi to ekran moderatora.
    """
    participant = ensure_participant(user, competition)
    ensure_enabled(competition)
    conversation = message.conversation
    if (
        conversation.competition_id != competition.pk
        or not conversation.is_peer
        or not ConversationMember.objects.filter(conversation=conversation, participant=participant).exists()
        or message.sender_id == user.pk
        or message.status != MessageStatus.PUBLISHED
    ):
        raise _not_found("Nie ma takiej wiadomości.")
    text = _clean_reason(reason, label="Powód zgłoszenia")
    copy = ""
    if message.is_encrypted:
        copy = (reported_plaintext or "").strip()
        if not copy:
            raise DomainError(
                "Nie udało się odszyfrować wiadomości do zgłoszenia – odblokuj szyfrowanie "
                "i spróbuj ponownie.",
                "CHAT_REPORT_PLAINTEXT_REQUIRED",
                http.HTTP_400_BAD_REQUEST,
            )
        if len(copy) > MAX_BODY_LENGTH:
            raise DomainError(
                "Zgłaszana treść jest za długa.", "CHAT_BODY_TOO_LONG", http.HTTP_400_BAD_REQUEST
            )
    if MessageReport.objects.filter(message=message, reporter=user).exists():
        raise DomainError(
            "Ta wiadomość jest już zgłoszona – organizator ją przejrzy.",
            "CHAT_ALREADY_REPORTED",
            http.HTTP_400_BAD_REQUEST,
        )
    return MessageReport.objects.create(message=message, reporter=user, reason=text, reported_plaintext=copy)


# --- moderacja ----------------------------------------------------------------------------------------------
#
# Każda czynność moderatora zostawia wpis audytowy **bez treści wiadomości** – ta sama reguła i ten
# sam powód, co na forum (``apps.forum.services``): dziennik zdarzeń zostaje na stałe i nie podlega
# anonimizacji konta, więc skopiowana tam treść byłaby danymi, których nie zdejmie żadne żądanie.


def moderator_visible_q() -> Q:
    """Wiadomości między uczestnikami, których treść koordynator **ma prawo** zobaczyć.

    Wysłane w trybie z moderacją (nadawca czytał nad formularzem, że organizator może je przeczytać)
    albo zgłoszone przez odbiorcę. Wszystko inne – wysłane w ``NONE`` i niezgłoszone – jest dla
    koordynatora niewidoczne także jako kontekst zgłoszenia innej wiadomości tej samej rozmowy.
    """
    reported = MessageReport.objects.filter(message=OuterRef("pk"))
    return Q(moderation_mode__in=MODERATED_MODES) | Q(Exists(reported))


def _peer_messages(competition):
    return Message.objects.for_competition(competition).filter(conversation__kind=ConversationKind.PEER)


def _review_q() -> Q:
    return Q(status=MessageStatus.PUBLISHED, moderation_mode=PeerMode.POST, reviewed_at__isnull=True)


#: Relacje potrzebne pozycji kolejki: nadawca i obie strony rozmowy (nadawca → odbiorca).
_QUEUE_RELATED = (
    "sender",
    "conversation",
    "conversation__participant_low__user",
    "conversation__participant_high__user",
)


def pending_messages(competition):
    return (
        _peer_messages(competition)
        .filter(status=MessageStatus.PENDING)
        .select_related(*_QUEUE_RELATED)
        .order_by("created_at", "id")
    )


def review_messages(competition):
    return (
        _peer_messages(competition)
        .filter(_review_q())
        .select_related(*_QUEUE_RELATED)
        .order_by("created_at", "id")
    )


def open_reports(competition):
    return (
        MessageReport.objects.for_competition(competition)
        .filter(resolved_at__isnull=True)
        .select_related("reporter", *(f"message__{path}" for path in _QUEUE_RELATED))
        .order_by("created_at", "id")
    )


def moderation_count(competition) -> int:
    """Pozycje kolejki: czekające, nieprzejrzane i otwarte zgłoszenia – to samo, co liczy ekran."""
    counts = _peer_messages(competition).aggregate(
        pending=Count("pk", filter=Q(status=MessageStatus.PENDING)),
        review=Count("pk", filter=_review_q()),
    )
    reports = MessageReport.objects.for_competition(competition).filter(resolved_at__isnull=True).count()
    return (counts["pending"] or 0) + (counts["review"] or 0) + reports


def organizer_unread_count(competition) -> int:
    return (
        Conversation.objects.for_competition(competition)
        .filter(kind=ConversationKind.ORGANIZER, organizer_unread_since__isnull=False)
        .count()
    )


def coordinator_attention(competition) -> int:
    """Odznaka „Wiadomości” w menu panelu: nieprzeczytane rozmowy organizatorskie + kolejka.

    Wyłączony moduł oddaje zero – pozycja prowadzi wtedy do ekranu ustawień, na którym nie ma nic
    „do zrobienia”.

    **Jedno zapytanie** – przełącznik i cztery liczby jako podzapytania jednego ``SELECT``-a po wierszu
    konkursu. Liczniki menu liczą się na zimno przy pierwszym wejściu do panelu, a budżet zapytań
    pulpitu jest twardą bramką (``apps/core/tests/query_budgets.py``); cztery osobne ``COUNT``-y
    byłyby czterema zapytaniami za odznakę, którą ta sama liczba opisuje w całości. Definicje są te
    same, co :func:`organizer_unread_count` i :func:`moderation_count` (ekran kolejki).
    """
    if competition is None:
        return 0
    from django.db.models.functions import Coalesce

    from apps.tenancy.models import Competition

    def counted(rows, group: str):
        grouped = rows.order_by().values(group).annotate(n=Count("pk")).values("n")[:1]
        return Coalesce(Subquery(grouped), 0)

    here = OuterRef("pk")
    row = (
        Competition.objects.filter(pk=competition.pk)
        .annotate(
            chat_enabled=Subquery(ChatSettings.objects.filter(competition=here).values("enabled")[:1]),
            chat_unread=counted(
                Conversation.objects.filter(
                    competition=here, kind=ConversationKind.ORGANIZER, organizer_unread_since__isnull=False
                ),
                "competition",
            ),
            chat_pending=counted(
                Message.objects.filter(
                    conversation__competition=here,
                    conversation__kind=ConversationKind.PEER,
                    status=MessageStatus.PENDING,
                ),
                "conversation__competition",
            ),
            chat_review=counted(
                Message.objects.filter(
                    conversation__competition=here, conversation__kind=ConversationKind.PEER
                ).filter(_review_q()),
                "conversation__competition",
            ),
            chat_reports=counted(
                MessageReport.objects.filter(
                    message__conversation__competition=here, resolved_at__isnull=True
                ),
                "message__conversation__competition",
            ),
        )
        .values_list("chat_enabled", "chat_unread", "chat_pending", "chat_review", "chat_reports")
        .first()
    )
    if row is None or row[0] is False:
        return 0
    return sum(row[1:])


def moderation_context(message: Message, limit: int = 5) -> list[Message]:
    """Kilka poprzednich wiadomości tej rozmowy – **tylko** tych, które moderator może czytać."""
    rows = (
        Message.objects.filter(conversation_id=message.conversation_id, created_at__lte=message.created_at)
        .exclude(pk=message.pk)
        .filter(moderator_visible_q())
        .select_related("sender")
        .order_by("-created_at", "-id")[:limit]
    )
    return list(reversed(rows))


def peer_message(competition, pk) -> Message:
    """Wiadomość między uczestnikami tego konkursu – do czynności moderatora. Inaczej 404."""
    message = (
        _peer_messages(competition)
        .filter(pk=pk)
        .filter(moderator_visible_q())
        .select_related("conversation", "sender")
        .first()
    )
    if message is None:
        raise _not_found("Nie ma takiej wiadomości.")
    return message


def _moderation_diff(message: Message, before: str, **extra) -> dict:
    return {
        "conversation_id": message.conversation_id,
        "status_before": str(before),
        "status_after": str(message.status),
        **extra,
    }


@transaction.atomic
def approve(*, message: Message, actor, request=None) -> Message:
    """Akceptacja wiadomości z premoderacji: dopiero teraz odbiorca ją widzi i dostaje list."""
    if message.status != MessageStatus.PENDING:
        raise DomainError(
            "Ta wiadomość nie czeka na akceptację.", "CHAT_NOT_PENDING", http.HTTP_400_BAD_REQUEST
        )
    before = message.status
    now = timezone.now()
    message.status = MessageStatus.PUBLISHED
    message.moderated_by = actor
    message.moderated_at = now
    message.reviewed_at = now
    message.save(update_fields=["status", "moderated_by", "moderated_at", "reviewed_at"])
    _deliver(message, now, request)
    audit(actor, AUDIT_MESSAGE_APPROVED, message, _moderation_diff(message, before), request=request)
    return message


@transaction.atomic
def reject(*, message: Message, actor, note: str, request=None) -> Message:
    """Odrzucenie wiadomości z premoderacji. Notatka obowiązkowa – nadawca zobaczy ją przy wiadomości."""
    if message.status != MessageStatus.PENDING:
        raise DomainError(
            "Ta wiadomość nie czeka na akceptację.", "CHAT_NOT_PENDING", http.HTTP_400_BAD_REQUEST
        )
    text = _clean_reason(note, label="Uzasadnienie odrzucenia")
    before = message.status
    now = timezone.now()
    message.status = MessageStatus.REJECTED
    message.moderated_by = actor
    message.moderated_at = now
    message.moderation_note = text
    message.reviewed_at = now
    message.save(update_fields=["status", "moderated_by", "moderated_at", "moderation_note", "reviewed_at"])
    audit(
        actor,
        AUDIT_MESSAGE_REJECTED,
        message,
        _moderation_diff(message, before, has_note=True),
        request=request,
    )
    return message


@transaction.atomic
def hide(*, message: Message, actor, note: str = "", request=None) -> Message:
    """Ukrycie doręczonej wiadomości. Zamyka też jej otwarte zgłoszenia – ukrycie **jest** decyzją o nich."""
    if message.status != MessageStatus.PUBLISHED:
        raise DomainError(
            "Ukryć można tylko doręczoną wiadomość.", "CHAT_NOT_PUBLISHED", http.HTTP_400_BAD_REQUEST
        )
    before = message.status
    now = timezone.now()
    message.status = MessageStatus.HIDDEN
    message.moderated_by = actor
    message.moderated_at = now
    message.moderation_note = (note or "").strip()[:MAX_REASON_LENGTH]
    message.reviewed_at = message.reviewed_at or now
    message.save(update_fields=["status", "moderated_by", "moderated_at", "moderation_note", "reviewed_at"])
    for report in MessageReport.objects.filter(message=message, resolved_at__isnull=True):
        resolve_report(report=report, actor=actor, request=request)
    audit(
        actor,
        AUDIT_MESSAGE_HIDDEN,
        message,
        _moderation_diff(message, before, has_note=bool(message.moderation_note)),
        request=request,
    )
    return message


@transaction.atomic
def mark_reviewed(*, message: Message, actor, request=None) -> Message:
    """Postmoderacja: „przejrzałem, zostaje”. Wiadomość znika z kolejki przejrzeń."""
    if message.reviewed_at is not None:
        return message
    message.reviewed_at = timezone.now()
    message.save(update_fields=["reviewed_at"])
    audit(
        actor, AUDIT_MESSAGE_REVIEWED, message, {"conversation_id": message.conversation_id}, request=request
    )
    return message


@transaction.atomic
def resolve_report(*, report: MessageReport, actor, request=None) -> MessageReport:
    if report.resolved_at is not None:
        return report
    report.resolved_at = timezone.now()
    report.resolved_by = actor
    report.save(update_fields=["resolved_at", "resolved_by"])
    audit(actor, AUDIT_REPORT_RESOLVED, report, {"message_id": report.message_id}, request=request)
    return report


@transaction.atomic
def bulk_approve(*, competition, actor, ids, request=None) -> int:
    """„Akceptuj zaznaczone” – przez tę samą funkcję, co pojedyncza akceptacja (wpis audytu na każdą)."""
    approved = 0
    for message in pending_messages(competition).filter(pk__in=list(ids)):
        approve(message=message, actor=actor, request=request)
        approved += 1
    return approved


# --- dane konta -----------------------------------------------------------------------------------------


def erase_for_user(user) -> int:
    """Anonimizacja konta: znikają profil katalogu, klucz szyfrowania, blokady i ustawienia listów.

    Wiadomości **zostają** – są częścią rozmowy drugiej strony, a podpis pod nimi po anonimizacji
    brzmi „Użytkownik usunięty” (``display_author``). To, z kim ta osoba nie chciała rozmawiać
    i czy była w katalogu, rozmową nie jest.
    """
    removed = ChatProfile.objects.filter(participant__user=user).delete()[0]
    removed += ChatKey.objects.filter(participant__user=user).delete()[0]
    removed += ChatBlock.objects.filter(Q(blocker__user=user) | Q(blocked__user=user)).delete()[0]
    removed += ChatNotificationSettings.objects.filter(user=user).delete()[0]
    return removed
