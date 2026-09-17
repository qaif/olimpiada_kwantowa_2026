"""Zgłoszenia do organizatora: wątek sprawy zamiast listu w cudzej skrzynce.

Po co osobna aplikacja, skoro na dole każdej strony stoi adres ``contact@qaif.org``: bo poczta
gubi kontekst i gubi sprawę. Uczestnik pisze „nie mogę wysłać pracy”, a organizator nie wie ani
kim jest nadawca (adres prywatny bywa inny niż adres konta), ani w którym etapie jest zapisany,
ani co robił minutę wcześniej – i musi to wszystko odpytać zwrotnie. Dwa dni później nikt już nie
pamięta, czy odpowiedź poszła, bo jedynym śladem sprawy jest wątek w skrzynce jednej osoby.

Trzy decyzje, na których stoi ten model:

- **wątek, nie formularz.** ``SupportTicket`` niesie sprawę (kategoria, temat, stan), a
  ``SupportMessage`` jej kolejne wypowiedzi. Pojedyncze pole „treść” plus pole „odpowiedź”
  wystarczyłoby dokładnie na jedną wymianę zdań – a druga jest regułą, nie wyjątkiem,
- **kontekst zbierany automatycznie.** ``context`` niesie to, o co organizator i tak zapytałby
  w pierwszej odpowiedzi: adres strony, z której przyszło zgłoszenie, przeglądarkę, język,
  kod publiczny uczestnika, etapy, w których jest zapisany, i ostatnią czynność z audytu.
  Czego tam **nie** ma i nie może być: ciasteczek, nagłówków uwierzytelnienia, tokenów, treści
  formularzy. Kontekst jest wskazówką diagnostyczną, a nie kopią sesji,
- **zgłoszenie bez konta jest dozwolone.** ``user`` jest nullowalne, a wtedy obowiązuje ``email``.
  Osoba, która nie może się zalogować, ma najwięcej powodów, żeby napisać – wymaganie konta
  zamykałoby drzwi dokładnie tym, którzy pukają.

Skasowanie konta zabiera jego zgłoszenia (``CASCADE``), bo są jego korespondencją, a nie
dokumentem zawodów – uzasadnienie przy samym polu. Anonimizacja ich nie rusza: tam wiersz konta
zostaje, tylko bez danych osobowych.

``role_snapshot`` jest zapisany w chwili zgłoszenia i nie zmienia się nigdy. To nie jest
denormalizacja dla wygody: rola konta bywa inna w chwili sprawy niż pół roku później (uczestnik
zostaje recenzentem, recenzent kończy kadencję), a sprawę czyta się w kontekście tego, kim nadawca
był wtedy.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.competitions.scoping import competition_scoped_manager

#: Twardy limit na teksty od użytkownika. Dłuższy tekst odrzuca formularz, a nie obcina po cichu –
#: obcięte zgłoszenie kończyłoby się w połowie zdania, w którym zwykle jest sedno sprawy.
MAX_BODY_LENGTH = 10000

#: Ile minut wstecz szukamy ostatniej czynności użytkownika w audycie. Dziesięć, bo zgłoszenie
#: pisze się chwilę po tym, jak coś nie zadziałało – czynność sprzed godziny nie ma z tym związku,
#: a wciągnięta do kontekstu podpowiadałaby fałszywy trop.
CONTEXT_AUDIT_MINUTES = 10


class SupportCategory(models.TextChoices):
    """Kategorie zgłoszeń – zamknięta lista, bo po niej filtruje się kolejka koordynatora.

    Pięć pozycji odpowiada pięciu miejscom, w których człowiek utyka: wejście do serwisu, wysyłka
    pracy, wynik, zapisy i wszystko inne. „Inne” jest tu świadomie: bez niego część zgłoszeń
    trafiałaby do przypadkowej kategorii i psuła jedyną rzecz, do której kategoria służy – podział
    kolejki.
    """

    ACCOUNT = "ACCOUNT", "konto i logowanie"
    SUBMISSION = "SUBMISSION", "wysyłka pracy"
    RESULTS = "RESULTS", "wyniki"
    REGISTRATION = "REGISTRATION", "rejestracja"
    OTHER = "OTHER", "inne"


class TicketStatus(models.TextChoices):
    """Trzy stany, bo tyle jest odpowiedzi na pytanie „czy ta sprawa czeka na mnie”.

    ``ANSWERED`` jest osobno od ``CLOSED``, bo to nie to samo: odpowiedzieliśmy, ale nadawca może
    dopisać jeszcze zdanie i wtedy sprawa wraca do kolejki. ``CLOSED`` znaczy „sprawa zamknięta”
    i zamyka też formularz odpowiedzi.
    """

    OPEN = "OPEN", "otwarte"
    ANSWERED = "ANSWERED", "odpowiedziane"
    CLOSED = "CLOSED", "zamknięte"


#: Statusy sprawy, która czeka na organizatora – to one wyznaczają licznik na pulpicie i domyślne
#: sortowanie kolejki. Jedna definicja, bo licznik i kolejka muszą liczyć to samo.
PENDING_STATUSES = (TicketStatus.OPEN,)


def default_context() -> dict:
    """``default`` JSONField musi być wywoływalny i zwracać nowy obiekt."""
    return {}


class SupportTicket(models.Model):
    """Jedna sprawa zgłoszona organizatorowi."""

    # Właściciel sprawy: organizator **tego** konkursu (``docs/UNIWERSALNY-ETAP-1.md`` § 3.2).
    #
    # ``SET_NULL``, a nie ``PROTECT``: korespondencja nie może zniknąć razem z konkursem ani
    # zablokować jego usunięcia. Sprawa po skasowanym konkursie zostaje w bazie jako sprawa bez
    # adresata – czyli dokładnie to, czym się wtedy staje.
    #
    # ``null=True`` opisuje jednak przede wszystkim **inny** przypadek i to on jest tu treścią:
    # pusty konkurs znaczy „zgłoszenie do operatora platformy”. Osoba, która nie może się
    # zalogować pod adresem bez konkursu (nieznany host, strona operatora), ma mieć dokąd
    # napisać, a jej sprawa nie należy do żadnego organizatora.
    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_tickets",
        verbose_name="konkurs",
        help_text="Puste = zgłoszenie do operatora platformy.",
    )
    # ``CASCADE``, a nie ``SET_NULL``, i to jest decyzja o danych, nie o kluczu obcym: zgłoszenie
    # jest **korespondencją tej osoby**, a nie dokumentem zawodów. Konto kasowane w całości to
    # konto bez śladu w zawodach (``apps.accounts.profile._erase_account``), czyli takie, którego
    # dane nie są nikomu potrzebne do niczego – zostawienie po nim wątku z treścią sprawy byłoby
    # trzymaniem danych osobowych po żądaniu z art. 17.
    #
    # Anonimizacji to nie dotyczy i tak ma być: tam wiersz konta zostaje (pseudonimowy), więc
    # sprawa zostaje razem z nim – a wraz z nią ślad, że organizator na nią odpowiedział.
    #
    # ``null=True`` zostaje mimo ``CASCADE``, bo opisuje **inny** przypadek: zgłoszenie złożone
    # bez konta, z publicznego formularza. Wtedy adresem zwrotnym jest pole ``email`` niżej,
    # a constraint pilnuje, żeby zawsze było jedno albo drugie.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="support_tickets",
        verbose_name="zgłaszający",
    )
    # Adres kontaktowy zgłoszenia bez konta. Przy zgłoszeniu z konta zostaje pusty – adresem jest
    # wtedy adres konta i nie ma powodu trzymać jego drugiej kopii, która może się rozjechać.
    email = models.EmailField("adres e-mail (zgłoszenie bez konta)", blank=True)
    role_snapshot = models.CharField("rola w chwili zgłoszenia", max_length=40, blank=True)
    category = models.CharField(
        "kategoria", max_length=16, choices=SupportCategory.choices, default=SupportCategory.OTHER
    )
    subject = models.CharField("temat", max_length=200)
    context = models.JSONField("kontekst techniczny", default=default_context, blank=True)
    status = models.CharField(
        "status", max_length=16, choices=TicketStatus.choices, default=TicketStatus.OPEN
    )
    created_at = models.DateTimeField("zgłoszone", default=timezone.now)
    answered_at = models.DateTimeField("odpowiedziano", null=True, blank=True)
    closed_at = models.DateTimeField("zamknięte", null=True, blank=True)

    #: Własna kolumna – sprawa nie ma jak dojść do konkursu inną drogą (§ 3.5). ``for_competition``
    #: jest **ścisłe**: sprawa bez konkursu należy do operatora platformy i w kolejce organizatora
    #: nie ma czego szukać.
    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = "zgłoszenie"
        verbose_name_plural = "zgłoszenia"
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["status", "-created_at"], name="support_ticket_status_idx"),
            models.Index(fields=["user", "-created_at"], name="support_ticket_user_idx"),
        ]
        constraints = [
            # Zgłoszenie musi mieć **jakiś** adres zwrotny: albo konto, albo wpisany e-mail.
            # Bez tego powstałaby sprawa, na którą nie da się odpowiedzieć – a odpowiedź jest
            # jedynym powodem, dla którego ten model istnieje.
            models.CheckConstraint(
                condition=models.Q(user__isnull=False) | ~models.Q(email=""),
                name="support_ticket_has_reply_address",
            ),
        ]

    def __str__(self) -> str:
        return f"#{self.pk} {self.subject}"

    @property
    def is_open(self) -> bool:
        return self.status == TicketStatus.OPEN

    @property
    def is_closed(self) -> bool:
        return self.status == TicketStatus.CLOSED

    @property
    def reply_to(self) -> str:
        """Adres, na który idzie powiadomienie o odpowiedzi. Pusty = nie ma dokąd pisać.

        Konto wygrywa z polem ``email``: to jego adres jest loginem i to on jest aktualny także
        wtedy, gdy zgłoszenie leży od tygodnia. Pole ``email`` obsługuje wyłącznie zgłoszenia
        bez konta.
        """
        if self.user_id is not None and self.user.email:
            return self.user.email
        return self.email


class SupportMessage(models.Model):
    """Jedna wypowiedź w wątku sprawy.

    ``author`` nullowalny znaczy „wiadomość systemowa albo autor, którego konta już nie ma”;
    ``from_coordinator`` mówi, po której stronie stoi wypowiedź. Dwa pola zamiast jednego, bo to
    dwa różne pytania: „kto to napisał” (bywa bez odpowiedzi) i „czy to odpowiedź organizatora”
    (musi mieć odpowiedź zawsze – od niej zależy układ wątku i to, czy poszło powiadomienie).
    """

    ticket = models.ForeignKey(
        SupportTicket, on_delete=models.CASCADE, related_name="messages", verbose_name="zgłoszenie"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_messages",
        verbose_name="autor",
    )
    from_coordinator = models.BooleanField("odpowiedź organizatora", default=False)
    body = models.TextField("treść", max_length=MAX_BODY_LENGTH)
    created_at = models.DateTimeField("dodana", default=timezone.now)

    class Meta:
        verbose_name = "wiadomość zgłoszenia"
        verbose_name_plural = "wiadomości zgłoszeń"
        ordering = ("created_at", "id")

    def __str__(self) -> str:
        side = "organizator" if self.from_coordinator else "zgłaszający"
        return f"{side}: {self.body[:40]}"
