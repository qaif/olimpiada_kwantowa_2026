"""Płatności online za udział: cennik delegacji, zamówienia, wpłaty, zwroty i dokumenty (PAY-01).

Specyfikacja: ``docs/tasks/PAY-01.md``. Cały obszar stoi za flagą ``fees`` – tą samą, co rejestr
wpisowego uczestnika (``apps.tenancy.fees``). Konkurs bez flagi nie ma tu ani jednego wiersza
i nie wykonuje ani jednego zapytania: każdy odczyt zaczyna się od :func:`enabled`.

**Dlaczego osobna aplikacja, a nie ``apps.tenancy.fees``.** Tamten moduł jest **rejestrem**
należności uczestnika („ile, czy wpłynęło, kiedy”) i świadomie nie zna żadnego dostawcy (D15, D18).
Ten jest jego **kasą**: zamówienie z pozycjami, próba zapłaty u dostawcy, zwrot przez API, numerowany
dokument. Należność uczestnika dalej żyje w ``ParticipantFee`` – zapłacone tutaj zamówienie kończy
się wywołaniem ``record_payment`` tamtego modułu, więc rejestr wpisowego i jego ekrany widzą wpłatę
online tak samo jak wpisaną ręcznie.

**Pieniądze są ``Decimal`` z dwoma miejscami, waluta jest kolumną** – ta sama reguła, co w rejestrze
wpisowego. Kwoty w zamówieniu są **migawką** z chwili wystawienia (cena, okres, skład), a nie
odczytem cennika: zmiana ceny w połowie sezonu nie może zmienić pro formy, którą ktoś już dostał.

**Kwota płatności nigdy nie przychodzi od klienta.** ``Payment.amount`` jest kopią ``Order.total``
w chwili utworzenia próby, a ``Order.total`` liczy serwer z pozycji. Webhook dostawcy jest
porównywany z tą kopią – rozbieżność nie zamyka zamówienia, tylko oznacza płatność do wyjaśnienia.
"""

from __future__ import annotations

import secrets
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.competitions.scoping import competition_scoped_manager
from apps.tenancy.fees import validate_currency

#: Flaga konkursu (``apps.tenancy.models.FEATURE_DEFAULTS``) – ta sama, co rejestr wpisowego.
FLAG = "fees"

#: Zero w kolumnie kwot – jedna stała, żeby ``Decimal("0")`` i ``Decimal("0.00")`` nie mieszały się
#: w porównaniach i sumach.
ZERO = Decimal("0.00")

#: Najwięcej obserwatorów, których opiekun może zadeklarować. Sito na literówki („50” zamiast „5”),
#: a nie reguła regulaminu – organizator i tak widzi deklarację i może ją skorygować zniżką.
MAX_OBSERVERS = 20

#: Alfabet kodu referencyjnego: bez 0/O, 1/I/L – kod przepisuje się ręcznie do tytułu przelewu.
REFERENCE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
REFERENCE_LENGTH = 8


def enabled(competition) -> bool:
    """Czy konkurs pobiera opłaty. Jedno miejsce odczytu flagi w tej aplikacji."""
    return competition is not None and competition.has_feature(FLAG)


def new_reference(competition) -> str:
    """Kod do tytułu przelewu: ``<PREFIKS>-<8 znaków>``, losowany, unikalny w bazie.

    Losowy, a nie kolejny: kolejny numer zdradzałby liczbę zamówień i dawał się zgadnąć, a kod jest
    jedynym kluczem, po którym koordynator dopasowuje przelew z wyciągu.
    """
    prefix = (competition.slug or "PAY").upper().replace("-", "")[:8]
    body = "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(REFERENCE_LENGTH))
    return f"{prefix}-{body}"


# --- ustawienia konkursu --------------------------------------------------------------------------


class PaymentSettings(models.Model):
    """Dane sprzedawcy i metody płatności konkursu. Brak wiersza = wartości domyślne.

    Nazwa, adres i dane rejestrowe sprzedawcy są w ``Competition.organizer_*`` i stamtąd trafiają na
    dokument – tu stoi wyłącznie to, czego konkurs nie miał: NIP/VAT ID, rachunek do przelewu,
    prefiks numeracji i adnotacja VAT. Osobny model, a nie pola konkursu, bo dotyczą wyłącznie
    konkursów z flagą ``fees`` (Konkurs #1 nie ma tu wiersza).
    """

    competition = models.OneToOneField(
        "tenancy.Competition",
        on_delete=models.CASCADE,
        related_name="payment_settings",
        verbose_name="konkurs",
    )
    seller_tax_id = models.CharField("NIP / VAT ID sprzedawcy", max_length=32, blank=True)
    bank_account = models.CharField("rachunek bankowy (IBAN)", max_length=64, blank=True)
    bank_swift = models.CharField("SWIFT/BIC", max_length=11, blank=True)
    bank_name = models.CharField("bank", max_length=120, blank=True)
    document_prefix = models.CharField(
        "prefiks numeracji dokumentów",
        max_length=12,
        blank=True,
        help_text="Puste = identyfikator konkursu wielkimi literami, np. IQO/FV/2026/0001.",
    )
    vat_note = models.CharField(
        "adnotacja VAT",
        max_length=300,
        blank=True,
        help_text="Np. podstawa zwolnienia albo „odwrotne obciążenie”. System nie liczy podatku.",
    )
    invoice_note = models.TextField("uwagi na dokumentach", max_length=1000, blank=True)
    proforma_due_days = models.PositiveSmallIntegerField(
        "termin płatności pro formy (dni)", default=14, validators=[MaxValueValidator(365)]
    )
    card_payments = models.BooleanField("płatność kartą (Stripe)", default=True)
    p24_payments = models.BooleanField("Przelewy24 (tylko PLN)", default=True)
    bank_transfer = models.BooleanField("przelew tradycyjny", default=True)
    updated_at = models.DateTimeField("zmieniono", auto_now=True)

    objects = competition_scoped_manager()

    class Meta:
        verbose_name = "ustawienia płatności"
        verbose_name_plural = "ustawienia płatności"

    def __str__(self) -> str:
        return f"Płatności: {self.competition_id}"

    @property
    def prefix(self) -> str:
        return (self.document_prefix or self.competition.slug or "DOC").upper()


# --- cennik delegacji -----------------------------------------------------------------------------


class PriceKind(models.TextChoices):
    """Pozycja cennika delegacji. ``PARTICIPANT`` i ``DISCOUNT`` występują wyłącznie w zamówieniu."""

    DELEGATION = "DELEGATION", _("Opłata za delegację")
    STUDENT = "STUDENT", _("Uczeń")
    LEADER = "LEADER", _("Opiekun drużyny")
    OBSERVER = "OBSERVER", _("Obserwator")
    PARTICIPANT = "PARTICIPANT", _("Opłata za udział")
    DISCOUNT = "DISCOUNT", _("Zniżka")


#: Rodzaje wyceniane w cenniku delegacji – kolejność jest kolejnością wierszy na ekranie i pozycji
#: na dokumencie.
DELEGATION_KINDS: tuple[str, ...] = (
    PriceKind.DELEGATION,
    PriceKind.STUDENT,
    PriceKind.LEADER,
    PriceKind.OBSERVER,
)


class PricePeriod(models.TextChoices):
    EARLY = "EARLY", _("cena wczesna")
    REGULAR = "REGULAR", _("cena podstawowa")
    LATE = "LATE", _("cena późna")


class PriceList(models.Model):
    """Cennik delegacji jednej edycji: waluta i dwa terminy dzielące rok na trzy okresy.

    Jeden cennik na edycję (więz), bo „ile kosztuje drużyna w tym roku” ma jedną odpowiedź. Ceny
    stoją w :class:`PriceItem` (rodzaj × okres), a nie w dwunastu kolumnach: brak ceny okresu
    znaczy „jak cena podstawowa” i taki brak da się zapisać tylko brakiem wiersza.
    """

    competition = models.ForeignKey(
        "tenancy.Competition", on_delete=models.PROTECT, related_name="price_lists", verbose_name="konkurs"
    )
    edition = models.OneToOneField(
        "competitions.Edition", on_delete=models.CASCADE, related_name="price_list", verbose_name="edycja"
    )
    currency = models.CharField("waluta", max_length=3, default="EUR", validators=[validate_currency])
    early_until = models.DateField("cena wczesna do (włącznie)", null=True, blank=True)
    late_from = models.DateField("cena późna od", null=True, blank=True)
    is_active = models.BooleanField("aktywny", default=True)
    created_at = models.DateTimeField("utworzony", default=timezone.now)

    objects = competition_scoped_manager()

    class Meta:
        verbose_name = "cennik delegacji"
        verbose_name_plural = "cenniki delegacji"
        constraints = [
            models.CheckConstraint(
                condition=Q(early_until__isnull=True)
                | Q(late_from__isnull=True)
                | Q(early_until__lt=models.F("late_from")),
                name="payments_pricelist_early_before_late",
            ),
        ]

    def __str__(self) -> str:
        return f"Cennik {self.edition_id} ({self.currency})"

    def clean(self) -> None:
        super().clean()
        if self.edition_id and self.competition_id and self.edition.competition_id != self.competition_id:
            raise ValidationError({"edition": "Edycja należy do innego konkursu niż cennik."})
        if self.early_until and self.late_from and self.early_until >= self.late_from:
            raise ValidationError({"late_from": "Cena późna musi zaczynać się po terminie ceny wczesnej."})

    def period_on(self, day) -> str:
        """Okres cenowy dnia ``day``: ≤ ``early_until`` – wczesny, ≥ ``late_from`` – późny."""
        if self.early_until and day <= self.early_until:
            return PricePeriod.EARLY
        if self.late_from and day >= self.late_from:
            return PricePeriod.LATE
        return PricePeriod.REGULAR


class PriceItem(models.Model):
    price_list = models.ForeignKey(PriceList, on_delete=models.CASCADE, related_name="items")
    kind = models.CharField("pozycja", max_length=16, choices=PriceKind.choices)
    period = models.CharField("okres", max_length=8, choices=PricePeriod.choices)
    amount = models.DecimalField("kwota", max_digits=10, decimal_places=2)

    objects = competition_scoped_manager("price_list__competition")

    class Meta:
        verbose_name = "cena"
        verbose_name_plural = "ceny"
        constraints = [
            models.UniqueConstraint(
                fields=["price_list", "kind", "period"], name="payments_priceitem_unique"
            ),
            models.CheckConstraint(condition=Q(amount__gte=0), name="payments_priceitem_amount_not_negative"),
        ]

    def __str__(self) -> str:
        return f"{self.kind}/{self.period}: {self.amount}"


# --- nabywca --------------------------------------------------------------------------------------


class BuyerType(models.TextChoices):
    INSTITUTION = "INSTITUTION", _("Instytucja")
    PERSON = "PERSON", _("Osoba prywatna")


class BillingProfile(models.Model):
    """Dane nabywcy na fakturę, wpisane przez płacącego. Delegacja **albo** uczestnik.

    Profil jest wzorem, a nie dokumentem: zamówienie robi z niego migawkę w chwili wystawienia, więc
    poprawka adresu po zapłacie nie zmienia faktury, którą ktoś już zaksięgował.
    """

    delegation = models.OneToOneField(
        "accounts.Delegation",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="billing_profile",
        verbose_name="delegacja",
    )
    participant = models.OneToOneField(
        "accounts.Participant",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="billing_profile",
        verbose_name="uczestnik",
    )
    buyer_type = models.CharField(
        "nabywca", max_length=12, choices=BuyerType.choices, default=BuyerType.INSTITUTION
    )
    buyer_name = models.CharField("nazwa nabywcy", max_length=200)
    buyer_address = models.TextField("adres", max_length=500)
    buyer_country = models.CharField("kraj", max_length=100)
    buyer_vat_id = models.CharField("NIP / VAT ID", max_length=32, blank=True)
    buyer_email = models.EmailField("e-mail do rozliczeń")
    #: Deklaracja opiekuna – obserwatorzy nie mają kont, więc ich liczba nie wynika z bazy.
    observers = models.PositiveSmallIntegerField(
        "obserwatorzy", default=0, validators=[MaxValueValidator(MAX_OBSERVERS)]
    )
    updated_at = models.DateTimeField("zmieniono", auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "dane nabywcy"
        verbose_name_plural = "dane nabywców"
        constraints = [
            models.CheckConstraint(
                condition=(Q(delegation__isnull=False) & Q(participant__isnull=True))
                | (Q(delegation__isnull=True) & Q(participant__isnull=False)),
                name="payments_billingprofile_one_owner",
            ),
        ]

    def __str__(self) -> str:
        return self.buyer_name


# --- zniżki i zwolnienia --------------------------------------------------------------------------


class AdjustmentKind(models.TextChoices):
    DISCOUNT = "DISCOUNT", _("Zniżka")
    WAIVER = "WAIVER", _("Zwolnienie z opłat")


class FeeAdjustment(models.Model):
    """Decyzja koordynatora o zniżce albo zwolnieniu delegacji. Powód obowiązkowy, cofnięcie – też.

    Cofnięcie jest polem, a nie skasowaniem wiersza: decyzja finansowa i jej odwołanie są dwoma
    faktami w historii delegacji, a nie korektą literówki.
    """

    competition = models.ForeignKey(
        "tenancy.Competition", on_delete=models.PROTECT, related_name="fee_adjustments"
    )
    delegation = models.ForeignKey(
        "accounts.Delegation", on_delete=models.PROTECT, related_name="fee_adjustments"
    )
    kind = models.CharField("rodzaj", max_length=10, choices=AdjustmentKind.choices)
    amount = models.DecimalField("kwota zniżki", max_digits=10, decimal_places=2, null=True, blank=True)
    reason = models.CharField("uzasadnienie", max_length=300)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    created_at = models.DateTimeField("utworzono", default=timezone.now)
    revoked_at = models.DateTimeField("cofnięto", null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    revoke_reason = models.CharField("powód cofnięcia", max_length=300, blank=True)

    objects = competition_scoped_manager()

    class Meta:
        verbose_name = "zniżka / zwolnienie"
        verbose_name_plural = "zniżki i zwolnienia"
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=Q(kind="WAIVER") | Q(amount__gt=0), name="payments_adjustment_discount_positive"
            ),
            models.CheckConstraint(condition=~Q(reason=""), name="payments_adjustment_has_reason"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} ({self.delegation_id})"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


# --- zamówienie -----------------------------------------------------------------------------------


class OrderStatus(models.TextChoices):
    OPEN = "OPEN", _("czeka na wpłatę")
    PAID = "PAID", _("zapłacone")
    CANCELLED = "CANCELLED", _("anulowane")
    REFUNDED = "REFUNDED", _("zwrócone")


#: Zamówienia, które **pokrywają** skład delegacji – tych pozycji nie wystawia się drugi raz.
#: ``REFUNDED`` też: pokrycie liczy ilości pozycji **minus ilości zwrócone** (:class:`RefundLine`),
#: więc zamówienie zwrócone w całości nie pokrywa już niczego, a zwrócone w części – resztę.
COVERING_STATUSES: tuple[str, ...] = (OrderStatus.OPEN, OrderStatus.PAID, OrderStatus.REFUNDED)


class Order(models.Model):
    """Przedmiot płatności: delegacja **albo** należność uczestnika. Niezmienne po wystawieniu.

    Pozycje i suma powstają raz, razem z numerem pro formy. Zmiana składu przed zapłatą to
    anulowanie i nowe zamówienie – a nie poprawka, bo pro forma z numerem mogła już trafić do
    księgowości płacącego.
    """

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    competition = models.ForeignKey(
        "tenancy.Competition", on_delete=models.PROTECT, related_name="payment_orders"
    )
    edition = models.ForeignKey("competitions.Edition", on_delete=models.PROTECT, related_name="+")
    delegation = models.ForeignKey(
        "accounts.Delegation",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payment_orders",
    )
    participant_fee = models.ForeignKey(
        "tenancy.ParticipantFee",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payment_orders",
    )
    reference = models.CharField("kod referencyjny", max_length=24, unique=True)
    currency = models.CharField("waluta", max_length=3, validators=[validate_currency])
    total = models.DecimalField("suma", max_digits=10, decimal_places=2)
    refunded_amount = models.DecimalField("zwrócono", max_digits=10, decimal_places=2, default=ZERO)
    period = models.CharField("okres cenowy", max_length=8, choices=PricePeriod.choices, blank=True)
    status = models.CharField("stan", max_length=10, choices=OrderStatus.choices, default=OrderStatus.OPEN)
    # --- migawka nabywcy ---
    buyer_type = models.CharField(max_length=12, choices=BuyerType.choices)
    buyer_name = models.CharField(max_length=200)
    buyer_address = models.TextField(max_length=500, blank=True)
    buyer_country = models.CharField(max_length=100, blank=True)
    buyer_vat_id = models.CharField(max_length=32, blank=True)
    buyer_email = models.EmailField()
    #: Język płacącego z chwili wystawienia – w nim powstają dokumenty i listy do niego.
    language = models.CharField("język", max_length=8, default="en")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="payment_orders"
    )
    created_at = models.DateTimeField("wystawiono", default=timezone.now)
    due_on = models.DateField("termin płatności", null=True, blank=True)
    paid_at = models.DateTimeField("zapłacono", null=True, blank=True)
    cancelled_at = models.DateTimeField("anulowano", null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    cancel_reason = models.CharField("powód anulowania", max_length=300, blank=True)

    objects = competition_scoped_manager()

    class Meta:
        verbose_name = "zamówienie"
        verbose_name_plural = "zamówienia"
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=(Q(delegation__isnull=False) & Q(participant_fee__isnull=True))
                | (Q(delegation__isnull=True) & Q(participant_fee__isnull=False)),
                name="payments_order_one_payer",
            ),
            models.CheckConstraint(condition=Q(total__gt=0), name="payments_order_total_positive"),
            models.CheckConstraint(
                condition=Q(refunded_amount__gte=0) & Q(refunded_amount__lte=models.F("total")),
                name="payments_order_refund_within_total",
            ),
            models.CheckConstraint(
                condition=~Q(status="PAID") | Q(paid_at__isnull=False), name="payments_order_paid_has_date"
            ),
            # Jedno otwarte zamówienie na należność uczestnika: druga pro forma na ten sam start
            # znaczyłaby dwa przelewy za jedno wpisowe.
            models.UniqueConstraint(
                fields=["participant_fee"],
                condition=Q(status="OPEN"),
                name="payments_order_one_open_per_fee",
            ),
        ]
        indexes = [models.Index(fields=["competition", "edition", "status"], name="payments_order_scope_idx")]

    def __str__(self) -> str:
        return self.reference

    @property
    def is_open(self) -> bool:
        return self.status == OrderStatus.OPEN

    @property
    def amount_left_to_refund(self) -> Decimal:
        return self.total - self.refunded_amount


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    position = models.PositiveSmallIntegerField(default=0)
    kind = models.CharField(max_length=16, choices=PriceKind.choices)
    description = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ("order", "position", "id")

    def __str__(self) -> str:
        return self.description


# --- wpłata ---------------------------------------------------------------------------------------


class Provider(models.TextChoices):
    STRIPE = "stripe", _("Karta płatnicza (Stripe)")
    P24 = "przelewy24", "Przelewy24"
    BANK_TRANSFER = "bank_transfer", _("Przelew bankowy")


class PaymentStatus(models.TextChoices):
    PENDING = "PENDING", _("w toku")
    SUCCEEDED = "SUCCEEDED", _("przyjęta")
    FAILED = "FAILED", _("nieudana")
    CANCELLED = "CANCELLED", _("przerwana")
    #: Dostawca potwierdził inną kwotę albo walutę niż zamówienie – sprawa dla człowieka.
    MISMATCH = "MISMATCH", _("do wyjaśnienia")


class ScanStatus(models.TextChoices):
    """Wynik skanu dowodu wpłaty – te same wartości, co przy zaświadczeniach i pracach."""

    PENDING = "PENDING", "oczekuje na skan"
    CLEAN = "CLEAN", "czysty"
    INFECTED = "INFECTED", "zainfekowany"
    ERROR = "ERROR", "błąd skanu"


class Payment(models.Model):
    """Jedna próba zapłaty zamówienia. **Danych karty tu nie ma i nie będzie** – są u dostawcy."""

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="payments")
    provider = models.CharField("dostawca", max_length=16, choices=Provider.choices)
    status = models.CharField(
        "stan", max_length=10, choices=PaymentStatus.choices, default=PaymentStatus.PENDING
    )
    amount = models.DecimalField("kwota", max_digits=10, decimal_places=2)
    currency = models.CharField("waluta", max_length=3)
    #: Identyfikator sesji u dostawcy (Stripe ``cs_…``, P24 ``sessionId``) – po nim webhook odnajduje
    #: płatność. Unikalny per dostawca (więz niżej).
    provider_ref = models.CharField("identyfikator sesji", max_length=255, blank=True)
    #: Identyfikator transakcji po zapłacie (Stripe ``pi_…``, P24 ``orderId``) – potrzebny do zwrotu.
    provider_payment_id = models.CharField("identyfikator transakcji", max_length=255, blank=True)
    refunded_amount = models.DecimalField("zwrócono", max_digits=10, decimal_places=2, default=ZERO)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField("utworzono", default=timezone.now)
    succeeded_at = models.DateTimeField("przyjęto", null=True, blank=True)
    closed_at = models.DateTimeField("zamknięto", null=True, blank=True)
    #: Opis rozbieżności albo błędu – dla koordynatora, bez danych płacącego.
    detail = models.CharField("szczegóły", max_length=300, blank=True)
    # --- przelew tradycyjny (zapis koordynatora) ---
    received_on = models.DateField("data wpływu", null=True, blank=True)
    note = models.CharField("notatka", max_length=300, blank=True)
    proof_key = models.CharField("dowód wpłaty (klucz)", max_length=300, blank=True)
    proof_mime = models.CharField(max_length=40, blank=True)
    proof_size = models.PositiveIntegerField(default=0)
    proof_sha256 = models.CharField(max_length=64, blank=True)
    proof_scan_status = models.CharField(max_length=10, choices=ScanStatus.choices, blank=True)

    objects = competition_scoped_manager("order__competition")

    class Meta:
        verbose_name = "wpłata"
        verbose_name_plural = "wpłaty"
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_ref"],
                condition=~Q(provider_ref=""),
                name="payments_payment_unique_provider_ref",
            ),
            models.CheckConstraint(condition=Q(amount__gt=0), name="payments_payment_amount_positive"),
            models.CheckConstraint(
                condition=Q(refunded_amount__gte=0) & Q(refunded_amount__lte=models.F("amount")),
                name="payments_payment_refund_within_amount",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider}:{self.uuid}"

    @property
    def refundable(self) -> Decimal:
        return self.amount - self.refunded_amount if self.status == PaymentStatus.SUCCEEDED else ZERO


class RefundStatus(models.TextChoices):
    PENDING = "PENDING", _("w toku")
    SUCCEEDED = "SUCCEEDED", _("wykonany")
    FAILED = "FAILED", _("nieudany")


class Refund(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="refunds")
    amount = models.DecimalField("kwota", max_digits=10, decimal_places=2)
    reason = models.CharField("powód", max_length=300)
    status = models.CharField(
        "stan", max_length=10, choices=RefundStatus.choices, default=RefundStatus.PENDING
    )
    provider_refund_id = models.CharField("identyfikator zwrotu", max_length=255, blank=True)
    detail = models.CharField("szczegóły", max_length=300, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    created_at = models.DateTimeField("zlecono", default=timezone.now)
    completed_at = models.DateTimeField("zakończono", null=True, blank=True)

    objects = competition_scoped_manager("payment__order__competition")

    class Meta:
        verbose_name = "zwrot"
        verbose_name_plural = "zwroty"
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="payments_refund_amount_positive"),
            models.CheckConstraint(condition=~Q(reason=""), name="payments_refund_has_reason"),
        ]

    def __str__(self) -> str:
        return f"refund:{self.uuid}"


class RefundLine(models.Model):
    """Która pozycja zamówienia i w jakiej ilości wraca w tym zwrocie.

    Zwrot jest przypisany do **pozycji**, a nie tylko do kwoty: inaczej uczeń wypisany po zapłacie
    i zwrócony zostawałby „pokryty”, a jego zastępca nie zapłaciłby niczego (``pricing.covered``
    odejmuje ilości zwrotów zakończonych). Zwrot wpłaty ``MISMATCH`` (podwójnej albo z rozbieżną
    kwotą) pozycji nie ma – nie pokrywał niczego.
    """

    refund = models.ForeignKey(Refund, on_delete=models.CASCADE, related_name="lines")
    line = models.ForeignKey(OrderLine, on_delete=models.PROTECT, related_name="refund_lines")
    quantity = models.PositiveIntegerField("ilość")
    amount = models.DecimalField("kwota", max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = "pozycja zwrotu"
        verbose_name_plural = "pozycje zwrotów"
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="payments_refundline_quantity_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.line_id} × {self.quantity}"


class ProviderEvent(models.Model):
    """Dziennik doręczeń webhooków. Więz ``(dostawca, zdarzenie)`` jest kluczem idempotencji.

    Ładunku nie przechowujemy (tylko skrót) – niesie dane płacącego, których nie zamawialiśmy.
    """

    provider = models.CharField(max_length=16, choices=Provider.choices)
    event_id = models.CharField(max_length=255)
    event_type = models.CharField(max_length=80, blank=True)
    payload_hash = models.CharField(max_length=64)
    outcome = models.CharField(max_length=40, blank=True)
    payment = models.ForeignKey(
        Payment, on_delete=models.SET_NULL, null=True, blank=True, related_name="events"
    )
    received_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        verbose_name = "doręczenie od dostawcy"
        verbose_name_plural = "doręczenia od dostawców"
        ordering = ("-received_at", "-id")
        constraints = [
            models.UniqueConstraint(fields=["provider", "event_id"], name="payments_providerevent_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.provider}:{self.event_id}"


# --- dokumenty ------------------------------------------------------------------------------------


class DocumentKind(models.TextChoices):
    PROFORMA = "PROFORMA", _("Faktura pro forma")
    INVOICE = "INVOICE", _("Faktura")


#: Skrót rodzaju w numerze dokumentu.
DOCUMENT_CODES = {DocumentKind.PROFORMA: "PF", DocumentKind.INVOICE: "FV"}


class DocumentCounter(models.Model):
    """Ostatni numer dokumentu rodzaju w roku. Blokowany wierszem w transakcji wystawienia."""

    competition = models.ForeignKey("tenancy.Competition", on_delete=models.CASCADE, related_name="+")
    kind = models.CharField(max_length=10, choices=DocumentKind.choices)
    year = models.PositiveSmallIntegerField()
    last = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["competition", "kind", "year"], name="payments_counter_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.kind}/{self.year}: {self.last}"


class BillingDocument(models.Model):
    """Pro forma albo faktura: numer i **migawka** treści. PDF składa się z migawki przy pobraniu."""

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="documents")
    competition = models.ForeignKey("tenancy.Competition", on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=10, choices=DocumentKind.choices)
    year = models.PositiveSmallIntegerField()
    sequence = models.PositiveIntegerField()
    number = models.CharField("numer", max_length=64)
    issued_at = models.DateTimeField("wystawiono", default=timezone.now)
    language = models.CharField(max_length=8)
    snapshot = models.JSONField(default=dict)

    objects = competition_scoped_manager()

    class Meta:
        verbose_name = "dokument rozliczeniowy"
        verbose_name_plural = "dokumenty rozliczeniowe"
        ordering = ("-issued_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=["competition", "kind", "year", "sequence"], name="payments_document_number"
            ),
            models.UniqueConstraint(fields=["order", "kind"], name="payments_document_one_per_order"),
        ]

    def __str__(self) -> str:
        return self.number
