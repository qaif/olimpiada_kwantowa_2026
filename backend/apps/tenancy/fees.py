"""Wpisowe: cennik konkursu, rejestr należności i dokument rozliczeniowy (§ 1.5.1).

Cały obszar stoi za flagą ``fees`` (``docs/UNIWERSALNY-ETAP-2.md`` § 0.6) i Konkurs #1 jej nie
włącza: Olimpiada Kwantowa jest bezpłatna i ma taka zostać. Bez flagi nie powstaje ani jeden
wiersz, nie pada ani jedno zapytanie i nie ma czego pokazać na żadnym ekranie – tabele przybywają
w bazie puste, a ``StageEntry.fee`` zostaje ``NULL``. To jest cała cena, którą płaci instalacja
jednokonkursowa (§ 0.1).

**To jest rejestr, a nie program księgowy (decyzja organizatora D15).** System zapisuje, ile się
należy, czy wpłynęło i kiedy – i nic ponadto. Nie numeruje faktur zgodnie z ustawą, nie prowadzi
ewidencji VAT, nie wystawia korekt i nie liczy podatku: ``FeeSchedule.vat_rate`` jest **zapisem
decyzji organizatora**, a nie podstawą wyliczenia. Dokument rozliczeniowy powstaje z
``DocumentTemplate`` rodzaju ``INVOICE`` (§ 1.1.3) tą samą ścieżką ReportLab, którą składa się
dyplom; ten moduł zna z niej wyłącznie **wersję** złożonego dokumentu (patrz
:func:`record_fee_document`). Pełne uzasadnienie: § 6, D15.

**Nieopłacone wpisowe niczego nie blokuje (decyzja organizatora D16).** Bramka „bez wpłaty nie
oddasz pracy” jest wyborem regulaminowym organizatora, a nie zachowaniem systemu, i włącza ją
pojedyncze pole ``FeeSchedule.blocks_submission`` – domyślnie ``False``. Jedynym czytelnikiem tej
reguły jest :func:`submission_blocked`, a przelew idzie dwa dni i deadline na niego nie czeka.

**Dlaczego ``apps/tenancy``, a nie ``apps/competitions``.** Cennik wydaje **konkurs**, tak samo jak
dokumenty (§ 1.1.3): to organizator ustala, ile kosztuje start i czy w ogóle cokolwiek kosztuje,
a edycja jest tylko rocznikiem, którego ten cennik dotyczy. Ten sam podział przechodzi przez całą
sekcję: konfiguracja konkursu stoi w ``tenancy``, przebieg zawodów w ``competitions``.

**Dlaczego osobny moduł, a nie ``models.py``.** Katalog przełączników w ``apps/tenancy/models.py``
ma w etapie 2 jednego właściciela (§ 4.3), a modele rejestruje się przez wykonanie modułu – stąd
jedna linijka importu na końcu ``models.py``, dokładnie tak samo jak przy
``apps.competitions.logistics`` i ``apps.accounts.twofactor``. Bez niej ``makemigrations`` nie
zobaczyłby tabel, a błąd wyglądałby jak brak modelu, a nie jak brak importu.

**Pieniądze są ``Decimal``, waluta jest kolumną.** ``DecimalField(max_digits=10, decimal_places=2)``
– nigdy ``Float``, bo ``0.1 + 0.2`` w rejestrze należności jest błędem, który zauważy księgowa
organizatora, a nie test. Waluta jest kodem ISO 4217 w osobnej kolumnie, a nie założeniem:
„wszystko jest w złotych” przestaje być prawdą po podpisaniu pierwszej umowy z organizatorem spoza
Polski.

**Ekranów tu nie ma.** Widoki koordynatora (``/coordinator/fees/``) są własnością zadania
montażowego wydania K (T42, § 2.2) i wołają wyłącznie funkcje z dolnej części tego pliku. Webhook
dostawcy płatności (T49, § 1.5.1) nie dotyka modeli wprost – woła :func:`find_fee_by_reference`
i :func:`record_payment`, bo idempotencja doręczenia i zmiana stanu należności to dwie różne
rzeczy i tylko druga należy do tego modułu.
"""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status

from apps.competitions.scoping import competition_scoped_manager
from apps.core.api import DomainError

#: Flaga konkursu z katalogu ``apps.tenancy.models.FEATURE_DEFAULTS`` (§ 0.6). Czytana **wyłącznie**
#: przez :func:`fees_enabled` – jedno miejsce odczytu, zgodnie z § 1.0 (c).
FEATURE = "fees"

#: Kod waluty: trzy wielkie litery (ISO 4217). Walidator, a nie ``choices``: lista walut świata
#: należy do normy, a nie do nas, a zamknięta lista w kodzie znaczyłaby migrację przy każdym
#: organizatorze, który rozlicza się w czymś, czego nie przewidzieliśmy.
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

#: Waluta domyślna. Wartość domyślna, a nie założenie – patrz docstring modułu.
DEFAULT_CURRENCY = "PLN"

#: Domyślny termin płatności w dniach od wystawienia należności.
DEFAULT_DUE_DAYS = 14

#: Dokładność kwoty: dwa miejsca po przecinku, tyle samo, ile ma kolumna.
MONEY_QUANTUM = Decimal("0.01")

#: Rodzaj dokumentu z ``DocumentTemplate`` (§ 1.1.3), którym rozlicza się wpisowe. Napis, a nie
#: import z ``apps.tenancy.documents``: ten moduł nie zna API tamtego i nie ma powodu go znać –
#: rodzaj dokumentu jest **umową między nimi**, a nie zależnością (patrz :func:`issue_fee_document`).
DOCUMENT_KIND = "INVOICE"


def validate_currency(value: str) -> None:
    """Waluta zapisana kodem ISO 4217 („PLN”, „EUR”), wielkimi literami.

    Wielkość liter jest częścią reguły, a nie kosmetyką: kod idzie na dokument rozliczeniowy
    i do porównania z walutą cennika, a „pln” obok „PLN” znaczyłoby dwie waluty w jednym rejestrze.
    """
    if not CURRENCY_RE.match(value or ""):
        raise ValidationError(
            "Walutę podaje się kodem ISO 4217 wielkimi literami, np. PLN.",
            code="invalid_currency",
        )


def quantize_money(amount) -> Decimal:
    """Kwota sprowadzona do dwóch miejsc po przecinku – tylu, ile ma kolumna.

    Zaokrąglenie robimy **przed** zapisem, a nie zostawiamy bazie: ``Decimal("10.005")`` zapisany
    i odczytany byłby inną liczbą niż ta, którą widział wołający, a rejestr należności ma oddawać
    dokładnie to, co w nim postawiono.

    ``ROUND_HALF_UP``, a nie domyślne ``ROUND_HALF_EVEN``: pół grosza w górę jest tym, czego
    człowiek oczekuje po kwocie („50,005 zł to 50,01 zł”), a zaokrąglenie bankierskie – choć
    uczciwsze statystycznie – daje wynik, którego organizator nie potrafi wytłumaczyć rodzicowi.

    ``float`` przechodzi przez napis, bo ``Decimal(0.1)`` jest liczbą, której nikt nie wpisał.
    """
    if isinstance(amount, float):
        amount = str(amount)
    return Decimal(amount).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


# --- modele ---------------------------------------------------------------------------------------


class FeeSchedule(models.Model):
    """Cennik wpisowego: kwota za udział w edycji, ewentualnie zależna od kategorii.

    Cennik jest **konfiguracją konkursu** przypiętą do rocznika: ta sama olimpiada bierze w tym
    roku 50 zł, a w przyszłym 60, i oba fakty mają zostać w bazie. Kategoria jest opcjonalna –
    ``NULL`` znaczy „cena podstawowa edycji”, czyli ta, którą płaci każdy, kto nie wpada do
    kategorii z własną ceną (:func:`schedule_for`).

    Kwota należności jest **kopiowana** do ``ParticipantFee`` w chwili naliczenia, a nie czytana
    z cennika przy każdym wyświetleniu. Podniesienie ceny w połowie sezonu nie może zmienić kwoty,
    którą ktoś dostał na dokumencie i zapłacił – to jest ta sama reguła, co ``template_version``
    przy dokumentach i ``document_version`` przy zgodach.
    """

    #: ``PROTECT``, tak samo jak przy ``Edition.competition``: skasowanie konkursu razem
    #: z cennikiem pociągnęłoby za sobą rejestr wpłat, czyli dokumentację rozliczeń.
    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.PROTECT,
        related_name="fee_schedules",
        verbose_name="konkurs",
    )
    #: ``CASCADE``, bo cennik bez edycji nie znaczy nic: kasowanie rocznika (co i tak jest możliwe
    #: wyłącznie przed pierwszym zapisem, dalej trzyma go ``PROTECT`` z etapów) zabiera ze sobą
    #: cennik tego rocznika, a nie zostawia wiersza bez właściciela.
    edition = models.ForeignKey(
        "competitions.Edition",
        on_delete=models.CASCADE,
        related_name="fee_schedules",
        verbose_name="edycja",
    )
    #: ``NULL`` = cena podstawowa edycji. Kategoria własna jest **wyjątkiem** od tej ceny, a nie
    #: obowiązkiem: konkurs bez kategorii ma jeden wiersz i to jest stan docelowy, a nie brak.
    category = models.ForeignKey(
        "competitions.Category",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="fee_schedules",
        verbose_name="kategoria",
    )
    name = models.CharField("nazwa", max_length=120)
    amount = models.DecimalField("kwota", max_digits=10, decimal_places=2)
    currency = models.CharField(
        "waluta", max_length=3, default=DEFAULT_CURRENCY, validators=[validate_currency]
    )
    #: Wyłącznie **zapis** decyzji organizatora – system nie wylicza podatku i nie jest programem
    #: księgowym (decyzja D15). ``None`` = zwolnione.
    vat_rate = models.PositiveSmallIntegerField("stawka VAT (%)", null=True, blank=True)
    due_days = models.PositiveSmallIntegerField("termin płatności (dni)", default=DEFAULT_DUE_DAYS)
    #: Decyzja D16 i jedyne miejsce, w którym brak wpłaty cokolwiek blokuje. Domyślnie ``False``:
    #: system ma tę bramkę **umieć**, a nie włączać ją sam. Czyta ją :func:`submission_blocked`.
    blocks_submission = models.BooleanField("brak wpłaty blokuje oddanie pracy", default=False)
    #: Wycofanie cennika jest przestawieniem tego pola, a nie usunięciem wiersza: należności
    #: naliczone z niego mają dalej pokazywać, z czego wynikła ich kwota.
    is_active = models.BooleanField("aktywny", default=True)
    created_at = models.DateTimeField("utworzony", default=timezone.now)

    #: Domyślna ścieżka queryseta (``competition``) – cennik ma własną kolumnę, bo jest
    #: konfiguracją konkursu (§ 1.0 (a)).
    objects = competition_scoped_manager()

    class Meta:
        verbose_name = "cennik wpisowego"
        verbose_name_plural = "cenniki wpisowego"
        ordering = ("competition", "edition", "category", "id")
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gte=0), name="tenancy_feeschedule_amount_not_negative"
            ),
            # Stawka spoza zakresu 0–100 nie jest stawką, tylko literówką – a literówka trafia
            # wprost na dokument rozliczeniowy.
            models.CheckConstraint(
                condition=Q(vat_rate__isnull=True) | Q(vat_rate__lte=100),
                name="tenancy_feeschedule_vat_rate_percent",
            ),
            models.UniqueConstraint(
                fields=["edition", "category"], name="tenancy_feeschedule_unique_per_category"
            ),
            # Druga połowa tej samej reguły. ``UniqueConstraint`` na kolumnach z ``NULL``
            # przepuszcza w PostgreSQL dowolną liczbę wierszy bez kategorii (``NULL`` nie jest
            # równy ``NULL``), więc bez tego więzu cena **podstawowa** edycji mogłaby istnieć
            # w dwóch egzemplarzach – a to jest dokładnie ten wiersz, którego szuka
            # :func:`schedule_for`, gdy uczestnik nie ma kategorii.
            models.UniqueConstraint(
                fields=["edition"],
                condition=Q(category__isnull=True),
                name="tenancy_feeschedule_unique_base",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.amount} {self.currency})"

    def clean(self) -> None:
        """Cennik, edycja i kategoria należą do **jednego** konkursu.

        Ostatnia linia obrony przed cennikiem, który naliczyłby uczestnikom konkursu A kwotę
        wpisaną przez organizatora konkursu B. Zakresowanie zawęża **odczyt**; spójność zapisu
        jest osobnym pytaniem i odpowiada na nie walidacja (§ 3.4).
        """
        super().clean()
        errors: dict[str, str] = {}
        if self.edition_id and self.competition_id and self.edition.competition_id != self.competition_id:
            errors["edition"] = "Edycja należy do innego konkursu niż cennik."
        if self.category_id and self.competition_id and self.category.competition_id != self.competition_id:
            errors["category"] = "Kategoria należy do innego konkursu niż cennik."
        if self.vat_rate is not None and self.vat_rate > 100:
            errors["vat_rate"] = "Stawka VAT jest liczbą procent z zakresu 0–100."
        if errors:
            raise ValidationError(errors)


class FeeStatus(models.TextChoices):
    """Stan jednej należności. Pięć wartości, bo tyle rozróżnia organizator rozmawiając z rodzicem.

    ``EXEMPT`` (zwolniony) i ``WAIVED`` (umorzone) są celowo osobne: pierwsze znaczy „ta osoba
    nigdy nie miała płacić” (regulaminowe zwolnienie), drugie – „miała, ale organizator odstąpił”.
    Jedna wartość na oba stany zlewałaby decyzję regulaminową z decyzją uznaniową, a to są dwa
    różne pytania w sprawozdaniu.
    """

    DUE = "DUE", "do zapłaty"
    PAID = "PAID", "zapłacone"
    EXEMPT = "EXEMPT", "zwolniony"
    WAIVED = "WAIVED", "umorzone"
    REFUNDED = "REFUNDED", "zwrócone"


#: Stany, w których należność jest **rozliczona** – czyli takie, przy których bramka z D16 nie
#: blokuje niczego. Zwrot (``REFUNDED``) do nich nie należy: pieniądze wróciły, więc należność
#: znów czeka na rozstrzygnięcie organizatora.
SETTLED_STATUSES: tuple[str, ...] = (FeeStatus.PAID, FeeStatus.EXEMPT, FeeStatus.WAIVED)


class ParticipantFee(models.Model):
    """Należność jednego uczestnika w jednej edycji. **Rejestr**, nie operacja finansowa.

    Model zapisuje, ile się należy i czy wpłynęło – a nie przeprowadza transakcji, nie łączy się
    z bankiem i nie zna dostawcy płatności. Potwierdzenie z bramki płatniczej przyjmuje webhook
    (T49) i sprowadza je do jednego wywołania :func:`record_payment`; wszystko, co ten model
    o płatności wie, to data i identyfikator wpłaty podany przez dostawcę.

    Kwota i waluta są **kopią** z cennika z chwili naliczenia, a nie odczytem przez klucz obcy:
    patrz docstring :class:`FeeSchedule`.
    """

    #: ``PROTECT``: skasowanie cennika razem z należnościami zabrałoby informację, ile i za co
    #: ktoś zapłacił. Cennik wycofuje się przez ``is_active``.
    schedule = models.ForeignKey(
        FeeSchedule, on_delete=models.PROTECT, related_name="fees", verbose_name="cennik"
    )
    #: ``PROTECT`` z tego samego powodu, co przy ``Participant.competition``: rejestr wpłat jest
    #: dokumentem rozliczenia, a nie danymi pomocniczymi profilu.
    participant = models.ForeignKey(
        "accounts.Participant", on_delete=models.PROTECT, related_name="fees", verbose_name="uczestnik"
    )
    amount = models.DecimalField("kwota", max_digits=10, decimal_places=2)
    currency = models.CharField(
        "waluta", max_length=3, default=DEFAULT_CURRENCY, validators=[validate_currency]
    )
    status = models.CharField("status", max_length=16, choices=FeeStatus.choices, default=FeeStatus.DUE)
    exemption_reason = models.CharField("powód zwolnienia", max_length=200, blank=True)
    due_on = models.DateField("termin płatności", null=True, blank=True)
    paid_at = models.DateTimeField("wpłynęło", null=True, blank=True)
    #: Identyfikator wpłaty **u dostawcy** – klucz idempotencji dla webhooka (T49) i jedyna rzecz,
    #: po której da się dopasować przelew do należności bez pytania człowieka.
    external_reference = models.CharField("identyfikator wpłaty", max_length=120, blank=True)
    #: Wersja tekstu dokumentu rozliczeniowego (``DocumentTemplate.version``, § 1.1.3). Zapisujemy
    #: ją z tego samego powodu, co ``Certificate.template_version``: PDF-a **nie przechowujemy**,
    #: więc powstaje przy każdym pobraniu – bez zapamiętanej wersji poprawka szablonu zmieniłaby
    #: treść dokumentu, który ktoś trzyma w ręku. Puste znaczy „dokumentu jeszcze nie wydano”.
    document_version = models.CharField("wersja dokumentu", max_length=100, blank=True)
    document_issued_at = models.DateTimeField("dokument wydano", null=True, blank=True)
    #: ``SET_NULL``: skasowanie konta koordynatora nie może wymazać tego, że wpłata została
    #: przyjęta – bez autora zostaje data i kwota, czyli nadal dokument.
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fees_recorded",
        verbose_name="zapisał",
    )
    created_at = models.DateTimeField("naliczono", default=timezone.now)

    #: Droga do konkursu wiedzie przez uczestnika, a nie przez cennik: obie prowadzą do tego samego
    #: wiersza (pilnuje tego :meth:`clean`), a własna kolumna byłaby trzecią drogą do tej samej
    #: prawdy, czyli trzecią okazją do rozjazdu (§ 1.0 (a)).
    objects = competition_scoped_manager("participant__competition")

    class Meta:
        verbose_name = "należność uczestnika"
        verbose_name_plural = "należności uczestników"
        ordering = ("-created_at", "id")
        constraints = [
            models.UniqueConstraint(fields=["schedule", "participant"], name="tenancy_participantfee_unique"),
            # Zapłacone musi mieć datę, zwolnione musi mieć powód: bez tego rejestr odpowiada
            # „zapłacone” bez odpowiedzi na pytanie „kiedy”, a to jest pytanie, które pada.
            models.CheckConstraint(
                condition=~Q(status=FeeStatus.PAID) | Q(paid_at__isnull=False),
                name="tenancy_participantfee_paid_has_date",
            ),
            models.CheckConstraint(
                condition=~Q(status=FeeStatus.EXEMPT) | ~Q(exemption_reason=""),
                name="tenancy_participantfee_exempt_has_reason",
            ),
            models.CheckConstraint(
                condition=Q(amount__gte=0), name="tenancy_participantfee_amount_not_negative"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.participant.public_code}: {self.amount} {self.currency} ({self.status})"

    @property
    def is_settled(self) -> bool:
        """Czy należność jest rozliczona – jedno pytanie dla panelu, bramki i sprawozdania.

        Jedyne miejsce, w którym stoi lista stanów „już nie czekamy”; porównanie z literałem
        w widoku byłoby drugą definicją tej samej reguły.
        """
        return self.status in SETTLED_STATUSES

    @property
    def reference(self) -> str:
        """Oznaczenie należności do wpisania na dokumencie. **Nie jest numerem faktury** (D15).

        Numer faktury jest pojęciem ustawowym: ciągły, bez luk, w rejestrze, z korektami. Tego
        system nie prowadzi i prowadzić nie będzie, więc oznaczenie jest tym, czym naprawdę jest –
        kluczem do wiersza rejestru, po którym organizator odnajdzie należność w panelu.
        """
        return f"{self.participant.public_code}/{self.pk}"

    def clean(self) -> None:
        """Uczestnik i cennik należą do jednego konkursu, a waluta należności – do jej cennika."""
        super().clean()
        errors: dict[str, str] = {}
        if self.schedule_id and self.participant_id:
            if self.schedule.competition_id != self.participant.competition_id:
                errors["schedule"] = "Cennik należy do innego konkursu niż uczestnik."
            elif self.currency != self.schedule.currency:
                errors["currency"] = "Waluta należności musi być walutą cennika, z którego wynika."
        if self.status == FeeStatus.PAID and self.paid_at is None:
            errors["paid_at"] = "Zapłacona należność musi mieć datę wpłaty."
        if self.status == FeeStatus.EXEMPT and not self.exemption_reason:
            errors["exemption_reason"] = "Zwolnienie wymaga podania powodu."
        if errors:
            raise ValidationError(errors)


# --- flaga i odwroty ------------------------------------------------------------------------------


def fees_enabled(competition=None) -> bool:
    """Czy ten konkurs pobiera wpisowe. **Jedyny** odczyt flagi w module (§ 1.0 (c)).

    ``None`` (nie wiadomo, o który konkurs chodzi) znaczy „nie”: wpisowe jest zdolnością dołożoną,
    a nie zachowaniem zastanym, więc brak rozstrzygnięcia ma dać stan sprzed etapu 2.
    """
    return competition is not None and competition.has_feature(FEATURE)


def require_fees(competition=None) -> None:
    """Przerywa, gdy konkurs nie pobiera wpisowego. Odwrót twardy – to zapis **konfiguracji**.

    Rozróżnienie z § 1.0 (b): listy i podglądy schodzą do pustki (miękko), ale naliczenie
    należności albo przyjęcie wpłaty w konkursie bez wpisowego jest błędem wołającego i ma być
    słyszalne od razu, a nie zamienić się w wiersz, którego nikt nigdy nie zobaczy.
    """
    if not fees_enabled(competition):
        raise DomainError(
            "Ten konkurs nie pobiera wpisowego.",
            "FEES_DISABLED",
            status.HTTP_403_FORBIDDEN,
        )


def competition_of_fee(fee: ParticipantFee):
    """Konkurs należności – tą samą drogą, którą filtruje zakresowanie (``participant__competition``)."""
    return fee.participant.competition


# --- cennik ----------------------------------------------------------------------------------------


def schedules_for(competition=None, edition=None):
    """Cenniki konkursu; przy wyłączonej fladze **pustka bez zapytania do bazy**.

    Pustka, a nie wyjątek: ekran konkursu bez wpisowego i tak nie ma czego pokazać, a lista wyboru
    cennika w cudzym formularzu ma być pusta, a nie wywalać widok.
    """
    if not fees_enabled(competition):
        return FeeSchedule.objects.none()
    queryset = FeeSchedule.objects.for_competition(competition)
    if edition is not None:
        queryset = queryset.filter(edition=edition)
    return queryset


def schedule_for(edition, category=None) -> FeeSchedule | None:
    """Cennik dla tej edycji i kategorii: od szczegółu do ogółu, jak ``resolve_template`` (§ 1.1.3).

    Kolejność jest jedna i jest tutaj: ``(edycja, kategoria)`` → ``(edycja, —)`` → brak. Cennik
    kategorii jest **wyjątkiem** od ceny podstawowej, więc brak wyjątku nie znaczy „za darmo”,
    tylko „cena podstawowa”. ``None`` na końcu znaczy, że organizator nie ustalił żadnej ceny dla
    tej edycji – i to jest odpowiedź poprawna, a nie błąd: konkurs może pobierać wpisowe wyłącznie
    w wybranych rocznikach.

    Wiersze nieaktywne (``is_active=False``) nie biorą udziału w doborze: wycofany cennik ma dalej
    tłumaczyć kwoty już naliczone, a nie naliczać nowe.
    """
    competition = edition.competition
    if not fees_enabled(competition):
        return None
    active = FeeSchedule.objects.for_competition(competition).filter(edition=edition, is_active=True)
    if category is not None:
        specific = active.filter(category=category).first()
        if specific is not None:
            return specific
    return active.filter(category__isnull=True).first()


def create_fee_schedule(competition, *, edition, name: str, amount, actor=None, request=None, **fields):
    """Zakłada cennik wpisowego. Wołane z ekranu „Wpisowe” (T42) i z importu konfiguracji.

    Kwota przechodzi przez :func:`quantize_money`, bo wołający podaje ją tak, jak ją napisał
    człowiek („50”, „50.5”, ``Decimal("50.005")``), a w rejestrze ma stać jedna, jednoznaczna
    liczba.
    """
    from apps.core.models import audit

    require_fees(competition)
    schedule = FeeSchedule(
        competition=competition, edition=edition, name=name, amount=quantize_money(amount), **fields
    )
    schedule.full_clean()
    schedule.save()
    audit(
        actor,
        "fee_schedule.created",
        schedule,
        {
            "name": schedule.name,
            "amount": str(schedule.amount),
            "currency": schedule.currency,
            "category": schedule.category_id,
            "blocks_submission": schedule.blocks_submission,
        },
        request=request,
    )
    return schedule


def update_fee_schedule(schedule: FeeSchedule, *, actor=None, request=None, **fields):
    """Zmienia pola cennika; audyt notuje wyłącznie to, co naprawdę się zmieniło.

    Zmiana kwoty **nie rusza należności już naliczonych** – i to jest cała reguła kopiowania kwoty
    z :class:`FeeSchedule`. Podniesienie ceny obowiązuje od następnego naliczenia, a nie wstecz.
    """
    from apps.core.models import audit

    if "amount" in fields:
        fields["amount"] = quantize_money(fields["amount"])
    before = {field: getattr(schedule, field) for field in fields}
    for field, value in fields.items():
        setattr(schedule, field, value)
    schedule.full_clean()
    schedule.save(update_fields=list(fields) or None)
    changed = {
        field: {"from": str(before[field]), "to": str(value)}
        for field, value in fields.items()
        if before[field] != value
    }
    if changed:
        audit(actor, "fee_schedule.updated", schedule, changed, request=request)
    return schedule


# --- rejestr należności ----------------------------------------------------------------------------


def fees_for(competition=None, edition=None):
    """Należności konkursu; przy wyłączonej fladze pustka bez zapytania do bazy."""
    if not fees_enabled(competition):
        return ParticipantFee.objects.none()
    queryset = ParticipantFee.objects.for_competition(competition)
    if edition is not None:
        queryset = queryset.filter(schedule__edition=edition)
    return queryset


def fee_for(participant, edition) -> ParticipantFee | None:
    """Należność tego uczestnika w tej edycji albo ``None``. Jedno wejście dla panelu uczestnika."""
    if not fees_enabled(participant.competition):
        return None
    return (
        ParticipantFee.objects.select_related("schedule")
        .filter(participant=participant, schedule__edition=edition)
        .first()
    )


def assign_fee(
    participant,
    edition,
    *,
    category=None,
    schedule=None,
    issued_on=None,
    actor=None,
    request=None,
) -> ParticipantFee:
    """Nalicza należność uczestnikowi. **Idempotentne**: druga próba oddaje ten sam wiersz.

    Idempotencja jest tu wymaganiem, a nie wygodą: naliczenie woła rejestracja, import grupowy
    i ekran koordynatora, a każde z tych trzech miejsc bywa powtórzone – i żadne z nich nie może
    wystawić drugiej należności za ten sam start. Wiersz istniejący oddajemy **bez zmiany**, także
    wtedy, gdy cennik zdążył w międzyczasie podrożeć.

    Kwota, waluta i termin są kopiowane w chwili naliczenia. Termin liczy się od ``issued_on``
    (domyślnie: dzisiaj w strefie konkursu) plus ``FeeSchedule.due_days``.
    """
    from apps.core.models import audit

    competition = participant.competition
    require_fees(competition)
    schedule = schedule or schedule_for(edition, category)
    if schedule is None:
        raise DomainError(
            "Ta edycja nie ma ustalonego cennika wpisowego.",
            "FEE_SCHEDULE_MISSING",
            status.HTTP_409_CONFLICT,
        )
    if schedule.competition_id != competition.pk:
        raise DomainError(
            "Cennik należy do innego konkursu niż uczestnik.",
            "FEE_SCHEDULE_FOREIGN",
            status.HTTP_409_CONFLICT,
        )
    existing = ParticipantFee.objects.filter(schedule=schedule, participant=participant).first()
    if existing is not None:
        return existing
    issued_on = issued_on or timezone.localdate()
    fee = ParticipantFee(
        schedule=schedule,
        participant=participant,
        amount=schedule.amount,
        currency=schedule.currency,
        due_on=issued_on + timedelta(days=schedule.due_days),
    )
    fee.full_clean()
    fee.save()
    audit(
        actor,
        "fee.assigned",
        fee,
        {"amount": str(fee.amount), "currency": fee.currency, "due_on": str(fee.due_on)},
        request=request,
    )
    return fee


def assign_fee_if_due(participant, edition, *, category=None, actor=None, request=None):
    """Naliczenie **miękkie**: ``None``, gdy konkurs nie pobiera wpisowego albo nie ma cennika.

    To jest wejście dla rejestracji i importu grupowego, czyli dla kodu, który biegnie w **każdym**
    konkursie i nie ma powodu wiedzieć, czy ten akurat coś pobiera. Twardy odwrót
    (:func:`assign_fee`) zostaje dla ekranu koordynatora, gdzie „nalicz wpisowe” jest świadomą
    decyzją człowieka i cisza w odpowiedzi byłaby myląca.
    """
    if not fees_enabled(participant.competition):
        return None
    if schedule_for(edition, category) is None:
        return None
    return assign_fee(participant, edition, category=category, actor=actor, request=request)


def attach_fee(entry, fee=None, *, actor=None, request=None) -> ParticipantFee | None:
    """Wiąże należność z wpisem do etapu (``StageEntry.fee``) – albo nie robi nic.

    Pole na wpisie istnieje po to, żeby ekran etapu pokazał „nieopłacone” **bez złączenia przez
    edycję** (§ 1.5.1). Jest więc skrótem, a nie drugim źródłem prawdy: gdy jest puste,
    :func:`submission_blocked` i tak znajdzie należność po uczestniku i edycji.
    """
    from apps.core.models import audit

    competition = entry.stage.edition.competition
    if not fees_enabled(competition):
        return None
    fee = fee or fee_for(entry.participant, entry.stage.edition)
    if fee is None or entry.fee_id == getattr(fee, "pk", None):
        return fee
    entry.fee = fee
    entry.save(update_fields=["fee"])
    audit(actor, "fee.attached_to_entry", entry, {"fee": fee.pk}, request=request)
    return fee


# --- wpłaty, zwolnienia, zwroty ---------------------------------------------------------------------


def find_fee_by_reference(competition, external_reference: str) -> ParticipantFee | None:
    """Należność o tym identyfikatorze wpłaty albo ``None`` – dopasowanie dla webhooka (T49).

    ``None`` jest odpowiedzią, a nie błędem: dostawca doręcza także potwierdzenia, których nie da
    się dopasować (przelew z cudzym tytułem, wpłata po anulowaniu), a webhook ma je **zapisać**
    i odpowiedzieć 2xx, zamiast kazać dostawcy ponawiać w nieskończoność. Dopasowanie ręczne jest
    wtedy pracą organizatora i to jest właściwy podział ról.
    """
    if not external_reference or not fees_enabled(competition):
        return None
    return (
        ParticipantFee.objects.for_competition(competition)
        .select_related("schedule", "participant")
        .filter(external_reference=external_reference)
        .first()
    )


def set_reference(fee: ParticipantFee, reference: str, *, actor=None, request=None) -> ParticipantFee:
    """Nadaje należności identyfikator wpłaty **przed** utworzeniem płatności u dostawcy.

    Trzeci kierunek obok odczytu (``find_fee_by_reference``) i zapisu **przy** wpłacie
    (``record_payment``) – i pierwszy w czasie: organizator zakłada płatność u dostawcy z własnym
    identyfikatorem i musi go wpisać należności, zanim przyjdzie potwierdzenie. Bez tego webhook
    nie ma czego dopasować, bo dopasowuje wyłącznie po tym polu, i każde doręczenie kończyłoby się
    wierszem ``matched=False``.

    Reguła jest jedna i twarda: identyfikatora **nie nadpisujemy** na należności, która ma już
    swój. Dwa identyfikatory na jednej należności znaczą dwie płatności u dostawcy i potwierdzenie,
    które dopasuje się do tej, o której organizator zapomniał.
    """
    from apps.core.models import audit

    require_fees(competition_of_fee(fee))
    reference = (reference or "").strip()
    if not reference:
        raise DomainError(
            "Identyfikator wpłaty nie może być pusty.",
            "FEE_REFERENCE_REQUIRED",
            status.HTTP_400_BAD_REQUEST,
        )
    if fee.external_reference == reference:
        return fee
    if fee.external_reference:
        raise DomainError(
            "Ta należność ma już identyfikator wpłaty. Zmiana rozdzieliłaby ją od płatności "
            "założonej u dostawcy.",
            "FEE_REFERENCE_SET",
            status.HTTP_409_CONFLICT,
        )
    fee.external_reference = reference
    fee.full_clean()
    fee.save(update_fields=["external_reference"])
    audit(actor, "fee.reference_set", fee, {"reference": reference}, request=request)
    return fee


def record_payment(
    fee: ParticipantFee,
    *,
    external_reference: str = "",
    paid_at=None,
    amount=None,
    actor=None,
    request=None,
) -> ParticipantFee:
    """Zapisuje wpłatę. **Idempotentne po identyfikatorze wpłaty** – to jest wejście dla T49.

    Trzy reguły, każda z powodem:

    1. **powtórzone doręczenie nie zmienia niczego.** Należność już zapłacona tym samym
       identyfikatorem oddaje się bez zapisu i bez drugiego wpisu audytowego – dostawca ponawia
       webhooka po każdym timeoucie, a druga data wpłaty na tej samej należności znaczyłaby, że
       rejestr odpowiada „kiedy” inaczej za każdym razem;
    2. **inny identyfikator na zapłaconej należności jest konfliktem**, a nie drugą wpłatą do
       zaksięgowania: to jest sytuacja dla człowieka (nadpłata, pomyłka dostawcy), a nie dla
       automatu, który miałby ją po cichu nadpisać;
    3. **kwota jest opcjonalna i służy wyłącznie do porównania.** Ten model nie sumuje wpłat
       częściowych – rejestr odpowiada „zapłacone albo nie”, bo D15 wyklucza prowadzenie ksiąg.
       Rozbieżność idzie do audytu, żeby organizator ją zobaczył.
    """
    from apps.core.models import audit

    require_fees(competition_of_fee(fee))
    if fee.status == FeeStatus.PAID:
        if not external_reference or fee.external_reference == external_reference:
            return fee
        raise DomainError(
            "Ta należność jest już zapłacona innym identyfikatorem wpłaty.",
            "FEE_ALREADY_PAID",
            status.HTTP_409_CONFLICT,
        )
    if fee.status in (FeeStatus.EXEMPT, FeeStatus.WAIVED):
        raise DomainError(
            "Ta należność jest zwolniona albo umorzona – wpłaty do niej nie zapisujemy.",
            "FEE_NOT_DUE",
            status.HTTP_409_CONFLICT,
        )
    diff: dict[str, object] = {"from": fee.status}
    if amount is not None and quantize_money(amount) != fee.amount:
        diff["amount_mismatch"] = {"expected": str(fee.amount), "received": str(quantize_money(amount))}
    with transaction.atomic():
        fee.status = FeeStatus.PAID
        fee.paid_at = paid_at or timezone.now()
        if external_reference:
            fee.external_reference = external_reference
        fee.recorded_by = actor if actor is not None and getattr(actor, "is_authenticated", False) else None
        fee.full_clean()
        fee.save(update_fields=["status", "paid_at", "external_reference", "recorded_by"])
    diff.update({"to": fee.status, "paid_at": fee.paid_at.isoformat(), "reference": fee.external_reference})
    audit(actor, "fee.payment_recorded", fee, diff, request=request)
    return fee


def _settle(fee: ParticipantFee, new_status: str, reason: str, action: str, actor, request):
    """Wspólny przebieg zwolnienia i umorzenia: powód obowiązkowy, jeden wpis audytowy.

    Powód jest wymagany także dla umorzenia, choć więz bazy pilnuje go wyłącznie przy zwolnieniu:
    decyzja uznaniowa bez uzasadnienia nie jest decyzją, tylko przestawionym polem – to ta sama
    reguła, co przy ``StageEntry.manual_qualification_reason``.
    """
    from apps.core.models import audit

    require_fees(competition_of_fee(fee))
    if not reason.strip():
        raise DomainError(
            "Zwolnienie i umorzenie wymagają podania powodu.",
            "FEE_REASON_REQUIRED",
            status.HTTP_400_BAD_REQUEST,
        )
    if fee.status == FeeStatus.PAID:
        raise DomainError(
            "Ta należność jest już zapłacona.",
            "FEE_ALREADY_PAID",
            status.HTTP_409_CONFLICT,
        )
    before = fee.status
    fee.status = new_status
    fee.exemption_reason = reason.strip()
    fee.recorded_by = actor if actor is not None and getattr(actor, "is_authenticated", False) else None
    fee.full_clean()
    fee.save(update_fields=["status", "exemption_reason", "recorded_by"])
    audit(
        actor,
        action,
        fee,
        {"from": before, "to": new_status, "reason": fee.exemption_reason},
        request=request,
    )
    return fee


def mark_exempt(fee: ParticipantFee, *, reason: str, actor=None, request=None) -> ParticipantFee:
    """Zwolnienie regulaminowe: ta osoba nigdy nie miała płacić."""
    return _settle(fee, FeeStatus.EXEMPT, reason, "fee.exempted", actor, request)


def waive_fee(fee: ParticipantFee, *, reason: str, actor=None, request=None) -> ParticipantFee:
    """Umorzenie uznaniowe: miała płacić, organizator odstąpił."""
    return _settle(fee, FeeStatus.WAIVED, reason, "fee.waived", actor, request)


def record_refund(fee: ParticipantFee, *, reason: str = "", actor=None, request=None) -> ParticipantFee:
    """Zapisuje zwrot wpłaty. Zwrot zdejmuje się wyłącznie z należności **zapłaconej**.

    ``paid_at`` zostaje: data wpływu jest faktem, który się wydarzył, a zwrot jest drugim faktem
    obok niego, a nie wymazaniem pierwszego.
    """
    from apps.core.models import audit

    require_fees(competition_of_fee(fee))
    if fee.status != FeeStatus.PAID:
        raise DomainError(
            "Zwrot dotyczy wyłącznie należności zapłaconej.",
            "FEE_NOT_PAID",
            status.HTTP_409_CONFLICT,
        )
    fee.status = FeeStatus.REFUNDED
    fee.recorded_by = actor if actor is not None and getattr(actor, "is_authenticated", False) else None
    fee.full_clean()
    fee.save(update_fields=["status", "recorded_by"])
    audit(actor, "fee.refunded", fee, {"reason": reason}, request=request)
    return fee


# --- bramka z decyzji D16 ---------------------------------------------------------------------------


def submission_blocked(entry) -> bool:
    """Czy brak wpłaty blokuje oddanie pracy w tym wpisie. **Domyślnie zawsze ``False``** (D16).

    Trzy warunki naraz i każdy z nich osobno wystarcza, żeby odpowiedź brzmiała „nie”: konkurs
    nie pobiera wpisowego, cennik nie włączył bramki (``blocks_submission``), należność jest
    rozliczona. Konkurs #1 wychodzi z tej funkcji na pierwszym warunku i **bez zapytania do bazy**.

    Funkcja niczego nie podnosi i nie ma prawa: decyzja „nie przyjmujemy pracy” należy do
    ``apps.submissions`` i do tamtejszych komunikatów, a tutaj stoi wyłącznie odpowiedź na pytanie
    o stan rozliczenia.
    """
    if not fees_enabled(entry.stage.edition.competition):
        return False
    fee = entry.fee or fee_for(entry.participant, entry.stage.edition)
    if fee is None:
        return False
    return fee.schedule.blocks_submission and not fee.is_settled


# --- podsumowania dla panelu (T42) -------------------------------------------------------------------


def fee_rows(edition) -> list[dict]:
    """Wiersze ekranu „Wpisowe” dla jednej edycji: jedno zapytanie, gotowe do wyświetlenia.

    Składanie wierszy jest tutaj, a nie w widoku, z tego samego powodu, co przy logistyce: ekran
    należy do zadania montażowego (T42), a reguła „co pokazujemy o należności” – do obszaru.
    ``select_related`` obejmuje wszystko, co wiersz czyta, bo lista ma nie rosnąć zapytaniami
    razem z liczbą uczestników (§ 5.6).
    """
    if not fees_enabled(edition.competition):
        return []
    labels = dict(FeeStatus.choices)
    rows = []
    for fee in (
        ParticipantFee.objects.filter(schedule__edition=edition)
        .select_related("participant", "schedule", "schedule__category")
        .order_by("participant__public_code", "id")
    ):
        rows.append(
            {
                "fee": fee,
                "participant_code": fee.participant.public_code,
                "schedule": fee.schedule.name,
                "category": fee.schedule.category.name if fee.schedule.category_id else "",
                "amount": fee.amount,
                "currency": fee.currency,
                "status": fee.status,
                "status_label": labels[fee.status],
                "settled": fee.is_settled,
                "due_on": fee.due_on,
                "paid_at": fee.paid_at,
                "reference": fee.external_reference,
                "document_version": fee.document_version,
            }
        )
    return rows


def fee_totals(edition) -> dict[str, dict[str, object]]:
    """Podsumowanie edycji **po walutach**: ile naliczono, ile wpłynęło, ile czeka.

    Po walutach, a nie jedną liczbą: sumowanie 50 PLN z 10 EUR daje liczbę, która nie znaczy nic,
    a przeliczanie po kursie znaczyłoby kurs z jakiego dnia – czyli księgowość, której ten system
    nie prowadzi (D15).
    """
    totals: dict[str, dict[str, object]] = {}
    if not fees_enabled(edition.competition):
        return totals
    for fee in ParticipantFee.objects.filter(schedule__edition=edition).only("amount", "currency", "status"):
        bucket = totals.setdefault(
            fee.currency,
            {"charged": Decimal("0.00"), "paid": Decimal("0.00"), "outstanding": Decimal("0.00"), "count": 0},
        )
        bucket["charged"] += fee.amount
        bucket["count"] += 1
        if fee.status == FeeStatus.PAID:
            bucket["paid"] += fee.amount
        elif fee.status in (FeeStatus.DUE, FeeStatus.REFUNDED):
            bucket["outstanding"] += fee.amount
    return totals


# --- dokument rozliczeniowy (hak do T12) --------------------------------------------------------------


def fee_document_context(fee: ParticipantFee) -> dict[str, str]:
    """Podstawienia do dokumentu rozliczeniowego – mapa napisów, nic więcej.

    Nazwy znaczników są **wspólne** z resztą dokumentów (§ 1.1.3): ``{edition}``,
    ``{participant_code}``, ``{recipient}``, ``{school}``, ``{date}``, ``{number}``. Rachunek
    potrzebuje ponadto ``{amount}``, ``{currency}``, ``{due_date}`` i ``{vat_rate}`` – wartości,
    których dyplom nie zna.

    Czego tu **nie ma i mieć nie będzie**: nazwy konkursu w trzech odmianach i nazwy organizatora.
    Te wartości wynikają z samego konkursu, a nie z należności, i wylicza je moduł dokumentów –
    dopisanie ich tutaj byłoby drugim źródłem tej samej prawdy (i kolizją z argumentem wywołania,
    patrz :func:`issue_fee_document`).

    Wszystko jest napisem, także kwota: dokument składa się z napisów, a formatowanie liczby
    (przecinek czy kropka, spacja tysięcy) jest decyzją o **treści**, a nie o typie.
    """
    participant = fee.participant
    edition = fee.schedule.edition
    return {
        "edition": edition.year_label,
        "participant_code": participant.public_code,
        "recipient": participant.user.get_full_name() or participant.public_code,
        "school": participant.school or "",
        "date": timezone.localdate().isoformat(),
        "number": fee.reference,
        "amount": f"{fee.amount:.2f}",
        "currency": fee.currency,
        "due_date": fee.due_on.isoformat() if fee.due_on else "",
        "vat_rate": "" if fee.schedule.vat_rate is None else str(fee.schedule.vat_rate),
    }


def _document_version(document) -> str:
    """Wersja złożonego dokumentu: z atrybutu ``version`` albo wprost z napisu.

    Dwie postacie, bo dwóch wołających: T12 odda obiekt złożonego dokumentu, a import
    konfiguracji i test podadzą samą wersję. Obie drogi kończą się tym samym napisem w rejestrze.
    """
    version = document if isinstance(document, str) else getattr(document, "version", "")
    version = (version or "").strip()
    if not version:
        raise DomainError(
            "Dokument rozliczeniowy bez wersji szablonu nie da się odtworzyć.",
            "FEE_DOCUMENT_VERSION_MISSING",
            status.HTTP_409_CONFLICT,
        )
    return version


def record_fee_document(fee: ParticipantFee, document, *, actor=None, request=None) -> ParticipantFee:
    """Zapisuje, że wydano dokument rozliczeniowy, i **zapamiętuje wersję jego szablonu**.

    Hak, a nie integracja: ten moduł nie składa PDF-a, nie zna ``DocumentTemplate`` i nie importuje
    ``apps.tenancy.documents`` (T12 powstaje równolegle). Przyjmuje **gotowy** dokument – obiekt
    z atrybutem ``version`` albo sam napis wersji – i robi z nim jedyną rzecz, która należy do
    rejestru należności: zapisuje, z której wersji tekstu powstał.

    Powód jest ten sam, co przy ``Certificate.template_version``: PDF-a nie przechowujemy, więc
    powstaje przy każdym pobraniu. Bez zapamiętanej wersji poprawka szablonu zmieniłaby treść
    rachunku, który ktoś ma już w segregatorze.
    """
    from apps.core.models import audit

    require_fees(competition_of_fee(fee))
    version = _document_version(document)
    before = fee.document_version
    fee.document_version = version
    fee.document_issued_at = timezone.now()
    fee.full_clean()
    fee.save(update_fields=["document_version", "document_issued_at"])
    audit(
        actor,
        "fee.document_issued",
        fee,
        {"from": before, "to": version, "kind": DOCUMENT_KIND},
        request=request,
    )
    return fee


def issue_fee_document(fee: ParticipantFee, renderer, *, actor=None, request=None):
    """Składa dokument rozliczeniowy **cudzym** składaczem i zapisuje jego wersję.

    ``renderer`` jest wstrzykiwany, a nie importowany – to jest cała granica między tym zadaniem
    a modułem dokumentów (T12). Umowa jest jednozdaniowa: wołamy
    ``renderer(konkurs, "INVOICE", **podstawienia)`` i oczekujemy obiektu z atrybutem ``version``
    (albo samego napisu wersji). Co ten obiekt niesie poza tym – napisy, bajty PDF-a, nazwę pliku
    – należy do wołającego, a nie do rejestru należności.

    Dzięki temu ekran koordynatora (T42) podaje tu ``apps.tenancy.documents.render_document``, test
    podaje zaślepkę, a ten moduł nie ma ani jednego importu, który musiałby czekać na cudze
    wydanie.

    Dokument rozliczeniowy **musi** pochodzić z szablonu: rachunek bez zapamiętanej wersji odpadnie
    na :func:`record_fee_document`. To jest różnica wobec dyplomu, który ma odwrót na układ wbudowany
    – dyplom wyglądał jakoś przed etapem 2, a rachunku nikt dotąd nie wystawiał.
    """
    require_fees(competition_of_fee(fee))
    rendered = renderer(competition_of_fee(fee), DOCUMENT_KIND, **fee_document_context(fee))
    record_fee_document(fee, rendered, actor=actor, request=request)
    return rendered
