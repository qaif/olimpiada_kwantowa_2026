"""Wiadomości na platformie: rozmowy 1:1 organizator–uczestnik i uczestnik–uczestnik.

Zadanie CZ-01 (30.09.2026). Forum (``apps.forum``) jest tablicą ogłoszeń czytaną przez wszystkich;
tu stoi to, czego forum świadomie nie ma – rozmowa dwóch stron. Dlatego ten moduł jest **osobną
aplikacją**, a nie trybem forum: inne są odbiorcy (dwie strony, a nie cały konkurs), inna jest
prywatność (treść czyta moderator tylko w trybie, o którym nadawca wie z góry) i inna jest
wysyłka (list „masz wiadomość”, a nie zbiorcze podsumowanie wątków). Z forum bierzemy wyłącznie
dwie czyste funkcje: podpis :func:`apps.forum.models.display_author` i regułę „trwa etap
przyjmujący rozwiązania” (``apps.forum.services.stage_forcing_pre_moderation``).

Decyzje, na których stoją te modele:

- **dwa kanały, jedna tabela rozmów.** ``ORGANIZER`` to rozmowa uczestnika z **zespołem**
  organizatora (wspólna skrzynka koordynatorów), ``PEER`` – dwóch uczestników. Jedna tabela, bo
  wątek, wiadomość, stan odczytu i powiadomienie wyglądają w obu kanałach tak samo; różnią się
  **stroną**, a to opisują kolumny uczestników i więzy niżej,
- **stan odczytu uczestnika w osobnym wierszu** (:class:`ConversationMember`), a strony organizatora
  – w kolumnach rozmowy. Wiersz członkostwa daje skrzynce uczestnika jedno złączenie niezależnie od
  rodzaju rozmowy („moje rozmowy” to ``members__participant=ja``), zamiast warunku „jestem ``low``
  albo ``high`` albo ``participant``” w każdym zapytaniu. Strona organizatora jest jedna na
  rozmowę (skrzynka jest wspólna), więc trzy kolumny na rozmowie mówią wszystko, a wiersz
  „członka-organizatora” byłby udawaniem, że zespół jest osobą,
- **moderacja jest kolumną wiadomości** – ta sama reguła i ten sam powód, co na forum: każdy odczyt
  pyta „czy to wolno pokazać”, a odpowiedź ma stać w jednym miejscu,
- **tryb z chwili wysłania zostaje na wiadomości** (``moderation_mode``). Nadawca widzi nad
  formularzem, kto może przeczytać jego wiadomość; to zdanie jest obietnicą, a obietnica dotyczy
  wiadomości, nie konkursu. Zmiana trybu na „bez moderacji” nie otwiera więc moderatorowi wiadomości
  wysłanych wcześniej z inną obietnicą – i odwrotnie,
- **nadawca zostaje, tożsamość nie** (``SET_NULL``, podpis przez ``display_author``) – jak na forum:
  usunięte konto nie wycina drugiej stronie połowy rozmowy.

**Szyfrowanie end-to-end** (§ 11 zadania) jest opcją rozmów między uczestnikami, dozwoloną wyłącznie
w trybie ``NONE`` – tam, gdzie organizator i tak obiecał, że treści nie czyta. Wiadomość szyfrowana
ma pusty ``body`` i niesie szyfrogram razem z kluczami publicznymi obu stron z chwili wysłania;
klucz tożsamości uczestnika (:class:`ChatKey`) powstaje w przeglądarce, a jego prywatna połowa leży
na serwerze wyłącznie owinięta hasłem, którego serwer nie zna. Kanał organizatora nie jest szyfrowany
nigdy (więz w bazie).

Czego tu **nie ma**: załączników, HTML-a, edycji i usuwania wysłanej wiadomości, rozmów grupowych,
a także panelu administracyjnego z treścią wiadomości – ``/admin/`` byłby drogą do prywatnych
rozmów, która omija wszystkie reguły moderacji opisane niżej.
"""

from __future__ import annotations

import secrets

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from apps.competitions.scoping import competition_scoped_manager

#: Twardy limit długości wiadomości. Odrzuca formularz, a nie obcina – ta sama reguła, co na forum
#: i przy zgłoszeniach: obcięta wiadomość kończyłaby się w połowie zdania. Cztery tysiące znaków
#: to dwie strony – rozmowa, a nie wklejone rozwiązanie zadania.
MAX_BODY_LENGTH = 4000

#: Limit powodu zgłoszenia i notatki moderatora. Jedno zdanie dla człowieka po drugiej stronie.
MAX_REASON_LENGTH = 500

#: Rozmowy szyfrowane (§ 11): limit szyfrogramu odpowiada ``MAX_BODY_LENGTH`` znakom w najgorszym
#: przypadku UTF-8 (4 bajty na znak) plus 16 bajtów znacznika GCM – po zakodowaniu w base64.
MAX_CIPHERTEXT_LENGTH = ((MAX_BODY_LENGTH * 4 + 16 + 2) // 3) * 4
#: Minimalna liczba iteracji PBKDF2-SHA-256 kopii klucza prywatnego – zalecenie OWASP (2023).
MIN_KDF_ITERATIONS = 600_000
MAX_KDF_ITERATIONS = 10_000_000

#: Zbijanie powiadomień: najwyżej jeden list o jednej rozmowie do jednego odbiorcy na tyle godzin.
NOTIFY_INTERVAL_HOURS = 3

#: Ile wiadomości wątku pokazujemy naraz (najnowsze). Rozmowa 1:1 dłuższa niż to jest archiwum,
#: a nie rozmową, i ekran ma się otwierać szybko także na telefonie.
THREAD_LIMIT = 200

#: Pozycje katalogu na stronie – wymóg zadania (§ 3).
DIRECTORY_PAGE_SIZE = 20


class PeerMode(models.TextChoices):
    """Czy i jak uczestnicy mogą pisać do siebie nawzajem. Ustawia koordynator.

    Domyślnie ``OFF``: rozmowa niepełnoletnich bez świadków jest dokładnie tym, czego moderacja nie
    widzi (``apps.forum.models`` – docstring modułu), więc bez świadomej decyzji organizatora kanału
    między uczestnikami po prostu nie ma. Kanał organizatora od tego trybu nie zależy.
    """

    OFF = "OFF", "wyłączone"
    PRE = "PRE", "premoderacja"
    POST = "POST", "postmoderacja"
    NONE = "NONE", "bez moderacji"


#: Tryby, w których nadawca został uprzedzony, że treść może przeczytać moderator. Wiadomość
#: wysłana w jednym z nich jest dla koordynatora widoczna w kolejce (także jako kontekst zgłoszenia);
#: wysłana w ``NONE`` – wyłącznie po zgłoszeniu. Jedna krotka, bo tę samą obietnicę czytają
#: kolejka, kontekst zgłoszenia i zdanie nad formularzem.
MODERATED_MODES = (PeerMode.PRE, PeerMode.POST)


class AgePolicy(models.TextChoices):
    """Kto z kim może rozmawiać w kanale między uczestnikami (§ 12.3 zadania).

    Domyślnie ``SAME_GROUP``: niepełnoletni piszą wyłącznie z niepełnoletnimi, pełnoletni –
    z pełnoletnimi. Rozmowa 1:1 dorosłego z dzieckiem bez świadków jest tym, czego organizator
    zawodów dla młodzieży nie ma prawa ułatwiać bez świadomej decyzji; ``ANY`` jest tą decyzją
    (ekran ustawień ostrzega przed nią wprost).
    """

    SAME_GROUP = "SAME_GROUP", "tylko w tej samej grupie wiekowej"
    ANY = "ANY", "bez ograniczeń wieku"


#: Domyślny i graniczne limity nowych rozmów między uczestnikami na dobę (§ 12.4).
DEFAULT_DAILY_NEW_CONVERSATIONS = 5
MIN_DAILY_NEW_CONVERSATIONS = 1
MAX_DAILY_NEW_CONVERSATIONS = 50


class ConversationStatus(models.TextChoices):
    """Stan rozmowy organizatorskiej w skrzynce zespołu (§ 12.2). Uczestnik go nie widzi.

    Przejścia automatyczne: wiadomość uczestnika → ``OPEN`` (także z ``CLOSED`` – zamknięta sprawa,
    do której ktoś dopisał, znów czeka na zespół), odpowiedź koordynatora → ``WAITING`` (albo
    ``CLOSED`` przy „Odpowiedz i zamknij”).
    """

    OPEN = "OPEN", "otwarta"
    WAITING = "WAITING", "czeka na uczestnika"
    CLOSED = "CLOSED", "zamknięta"


class ConversationKind(models.TextChoices):
    ORGANIZER = "ORGANIZER", "z organizatorem"
    PEER = "PEER", "między uczestnikami"


class SenderRole(models.TextChoices):
    """Po której stronie stał nadawca. Kolumna, a nie wniosek z ról konta w chwili odczytu: rola
    bywa odebrana, a podpis „Organizator” pod wiadomością sprzed miesiąca ma zostać prawdą."""

    PARTICIPANT = "PARTICIPANT", "uczestnik"
    ORGANIZER = "ORGANIZER", "organizator"


class MessageStatus(models.TextChoices):
    """Stan wiadomości – te same cztery odpowiedzi moderacji, co na forum, z tym samym podziałem:
    ``REJECTED`` to decyzja **przed** doręczeniem (z notatką dla nadawcy), ``HIDDEN`` zdejmuje
    wiadomość już doręczoną (odbiorca widzi w jej miejscu informację, że ją ukryto)."""

    PENDING = "PENDING", "czeka na akceptację"
    PUBLISHED = "PUBLISHED", "doręczona"
    REJECTED = "REJECTED", "odrzucona"
    HIDDEN = "HIDDEN", "ukryta przez moderatora"


def new_profile_token() -> str:
    """Identyfikator osoby w katalogu – losowy, a nie ``pk`` profilu ani kod publiczny.

    Kolejny numer w adresie („napisz do /new/41/”) byłby licznikiem uczestników i zaproszeniem do
    przeglądania ich po kolei, także tych, którzy do katalogu się nie zapisali. Kod publiczny jest
    kluczem anonimowego oceniania i nie ma prawa pojawić się tam, gdzie widać imię.
    """
    return secrets.token_urlsafe(12)


class ChatSettings(models.Model):
    """Ustawienia Wiadomości **jednego konkursu**. Brak wiersza znaczy „domyślne”.

    Osobny model, a nie kolumny na ``tenancy.Competition`` – te same powody, co przy
    ``apps.forum.models.ForumSettings`` (tabela konkursów i jej złote testy migracji). Wiersz powstaje
    przy pierwszym zapisie ekranu ustawień (``apps.chat.services.save_settings``).
    """

    competition = models.OneToOneField(
        "tenancy.Competition",
        on_delete=models.CASCADE,
        related_name="chat_settings",
        verbose_name="konkurs",
    )
    #: Cały moduł, czyli kanał organizatora. Domyślnie włączony: rozmowa z organizatorem nie niesie
    #: ryzyka rozmowy bez świadków (drugą stroną jest administrator danych), a zastępuje maile.
    enabled = models.BooleanField("Wiadomości włączone", default=True)
    peer_mode = models.CharField(
        "rozmowy między uczestnikami", max_length=8, choices=PeerMode.choices, default=PeerMode.OFF
    )
    #: Szyfrowanie end-to-end nowych rozmów między uczestnikami (§ 11 zadania). Dozwolone **wyłącznie**
    #: przy ``peer_mode=NONE``: rozmowy, której treści serwer nie zna, nie da się moderować, więc
    #: szyfrowanie jest uczciwe tylko tam, gdzie organizator i tak obiecał, że treści nie czyta.
    #: Regułę pilnuje serwis (``apps.chat.services.save_settings``), nie tylko formularz.
    e2e_enabled = models.BooleanField("szyfrowanie end-to-end", default=False)
    age_policy = models.CharField(
        "grupa wiekowa w rozmowach uczestników",
        max_length=12,
        choices=AgePolicy.choices,
        default=AgePolicy.SAME_GROUP,
    )
    #: Ile **nowych** rozmów z innymi uczestnikami można zacząć w ciągu 24 h (okno kroczące). Limit
    #: chroni przed masowym „zagadywaniem” całego katalogu; odpowiedzi w istniejących rozmowach
    #: ogranicza osobno limit żądań ``chat``.
    daily_new_conversations = models.PositiveSmallIntegerField(
        "nowe rozmowy uczestnika na dobę", default=DEFAULT_DAILY_NEW_CONVERSATIONS
    )
    updated_at = models.DateTimeField("zmienione", default=timezone.now)

    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = "ustawienia wiadomości"
        verbose_name_plural = "ustawienia wiadomości"

    def __str__(self) -> str:
        return f"Wiadomości: {self.get_peer_mode_display()}"


class ChatProfile(models.Model):
    """Zgoda uczestnika na bycie w katalogu („inni uczestnicy mogą mnie znaleźć i do mnie napisać”).

    Domyślnie **nie**: wiersza nie ma, dopóki uczestnik sam nie włączy przełącznika. Profil należy
    do ``Participant``, a nie do konta, bo katalog jest katalogiem **konkursu** – zgoda dana
    w olimpiadzie A nie wystawia tej osoby w katalogu olimpiady B.
    """

    participant = models.OneToOneField(
        "accounts.Participant",
        on_delete=models.CASCADE,
        related_name="chat_profile",
        verbose_name="uczestnik",
    )
    discoverable = models.BooleanField("widoczny w katalogu", default=False)
    token = models.CharField(
        "identyfikator w katalogu", max_length=32, unique=True, default=new_profile_token
    )
    updated_at = models.DateTimeField("zmienione", default=timezone.now)

    objects = competition_scoped_manager("participant__competition")

    class Meta:
        verbose_name = "profil wiadomości"
        verbose_name_plural = "profile wiadomości"

    def __str__(self) -> str:
        return f"{self.participant_id}: {'w katalogu' if self.discoverable else 'poza katalogiem'}"


class Conversation(models.Model):
    """Rozmowa 1:1. Kształt kolumn zależy od rodzaju i pilnują go więzy w bazie.

    - ``ORGANIZER``: wypełnione ``participant``; jedna rozmowa na uczestnika (więz warunkowy),
      bo „napisz do organizatora” ma zawsze prowadzić do **tego samego** wątku – dwa wątki
      z organizatorem to sprawa, której połowa leży tam, gdzie koordynator akurat nie patrzy,
    - ``PEER``: para ``participant_low`` < ``participant_high`` (po ``pk``). Normalizacja kolejności
      robi z „rozmowy A z B” i „rozmowy B z A” ten sam wiersz, a więz unikalności nie musi znać
      kierunku.

    Klucze do uczestników są ``SET_NULL``: skasowanie konta jednej strony zostawia drugiej rozmowę
    (podpis „Użytkownik usunięty”), zamiast zabierać jej historię kaskadą.

    ``last_message_at`` przesuwa **wyłącznie doręczenie** – wiadomość czekająca na moderację nie
    podnosi rozmowy na liście odbiorcy, bo kolejność listy zdradzałaby, że ktoś właśnie coś napisał.
    """

    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.CASCADE,
        related_name="chat_conversations",
        verbose_name="konkurs",
    )
    kind = models.CharField("rodzaj", max_length=10, choices=ConversationKind.choices)
    participant = models.ForeignKey(
        "accounts.Participant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chat_organizer_conversations",
        verbose_name="uczestnik (rozmowa z organizatorem)",
    )
    participant_low = models.ForeignKey(
        "accounts.Participant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="uczestnik A",
    )
    participant_high = models.ForeignKey(
        "accounts.Participant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="uczestnik B",
    )
    #: Ustalane **przy utworzeniu** rozmowy między uczestnikami (= ``ChatSettings.e2e_enabled`` w tej
    #: chwili) i nigdy potem nie zmieniane: rozmowa nie może w połowie przestać być szyfrowana (ani
    #: zacząć), bo obie strony czytały nad formularzem, jaka jest. Kanał organizatora – nigdy.
    is_encrypted = models.BooleanField("szyfrowana end-to-end", default=False)
    #: Kto zaczął rozmowę między uczestnikami – podstawa dziennego limitu nowych rozmów (§ 12.4).
    started_by = models.ForeignKey(
        "accounts.Participant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="rozpoczął",
    )
    #: Rozmowa organizatorska w skrzynce zespołu (§ 12.2): komu przypisana i w jakim stanie. Pola
    #: istnieją na każdej rozmowie, ale czyta je wyłącznie panel koordynatora – uczestnik widzi
    #: zawsze tylko „Organizator”.
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chat_assigned_conversations",
        verbose_name="przypisana do",
    )
    status = models.CharField(
        "stan (skrzynka organizatora)",
        max_length=8,
        choices=ConversationStatus.choices,
        default=ConversationStatus.OPEN,
    )
    status_changed_at = models.DateTimeField("stan zmieniony", null=True, blank=True)
    created_at = models.DateTimeField("rozpoczęta", default=timezone.now)
    last_message_at = models.DateTimeField("ostatnia wiadomość", default=timezone.now, db_index=True)
    #: Strona organizatora: jeden stan na rozmowę, bo skrzynka jest wspólna dla koordynatorów.
    #: ``organizer_unread_since`` puste znaczy „wszystko przeczytane”; ustawia je doręczenie
    #: wiadomości od uczestnika, czyści otwarcie wątku przez któregokolwiek koordynatora.
    organizer_unread_since = models.DateTimeField("nieprzeczytane od (organizator)", null=True, blank=True)
    organizer_last_read_at = models.DateTimeField("ostatni odczyt (organizator)", null=True, blank=True)
    organizer_notified_at = models.DateTimeField("ostatni list (organizator)", null=True, blank=True)

    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = "rozmowa"
        verbose_name_plural = "rozmowy"
        ordering = ("-last_message_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=["competition", "participant"],
                condition=Q(kind=ConversationKind.ORGANIZER),
                name="chat_one_organizer_conversation_per_participant",
            ),
            models.UniqueConstraint(
                fields=["participant_low", "participant_high"],
                condition=Q(kind=ConversationKind.PEER),
                name="chat_one_peer_conversation_per_pair",
            ),
            # Kształt wiersza. Kolumny drugiego rodzaju są puste; para uczestników jest
            # uporządkowana, o ile obie strony jeszcze istnieją (``SET_NULL`` przy usunięciu konta).
            models.CheckConstraint(
                condition=(
                    Q(
                        kind=ConversationKind.ORGANIZER,
                        participant_low__isnull=True,
                        participant_high__isnull=True,
                    )
                    | (
                        Q(kind=ConversationKind.PEER, participant__isnull=True)
                        & (
                            Q(participant_low__isnull=True)
                            | Q(participant_high__isnull=True)
                            | Q(participant_low__lt=F("participant_high"))
                        )
                    )
                ),
                name="chat_conversation_shape",
            ),
            # Kanał organizatora nie jest szyfrowany nigdy – decyzja zadania (§ 11), a więz w bazie
            # jest po to, żeby żadna przyszła ścieżka zapisu nie mogła jej obejść.
            models.CheckConstraint(
                condition=Q(kind=ConversationKind.PEER) | Q(is_encrypted=False),
                name="chat_organizer_never_encrypted",
            ),
        ]
        indexes = [
            models.Index(
                fields=["competition", "kind", "-last_message_at"], name="chat_conversation_list_idx"
            ),
            models.Index(fields=["started_by", "created_at"], name="chat_conversation_started_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} #{self.pk}"

    @property
    def is_organizer(self) -> bool:
        return self.kind == ConversationKind.ORGANIZER

    @property
    def is_peer(self) -> bool:
        return self.kind == ConversationKind.PEER


class ConversationMember(models.Model):
    """Strona uczestnika w rozmowie: kiedy czytał, od kiedy ma nieprzeczytane i kiedy dostał list.

    Osobny wiersz zamiast kolumn ``low_last_read_at``/``high_last_read_at`` na rozmowie – uzasadnienie
    w docstringu modułu (jedno złączenie dla skrzynki uczestnika w obu kanałach).

    ``unread_since`` zamiast porównywania ``last_read_at`` z ``last_message_at``: wiadomość
    zaakceptowana przez moderatora doręcza się **teraz**, ale przesuwa rozmowę także u nadawcy –
    porównanie znaczników zapalałoby nadawcy kropkę „nieprzeczytane” przy jego własnej wiadomości.
    Znacznik ustawia wyłącznie doręczenie wiadomości **od drugiej strony**.
    """

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="members", verbose_name="rozmowa"
    )
    participant = models.ForeignKey(
        "accounts.Participant",
        on_delete=models.CASCADE,
        related_name="chat_memberships",
        verbose_name="uczestnik",
    )
    created_at = models.DateTimeField("od", default=timezone.now)
    unread_since = models.DateTimeField("nieprzeczytane od", null=True, blank=True)
    last_read_at = models.DateTimeField("ostatni odczyt", null=True, blank=True)
    notified_at = models.DateTimeField("ostatni list", null=True, blank=True)

    objects = competition_scoped_manager("conversation__competition")

    class Meta:
        verbose_name = "strona rozmowy"
        verbose_name_plural = "strony rozmów"
        constraints = [
            models.UniqueConstraint(fields=["conversation", "participant"], name="chat_member_once"),
        ]
        indexes = [
            models.Index(fields=["participant", "unread_since"], name="chat_member_unread_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.participant_id} w rozmowie {self.conversation_id}"


class Message(models.Model):
    """Jedna wiadomość. Tekst i nic poza tekstem – renderuje go ``apps.chat.templatetags``."""

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages", verbose_name="rozmowa"
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chat_messages",
        verbose_name="nadawca",
    )
    sender_role = models.CharField("strona nadawcy", max_length=12, choices=SenderRole.choices)
    #: Treść jawna. Pusta w rozmowie szyfrowanej – tam treść jest wyłącznie w ``ciphertext``.
    body = models.TextField("treść", max_length=MAX_BODY_LENGTH, blank=True)
    #: Rozmowa szyfrowana (§ 11): szyfrogram AES-GCM i jego IV (base64). Serwer nie zna klucza.
    ciphertext = models.TextField("szyfrogram", blank=True)
    iv = models.CharField("IV szyfrogramu", max_length=32, blank=True)
    #: Klucze publiczne (SPKI, base64) obu stron **w chwili wysłania**. Wpisuje je serwer z bieżących
    #: ``ChatKey``, a nie przeglądarka. Bez nich strona, która klucza nie zmieniała, nie odszyfrowałaby
    #: starszych wiadomości po zmianie klucza drugiej strony, a wątek nie umiałby powiedzieć, w którym
    #: miejscu klucz się zmienił (notka „Klucz szyfrowania tej osoby zmienił się”).
    sender_public_key = models.TextField("klucz publiczny nadawcy", blank=True)
    recipient_public_key = models.TextField("klucz publiczny odbiorcy", blank=True)
    status = models.CharField(
        "stan", max_length=10, choices=MessageStatus.choices, default=MessageStatus.PUBLISHED
    )
    #: Tryb rozmów między uczestnikami obowiązujący **w chwili wysłania** (pusty w kanale
    #: organizatora, który moderacji nie ma). Uzasadnienie w docstringu modułu.
    moderation_mode = models.CharField(
        "tryb w chwili wysłania", max_length=8, choices=PeerMode.choices, blank=True
    )
    created_at = models.DateTimeField("wysłana", default=timezone.now)
    moderated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chat_moderated_messages",
        verbose_name="moderował",
    )
    moderated_at = models.DateTimeField("moderowana", null=True, blank=True)
    moderation_note = models.CharField("notatka moderatora", max_length=MAX_REASON_LENGTH, blank=True)
    #: Postmoderacja: pusta znaczy „czeka na przejrzenie”. Kto przejrzał, mówi dziennik zdarzeń.
    reviewed_at = models.DateTimeField("przejrzana", null=True, blank=True)

    objects = competition_scoped_manager("conversation__competition")

    class Meta:
        verbose_name = "wiadomość"
        verbose_name_plural = "wiadomości"
        ordering = ("created_at", "id")
        indexes = [
            models.Index(fields=["conversation", "created_at"], name="chat_message_thread_idx"),
            models.Index(fields=["status", "moderation_mode", "reviewed_at"], name="chat_message_queue_idx"),
            models.Index(fields=["sender", "-created_at"], name="chat_message_sender_idx"),
        ]
        constraints = [
            # Dokładnie jedna postać treści: jawna albo szyfrogram z IV. Wiadomość z obiema byłaby
            # szyfrowaną rozmową, obok której leży jej jawna kopia.
            models.CheckConstraint(
                condition=(Q(ciphertext="", iv="") & ~Q(body=""))
                | (Q(body="") & ~Q(ciphertext="") & ~Q(iv="")),
                name="chat_message_body_or_ciphertext",
            ),
        ]

    def __str__(self) -> str:
        return f"wiadomość #{self.pk} w rozmowie {self.conversation_id}"

    @property
    def is_encrypted(self) -> bool:
        return bool(self.ciphertext)

    @property
    def is_pending(self) -> bool:
        return self.status == MessageStatus.PENDING

    @property
    def is_published(self) -> bool:
        return self.status == MessageStatus.PUBLISHED

    @property
    def is_rejected(self) -> bool:
        return self.status == MessageStatus.REJECTED

    @property
    def is_hidden(self) -> bool:
        return self.status == MessageStatus.HIDDEN


class MessageReport(models.Model):
    """„Zgłoś”: odbiorca uważa, że tej wiadomości nie powinno być. Trafia do kolejki koordynatora.

    Zgłoszenie **nie ukrywa** wiadomości (ta sama decyzja, co na forum) i jest jedyną drogą, którą
    treść rozmowy prowadzonej bez moderacji w ogóle dociera do organizatora.
    """

    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="reports", verbose_name="wiadomość"
    )
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chat_reports",
        verbose_name="zgłaszający",
    )
    reason = models.CharField("powód", max_length=MAX_REASON_LENGTH)
    #: Zgłoszenie wiadomości **szyfrowanej**: jawna kopia odszyfrowana w przeglądarce zgłaszającego.
    #: Serwer nie ma klucza, więc nie może potwierdzić, że to ta treść – moderator widzi to zdanie przy
    #: zgłoszeniu. Puste przy wiadomościach jawnych (tam moderator czyta ``Message.body``).
    reported_plaintext = models.TextField(
        "treść przekazana przez zgłaszającego", max_length=MAX_BODY_LENGTH, blank=True
    )
    created_at = models.DateTimeField("zgłoszone", default=timezone.now)
    resolved_at = models.DateTimeField("rozpatrzone", null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chat_resolved_reports",
        verbose_name="rozpatrzył",
    )

    objects = competition_scoped_manager("message__conversation__competition")

    class Meta:
        verbose_name = "zgłoszenie wiadomości"
        verbose_name_plural = "zgłoszenia wiadomości"
        ordering = ("created_at", "id")
        constraints = [
            models.UniqueConstraint(fields=["message", "reporter"], name="chat_report_once"),
        ]

    def __str__(self) -> str:
        return f"zgłoszenie wiadomości #{self.message_id}"


class ChatBlock(models.Model):
    """„Zablokuj”: ``blocked`` nie może pisać do ``blocker`` ani zacząć z nim rozmowy.

    Między uczestnikami tego samego konkursu (profil jest per konkurs). Organizatora blokada nie
    dotyczy – kanał organizatora jest kanałem administratora danych, nie rozmową towarzyską.
    """

    blocker = models.ForeignKey(
        "accounts.Participant",
        on_delete=models.CASCADE,
        related_name="chat_blocks_made",
        verbose_name="blokujący",
    )
    blocked = models.ForeignKey(
        "accounts.Participant",
        on_delete=models.CASCADE,
        related_name="chat_blocks_received",
        verbose_name="zablokowany",
    )
    created_at = models.DateTimeField("od", default=timezone.now)

    objects = competition_scoped_manager("blocker__competition")

    class Meta:
        verbose_name = "blokada"
        verbose_name_plural = "blokady"
        constraints = [
            models.UniqueConstraint(fields=["blocker", "blocked"], name="chat_block_once"),
            models.CheckConstraint(condition=~Q(blocker=F("blocked")), name="chat_block_not_self"),
        ]

    def __str__(self) -> str:
        return f"{self.blocker_id} blokuje {self.blocked_id}"


class ReplyTemplate(models.Model):
    """Szablon odpowiedzi koordynatora (§ 12.1) – tylko do kanału organizatora.

    Znacznik ``{imie}`` podmienia przeglądarka na imię uczestnika rozmowy w chwili wstawienia do
    pola wiadomości (``static/js/chat-templates.js``); w bazie szablon zostaje tekstem ze znacznikiem.
    """

    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.CASCADE,
        related_name="chat_reply_templates",
        verbose_name="konkurs",
    )
    title = models.CharField("nazwa", max_length=120)
    body = models.TextField("treść", max_length=MAX_BODY_LENGTH)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="autor",
    )
    updated_at = models.DateTimeField("zmieniony", default=timezone.now)

    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = "szablon odpowiedzi"
        verbose_name_plural = "szablony odpowiedzi"
        ordering = ("title", "id")

    def __str__(self) -> str:
        return self.title


class ChatKey(models.Model):
    """Klucz tożsamości uczestnika do rozmów szyfrowanych (§ 11 zadania): publiczny jawnie, prywatny
    **owinięty** hasłem do wiadomości, którego serwer nie zna.

    Para powstaje w przeglądarce (WebCrypto, ECDH P-256). Na serwer trafia klucz publiczny (SPKI)
    i kopia klucza prywatnego zaszyfrowana AES-GCM kluczem z PBKDF2-SHA-256 – po to, żeby uczestnik
    mógł czytać swoje rozmowy na drugim urządzeniu. Bez hasła ta kopia jest dla serwera szumem.

    ``fingerprint`` liczy **serwer** (SHA-256 z SPKI), a nie przeglądarka: odcisk jest tym, co dwie
    osoby porównują na żywo, więc nie może pochodzić z tego samego źródła, co sprawdzany klucz.
    Klucz należy do profilu uczestnika – jak katalog, jest per konkurs. Zmiana klucza (zapomniane
    hasło) nadpisuje wiersz; klucze użyte w starszych wiadomościach stoją na samych wiadomościach.
    """

    participant = models.OneToOneField(
        "accounts.Participant",
        on_delete=models.CASCADE,
        related_name="chat_key",
        verbose_name="uczestnik",
    )
    public_key = models.TextField("klucz publiczny (SPKI, base64)")
    wrapped_private_key = models.TextField("klucz prywatny zaszyfrowany hasłem (base64)")
    kdf_salt = models.CharField("sól PBKDF2 (base64)", max_length=64)
    wrap_iv = models.CharField("IV owinięcia (base64)", max_length=32)
    kdf_iterations = models.PositiveIntegerField("iteracje PBKDF2")
    fingerprint = models.CharField("odcisk klucza (SHA-256, hex)", max_length=64)
    created_at = models.DateTimeField("utworzony", default=timezone.now)

    objects = competition_scoped_manager("participant__competition")

    class Meta:
        verbose_name = "klucz szyfrowania wiadomości"
        verbose_name_plural = "klucze szyfrowania wiadomości"

    def __str__(self) -> str:
        return f"{self.participant_id}: {self.fingerprint[:16]}"


class ChatNotificationSettings(models.Model):
    """Czy wysyłać list „masz nową wiadomość”. Brak wiersza znaczy **tak**.

    Po stronie konta, nie konkursu – ta sama reguła, co ``ForumNotificationSettings``: o skrzynce
    decyduje jej właściciel, a nie to, w której olimpiadzie akurat pisze.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_notification_settings",
        verbose_name="konto",
    )
    email_on_message = models.BooleanField("list o nowej wiadomości", default=True)
    updated_at = models.DateTimeField("zmienione", auto_now=True)

    class Meta:
        verbose_name = "ustawienia powiadomień o wiadomościach"
        verbose_name_plural = "ustawienia powiadomień o wiadomościach"

    def __str__(self) -> str:
        return f"{self.user_id}: {'listy' if self.email_on_message else 'bez listów'}"


#: Limity ogłoszenia organizatora (CZ-ANN-01). Treść ma ten sam sufit, co wiadomość: ogłoszenie to
#: kilka zdań i odnośnik, a nie regulamin wklejony nad skrzynką.
ANNOUNCEMENT_TITLE_LENGTH = 200
ANNOUNCEMENT_BODY_LENGTH = MAX_BODY_LENGTH


class OrganizerAnnouncement(models.Model):
    """Ogłoszenie organizatora nad skrzynką Wiadomości i na pulpicie uczestnika (zadanie CZ-ANN-01).

    **Nie jest wiadomością w rozmowie** (:class:`Message`) i to jest cała decyzja tego modelu.
    Rozmowa jest 1:1, bywa szyfrowana end-to-end i powstaje dopiero z pierwszą wiadomością – konto
    założone jutro nie miałoby wątku, do którego dałoby się „dosłać” ogłoszenie, a kilka tysięcy
    kopii zapełniłoby wspólną skrzynkę zespołu odpowiedziami „dziękuję”. Ogłoszenie jest więc
    jednym jawnym wierszem konkursu, czytanym **w chwili wyświetlenia** (reguła widoczności:
    ``apps.chat.announcements.visible_announcements``) – widzi je każdy uczestnik, także ten, który
    zarejestruje się po publikacji.

    Nie jest też banerem ``cms.Announcement``: tamten wisi nad każdą stroną serwisu (także dla
    niezalogowanych) i ma jedną linijkę tekstu, a to jest wiadomość do zalogowanych uczestników,
    z tytułem i treścią, w miejscu, w którym czytają oni korespondencję od organizatora.

    Stanu „przeczytane” świadomie nie ma: ogłoszenie jest wspólne, a odczyt per konto byłby nową
    daną osobową bez potrzeby (docs/tasks/CZ-ANN-01.md § 5).
    """

    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.CASCADE,
        related_name="chat_announcements",
        verbose_name="konkurs",
    )
    title = models.CharField("tytuł", max_length=ANNOUNCEMENT_TITLE_LENGTH)
    #: Zwykły tekst – renderuje go filtr ``message_body`` (escape, potem odnośniki), jak wiadomość.
    body = models.TextField("treść", max_length=ANNOUNCEMENT_BODY_LENGTH)
    #: Przełącznik koordynatora „Opublikuj / Wyłącz”. Domyślnie szkic: ogłoszenie do wszystkich
    #: uczestników nie ma się pokazać przez samo zapisanie formularza.
    is_published = models.BooleanField("opublikowane", default=False)
    #: Chwila ostatniego „Opublikuj” – podstawa kolejności „od najnowszego”. Ogłoszenie wyłączone
    #: i opublikowane ponownie wraca na górę, bo dla uczestnika pojawia się wtedy na nowo.
    published_at = models.DateTimeField("opublikowane o", null=True, blank=True)
    #: Okno widoczności. Puste końce znaczą „bez ograniczenia”; okno nie zastępuje przełącznika –
    #: „zdejmij natychmiast” jest osobną potrzebą od „to obowiązuje do piątku”.
    published_from = models.DateTimeField(
        "widoczne od", null=True, blank=True, help_text="Puste = od chwili publikacji."
    )
    published_until = models.DateTimeField(
        "widoczne do", null=True, blank=True, help_text="Puste = do wyłączenia."
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="autor",
    )
    created_at = models.DateTimeField("utworzone", default=timezone.now)
    updated_at = models.DateTimeField("zmienione", default=timezone.now)

    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = "ogłoszenie organizatora"
        verbose_name_plural = "ogłoszenia organizatora"
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["competition", "is_published"], name="chat_announcement_live_idx"),
        ]
        constraints = [
            # Okno o niedodatniej długości nigdy się nie otwiera – ogłoszenie, którego nikt nie zobaczy.
            models.CheckConstraint(
                condition=Q(published_from__isnull=True)
                | Q(published_until__isnull=True)
                | Q(published_until__gt=F("published_from")),
                name="chat_announcement_window",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def shown_since(self):
        """Chwila, od której uczestnik widzi ogłoszenie: późniejsza z publikacji i początku okna."""
        moments = [moment for moment in (self.published_at, self.published_from) if moment is not None]
        return max(moments) if moments else self.created_at

    def state(self, now=None) -> str:
        """Stan dla panelu koordynatora: ``live``, ``draft``, ``scheduled`` albo ``expired``."""
        now = now or timezone.now()
        if not self.is_published:
            return "draft"
        if self.published_from is not None and self.published_from > now:
            return "scheduled"
        if self.published_until is not None and self.published_until <= now:
            return "expired"
        return "live"
