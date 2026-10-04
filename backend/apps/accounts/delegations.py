"""Delegacje krajowe: kraj, jego opiekunowie drużyny i zaproszenia (docs/tasks/DEL-01.md § 2).

Skąd to się wzięło. W olimpiadzie międzynarodowej (``iqo``) uczeń nie rejestruje się sam: kraj
wystawia **drużynę**, a zgłasza ją opiekun (team leader) wskazany przez krajowego organizatora.
Koordynator zaprasza opiekuna adresem e-mail, opiekun zakłada konto z zaproszenia i rejestruje
uczniów swojego kraju. Jeden kraj bywa prowadzony przez kilku opiekunów naraz (lider i zastępca,
dwóch nauczycieli z dwóch miast) – dlatego delegacja jest **osobnym wierszem**, a nie atrybutem
opiekuna: uczniowie należą do kraju, nie do tego opiekuna, który akurat kliknął „Dodaj ucznia”.

Trzy modele i ich granice:

- ``Delegation`` – kraj w edycji: limit uczniów, stan (otwarta/zamknięta), notatka koordynatora.
  Jedna na parę (edycja, kraj) – dwie delegacje Niemiec w jednej edycji znaczyłyby dwa liczniki
  limitu i drużynę, która może być dwa razy większa, niż pozwala regulamin,
- ``DelegationLeader`` – „ta osoba prowadzi tę delegację”: wielu na delegację, ale **jedna
  delegacja na osobę w edycji** (więz bazy, nie tylko walidacja – drugi kraj dla tego samego konta
  to pomyłka koordynatora albo próba zobaczenia cudzych uczniów),
- ``DelegationInvitation`` – zaproszenie opiekuna: adres, skrót tokenu, ważność, przyjęcie,
  unieważnienie. Token **nigdy** nie leży w bazie jawnie – tak samo jak kod zaproszenia do komitetu
  (``hash_invitation_code``).

Moduł zawiera wyłącznie modele i reguły, które czyta się razem z nimi (stan zaproszenia, liczenie
miejsc). Czynności – zaproszenie, przyjęcie, dodanie ucznia – są w ``apps.accounts.delegation_services``.
Importuje go ``apps.accounts.models`` na końcu pliku (rejestracja modeli w Django), więc tutaj
modele innych plików wskazujemy **nazwami** („accounts.Region”), a nie importem.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.tenancy.managers import CompetitionScopedManager, CompetitionScopedQuerySet

#: Ważność zaproszenia opiekuna. Czternaście dni – tyle samo, co zaproszenie ucznia z importu
#: i prośba o zgodę opiekuna prawnego: list idzie do krajowego organizatora, który czyta pocztę
#: między lekcjami, a wygaśnięcie niczego nie zwalnia (koordynator wyśle ponownie jednym klikiem).
INVITATION_DAYS = 14


class DelegationStatus(models.TextChoices):
    """Stan delegacji. ``CLOSED`` zamraża listę uczniów – opiekun dalej ją widzi, ale nie zmienia."""

    ACTIVE = "ACTIVE", "otwarta"
    CLOSED = "CLOSED", "zamknięta"


class DelegationInvitationStatus(models.TextChoices):
    """Stan zaproszenia **wyliczany** z pól – ta sama decyzja, co przy ``InvitationStatus``.

    Kolumna statusu musiałaby być przestawiana przy każdej drodze zapisu i nie zauważyłaby upływu
    czasu („wygasłe” powstaje samo, o północy czternastego dnia). Wyliczenie z faktów
    (``accepted_at``, ``revoked_at``, ``expires_at``) nie ma jak się z nimi rozjechać.
    """

    PENDING = "PENDING", "wysłane"
    ACCEPTED = "ACCEPTED", "przyjęte"
    REVOKED = "REVOKED", "cofnięte"
    EXPIRED = "EXPIRED", "wygasłe"


class Delegation(models.Model):
    """Drużyna jednego kraju w jednej edycji konkursu."""

    #: ``CASCADE`` po stronie konkursu i edycji, bo delegacja jest **organizacją zawodów**, a nie
    #: cudzymi danymi; cudze dane (profile uczniów) stoją na ``Participant.delegation`` z ``PROTECT``
    #: i to one zatrzymają kasowanie konkursu z zarejestrowanymi uczniami.
    competition = models.ForeignKey(
        "tenancy.Competition",
        verbose_name="konkurs",
        on_delete=models.CASCADE,
        related_name="delegations",
    )
    edition = models.ForeignKey(
        "competitions.Edition",
        verbose_name="edycja",
        on_delete=models.CASCADE,
        related_name="delegations",
    )
    #: Region konkursu na poziomie ``COUNTRY`` (REG-01). ``PROTECT`` – kraj z delegacją wycofuje się
    #: odznaczeniem „aktywny”, a nie skasowaniem wiersza.
    country = models.ForeignKey(
        "accounts.Region",
        verbose_name="kraj",
        on_delete=models.PROTECT,
        related_name="delegations",
    )
    max_students = models.PositiveSmallIntegerField("limit uczniów")
    status = models.CharField(
        "stan", max_length=16, choices=DelegationStatus.choices, default=DelegationStatus.ACTIVE
    )
    #: Notatka **koordynatora** – opiekun jej nie widzi. Do spraw organizacyjnych („płatność
    #: przelewem”, „lot 12.07”), nie do danych osobowych uczniów.
    note = models.TextField("notatka koordynatora", blank=True, max_length=2000)
    created_at = models.DateTimeField("utworzona", default=timezone.now)

    objects = CompetitionScopedManager()

    class Meta:
        verbose_name = "delegacja"
        verbose_name_plural = "delegacje"
        ordering = ("country__name", "id")
        constraints = [
            models.UniqueConstraint(fields=["edition", "country"], name="accounts_delegation_unique_country"),
        ]

    def __str__(self) -> str:
        return f"{self.country} ({self.edition_id})"

    def clean(self) -> None:
        """Kraj i edycja należą do konkursu delegacji, a kraj jest krajem – nie województwem.

        Reguła stoi w modelu, a nie tylko w serwisie, bo do delegacji prowadzi też ``/admin/``.
        """
        super().clean()
        from apps.accounts.models import RegionLevel

        errors: dict[str, str] = {}
        if self.country_id and self.competition_id:
            if self.country.competition_id != self.competition_id:
                errors["country"] = "Kraj należy do innego konkursu."
            elif self.country.level != RegionLevel.COUNTRY:
                errors["country"] = "Delegację zakłada się dla regionu na poziomie „kraj”."
        if self.edition_id and self.competition_id and self.edition.competition_id != self.competition_id:
            errors["edition"] = "Edycja należy do innego konkursu."
        if errors:
            raise ValidationError(errors)

    @property
    def is_open(self) -> bool:
        return self.status == DelegationStatus.ACTIVE


class DelegationLeaderQuerySet(CompetitionScopedQuerySet):
    """Opiekunowie – do konkursu przez delegację."""

    competition_path = "delegation__competition"

    def active(self):
        """Opiekunowie **nieodwołani** – jedyni, którzy cokolwiek widzą i liczą się do więzów."""
        return self.filter(removed_at__isnull=True)


class DelegationLeader(models.Model):
    """„Ta osoba prowadzi tę delegację” – źródło uprawnienia opiekuna do uczniów kraju.

    Rola ``team_leader`` (``Membership``) mówi, że ktoś **jest** opiekunem w konkursie; ten wiersz –
    **którego kraju**. Panel opiekuna czyta wyłącznie ten wiersz: rola bez wiersza (opiekun
    odwołany z delegacji) nie otwiera żadnej listy uczniów.
    """

    delegation = models.ForeignKey(Delegation, on_delete=models.CASCADE, related_name="leaders")
    #: Kopia ``delegation.edition`` – wyłącznie po to, żeby więz „jedna delegacja na osobę
    #: w edycji” był więzem bazy. Walidacja w serwisie przy dwóch równoległych przyjęciach
    #: zaproszeń (dwa kraje, jedno konto) przepuściłaby oba.
    edition = models.ForeignKey(
        "competitions.Edition", on_delete=models.CASCADE, related_name="delegation_leaders"
    )
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="delegation_leaderships")
    invited_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="zaprosił",
    )
    accepted_at = models.DateTimeField("przyjął zaproszenie", default=timezone.now)
    #: Odwołanie z delegacji jest znacznikiem, a nie skasowaniem wiersza (poprawka po przeglądzie):
    #: na wierszu wiszą dowody zgód opiekuna (``ConsentRecord.team_leader``), a konto po odwołaniu
    #: istnieje dalej – kaskada zabrałaby dowód podstawy przetwarzania danych, które wciąż mamy.
    #: Wiersz znika dopiero z kontem (``delegation_services.erase_for_user``).
    removed_at = models.DateTimeField("odwołany", null=True, blank=True)

    objects = models.Manager.from_queryset(DelegationLeaderQuerySet)()

    class Meta:
        verbose_name = "opiekun drużyny"
        verbose_name_plural = "opiekunowie drużyn"
        ordering = ("accepted_at", "id")
        constraints = [
            # Jedna **czynna** delegacja na osobę w edycji; odwołany wiersz (``removed_at``) nie blokuje
            # zaproszenia tej osoby do innego kraju.
            models.UniqueConstraint(
                fields=["user", "edition"],
                condition=models.Q(removed_at__isnull=True),
                name="accounts_delegation_leader_one_active_per_edition",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} → {self.delegation_id}"

    def save(self, *args, **kwargs):
        # Kopia edycji ustawiana zawsze z delegacji – rozjazd tych dwóch kolumn wyłączyłby więz.
        if self.delegation_id and not self.edition_id:
            self.edition_id = self.delegation.edition_id
        super().save(*args, **kwargs)


class DelegationInvitationQuerySet(CompetitionScopedQuerySet):
    """Zaproszenia – do konkursu przez delegację."""

    competition_path = "delegation__competition"


class DelegationInvitation(models.Model):
    """Zaproszenie opiekuna drużyny wysłane przez koordynatora. W bazie wyłącznie skrót tokenu."""

    delegation = models.ForeignKey(Delegation, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField("adres opiekuna")
    #: sha256 tokenu (``hash_invitation_code``). Unikalny, bo po nim szuka się zaproszenia z linku.
    token_hash = models.CharField("sha256 tokenu", max_length=64, unique=True, editable=False)
    #: ``SET_NULL``, a nie ``PROTECT`` jak przy ``InvitationCode``: usunięcie konta koordynatora nie
    #: może być zablokowane tym, że kiedyś kogoś zaprosił – autora zdarzenia zna i tak audyt.
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField("utworzone", default=timezone.now)
    #: Kiedy ostatnio poszedł list. Ponowne wysłanie **zmienia token** (stary link przestaje
    #: działać) i przesuwa ważność – patrz ``delegation_services.resend_leader_invitation``.
    sent_at = models.DateTimeField("wysłane", null=True, blank=True)
    expires_at = models.DateTimeField("wygasa")
    accepted_at = models.DateTimeField("przyjęte", null=True, blank=True)
    accepted_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    revoked_at = models.DateTimeField("cofnięte", null=True, blank=True)

    objects = models.Manager.from_queryset(DelegationInvitationQuerySet)()

    class Meta:
        verbose_name = "zaproszenie opiekuna drużyny"
        verbose_name_plural = "zaproszenia opiekunów drużyn"
        ordering = ("-created_at", "-id")
        constraints = [
            # Jedno **otwarte** zaproszenie na adres w delegacji: drugie kliknięcie „Zaproś” pod ten
            # sam adres ma odświeżyć pierwsze, a nie dołożyć drugi działający link.
            models.UniqueConstraint(
                fields=["delegation", "email"],
                condition=models.Q(accepted_at__isnull=True, revoked_at__isnull=True),
                name="accounts_delegation_invitation_one_open",
            ),
        ]

    def __str__(self) -> str:
        return f"zaproszenie {self.token_hash[:8]}… ({self.delegation_id})"

    def status(self, now=None) -> str:
        """Przyjęte → cofnięte → wygasłe → wysłane: fakt dokonany wygrywa z decyzją, decyzja z czasem."""
        if self.accepted_at is not None:
            return DelegationInvitationStatus.ACCEPTED
        if self.revoked_at is not None:
            return DelegationInvitationStatus.REVOKED
        if self.expires_at <= (now or timezone.now()):
            return DelegationInvitationStatus.EXPIRED
        return DelegationInvitationStatus.PENDING

    @property
    def status_label(self) -> str:
        return DelegationInvitationStatus(self.status()).label

    def is_usable(self, now=None) -> bool:
        """Jedyne miejsce reguły „ten link jeszcze działa” – czyta ją przyjęcie i ekran zaproszenia."""
        return self.status(now) == DelegationInvitationStatus.PENDING


def invitation_expiry(now=None):
    """Termin ważności zaproszenia wysłanego teraz."""
    return (now or timezone.now()) + timedelta(days=INVITATION_DAYS)
