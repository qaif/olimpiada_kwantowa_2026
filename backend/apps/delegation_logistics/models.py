"""Logistyka finału stacjonarnego dla delegacji krajowych (docs/tasks/LOG-01.md).

Skąd to się wzięło. Finał olimpiady międzynarodowej (``iqo``) odbywa się na miejscu, a kraje
przyjeżdżają **delegacjami**: uczniowie, opiekunowie drużyn, obserwatorzy i goście. Organizator
potrzebuje od każdej z tych osób danych do zaproszenia wizowego, odbioru z lotniska, przydziału pokoi,
zamówienia posiłków i identyfikatora – i potrzebuje ich w terminie, a po zawodach ma je usunąć.

Dlaczego osobna aplikacja, a nie rozbudowa ``apps.competitions.logistics``. Tamten moduł opisuje
**uczestnika etapu** (deklaracja przyjazdu jednego ucznia na etap krajowy) i jest kluczowany wpisem
do etapu. Tu jednostką jest **osoba w delegacji** – także taka, która nie ma konta (gość), i taka,
która nie jest uczestnikiem (opiekun drużyny). Wspólna jest jedna decyzja: dane o zdrowiu zbieramy
wyłącznie po świadomym włączeniu przez organizatora (D21, ``collects_special_needs``) – i tę decyzję
czytamy stamtąd, a nie powielamy.

Bramka obszaru: flaga konkursu ``onsite_logistics`` (§ 1.5.2) **i** tryb rejestracji ``DELEGATIONS``
(:func:`enabled`). Konkurs bez delegacji nie ma czego tu oglądać – ma swój formularz przyjazdu.

**Dane wrażliwe są szyfrowane w bazie** (:class:`~apps.delegation_logistics.crypto.EncryptedTextField`):
numer i data ważności paszportu, imię i nazwisko z paszportu, data urodzenia, dane o zdrowiu
**łącznie z dietą** (halal, koszerna, bezglutenowa to dane z art. 9 RODO) i kontakt alarmowy. Jawne
są pola, które niczego szczególnego o osobie nie mówią, a po których się liczy: rozmiar koszulki,
daty przylotów, płeć do przydziału pokoi. Liczenie diet idzie w Pythonie po odszyfrowaniu – kilkaset
wierszy jednej edycji.
"""

from __future__ import annotations

import secrets
from datetime import date

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.competitions.scoping import competition_scoped_manager

from .crypto import EncryptedTextField

#: Flaga konkursu z katalogu ``apps.tenancy.models.FEATURE_DEFAULTS`` – ta sama, co logistyki etapu.
FLAG = "onsite_logistics"

#: Domyślna liczba dni po zakończeniu finału, po której dane członków delegacji są usuwane.
#: Trzydzieści dni wystarcza na rozliczenie hotelu, zwrot kosztów podróży i reklamacje po zawodach;
#: dłużej numer paszportu nie ma już żadnego celu (art. 5 ust. 1 lit. e RODO).
DEFAULT_RETENTION_DAYS = 30

#: Długość tokenu identyfikatora (bajty ``token_urlsafe``). 16 bajtów = 128 bitów: token jest
#: w kodzie QR na plastikowej karcie, więc ma być nie do zgadnięcia, ale nie musi być długi.
BADGE_TOKEN_BYTES = 16

#: Wersja tekstu zgody na przetwarzanie danych o zdrowiu – zapisywana przy zgodzie, jak
#: ``ConsentRecord.document_version``. Zmiana treści zgody w szablonie = nowa wersja tutaj.
HEALTH_CONSENT_VERSION = "1.0"


def enabled(competition) -> bool:
    """Czy ten konkurs prowadzi logistykę finału dla delegacji. **Jedyne** wejście do bramki obszaru.

    Oba warunki są polami wczytanego wiersza konkursu – odczyt nie pyta bazy, więc menu panelu
    i pasek konta Olimpiady Kwantowej nie płacą za tę funkcję ani jednego zapytania.
    """
    return competition is not None and competition.uses_delegations and competition.has_feature(FLAG)


def new_badge_token() -> str:
    return secrets.token_urlsafe(BADGE_TOKEN_BYTES)


class FieldGroup(models.TextChoices):
    """Grupy pól formularza – jednostka terminu, blokady i przypomnienia."""

    IDENTITY = "IDENTITY", _("Dokument podróży")
    TRAVEL = "TRAVEL", _("Przyjazd i wyjazd")
    ACCOMMODATION = "ACCOMMODATION", _("Zakwaterowanie")
    HEALTH = "HEALTH", _("Wyżywienie i zdrowie")
    PERSONAL = "PERSONAL", _("Identyfikator i kontakt alarmowy")


class AccessRole(models.TextChoices):
    """Przydział dostępu do danych logistyki – ponad rolę koordynatora (LOG-01 § 2)."""

    OFFICER = "OFFICER", "oficer logistyki (pełny wgląd)"
    CHECKIN = "CHECKIN", "obsługa rejestracji (skanowanie identyfikatorów)"


class MemberKind(models.TextChoices):
    STUDENT = "STUDENT", _("Uczeń")
    LEADER = "LEADER", _("Opiekun drużyny")
    GUEST = "GUEST", _("Gość")


class GuestRole(models.TextChoices):
    """Rola osoby bez konta w delegacji. Rola trafia na identyfikator, więc jest listą zamkniętą."""

    OBSERVER = "OBSERVER", _("Obserwator")
    GUEST = "GUEST", _("Gość")


class TravelMode(models.TextChoices):
    FLIGHT = "FLIGHT", _("Samolot")
    TRAIN = "TRAIN", _("Pociąg")
    BUS = "BUS", _("Autobus")
    CAR = "CAR", _("Samochód")
    OTHER = "OTHER", _("Inny")


class Gender(models.TextChoices):
    """Płeć – wyłącznie do przydziału pokoi (zasady zakwaterowania niepełnoletnich)."""

    FEMALE = "F", _("Kobieta")
    MALE = "M", _("Mężczyzna")
    OTHER = "X", _("Inna / wolę nie podawać")


class RoomGender(models.TextChoices):
    FEMALE = "F", "kobiety"
    MALE = "M", "mężczyźni"
    ANY = "ANY", "dowolna (tylko dorośli)"


class Diet(models.TextChoices):
    """Dieta – lista zamknięta, bo po niej kuchnia **liczy** posiłki."""

    NONE = "NONE", _("Bez ograniczeń")
    VEGETARIAN = "VEGETARIAN", _("Wegetariańska")
    VEGAN = "VEGAN", _("Wegańska")
    HALAL = "HALAL", _("Halal")
    KOSHER = "KOSHER", _("Koszerna")
    GLUTEN_FREE = "GLUTEN_FREE", _("Bezglutenowa")
    OTHER = "OTHER", _("Inna (opis w uwagach)")


class TshirtSize(models.TextChoices):
    XS = "XS", "XS"
    S = "S", "S"
    M = "M", "M"
    L = "L", "L"
    XL = "XL", "XL"
    XXL = "XXL", "XXL"
    XXXL = "XXXL", "3XL"


class ScanStatus(models.TextChoices):
    """Wynik skanu zdjęcia – te same wartości, co ``student_status.ScanStatus`` (kopia, nie import)."""

    PENDING = "PENDING", "oczekuje na skan"
    CLEAN = "CLEAN", "czysty"
    INFECTED = "INFECTED", "zainfekowany"
    ERROR = "ERROR", "błąd skanu"


class LetterScope(models.TextChoices):
    PERSON = "PERSON", "imienny"
    DELEGATION = "DELEGATION", "dla delegacji"


class LetterRequestStatus(models.TextChoices):
    """Stan wniosku opiekuna o list zapraszający (VISA-01 § 3). Etykiety widzi też opiekun – gettext."""

    PENDING = "PENDING", _("oczekuje na decyzję")
    APPROVED = "APPROVED", _("zatwierdzony – list wystawiony")
    REJECTED = "REJECTED", _("odrzucony")
    WITHDRAWN = "WITHDRAWN", _("wycofany")


def new_verification_code() -> str:
    """Losowy kod weryfikacyjny listu – alfabet i długość kodów dyplomów (bez 0/O, 1/I/L).

    Losowy, a nie wyliczony z numeru: numer listu jest kolejny i jawny (stoi na papierze i w rejestrze
    pism), więc z niego dałoby się zgadnąć kody cudzych listów i obejrzeć nazwiska na stronie weryfikacji.
    """
    from apps.results.models import generate_verification_code

    return generate_verification_code()


# --- konfiguracja --------------------------------------------------------------------------------


class LogisticsAccess(models.Model):
    """Przydział dostępu do danych logistyki finału w jednym konkursie.

    Rola koordynatora **nie wystarcza** do oglądania paszportów i danych o zdrowiu: koordynatorów
    bywa kilkunastu (komitet, sekretariat, informatyk), a te dane są potrzebne dwóm osobom
    organizującym pobyt. Przydział jest więc jawną, audytowaną decyzją – minimalizacja kręgu
    odbiorców (art. 5 ust. 1 lit. c i f RODO), a nie tylko ukrycie przycisku.
    """

    competition = models.ForeignKey(
        "tenancy.Competition", on_delete=models.CASCADE, related_name="+", verbose_name="konkurs"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="logistics_access",
        verbose_name="konto",
    )
    role = models.CharField("przydział", max_length=16, choices=AccessRole.choices)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    granted_at = models.DateTimeField("nadano", default=timezone.now)

    objects = competition_scoped_manager()

    class Meta:
        verbose_name = "dostęp do logistyki finału"
        verbose_name_plural = "dostępy do logistyki finału"
        ordering = ("competition", "role", "granted_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["competition", "user", "role"], name="delegation_logistics_access_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}: {self.role}"


class FinalEvent(models.Model):
    """Finał jednej edycji: daty, miejsce, terminy grup pól, retencja i numeracja listów.

    Jeden na edycję, zakładany przy pierwszym zapisie ustawień. Brak wiersza znaczy „finał jeszcze
    nieopisany” – formularz opiekuna działa (bez terminów), ale list wizowy wymaga dat i miejsca.
    """

    competition = models.ForeignKey(
        "tenancy.Competition", on_delete=models.CASCADE, related_name="+", verbose_name="konkurs"
    )
    edition = models.OneToOneField(
        "competitions.Edition", on_delete=models.CASCADE, related_name="final_event", verbose_name="edycja"
    )
    name = models.CharField("nazwa wydarzenia", max_length=200, blank=True)
    city = models.CharField("miasto", max_length=120, blank=True)
    venue = models.CharField("miejsce (adres)", max_length=255, blank=True)
    starts_on = models.DateField("pierwszy dzień", null=True, blank=True)
    ends_on = models.DateField("ostatni dzień", null=True, blank=True)
    retention_days = models.PositiveSmallIntegerField(
        "usunięcie danych po zakończeniu (dni)", default=DEFAULT_RETENTION_DAYS
    )
    letter_prefix = models.CharField("prefiks numeru listów", max_length=20, blank=True)
    deadline_identity = models.DateTimeField("termin: dokument podróży", null=True, blank=True)
    deadline_travel = models.DateTimeField("termin: przyjazd i wyjazd", null=True, blank=True)
    deadline_accommodation = models.DateTimeField("termin: zakwaterowanie", null=True, blank=True)
    deadline_health = models.DateTimeField("termin: wyżywienie i zdrowie", null=True, blank=True)
    deadline_personal = models.DateTimeField("termin: identyfikator i kontakt", null=True, blank=True)
    #: Kiedy automat retencji usunął dane członków. Ustawione = dane edycji już nie istnieją,
    #: a formularz opiekuna jest zamknięty (finał się odbył).
    purged_at = models.DateTimeField("dane usunięte", null=True, blank=True)
    updated_at = models.DateTimeField("zmienione", default=timezone.now)

    objects = competition_scoped_manager()

    class Meta:
        verbose_name = "finał (logistyka)"
        verbose_name_plural = "finały (logistyka)"
        constraints = [
            models.CheckConstraint(
                condition=Q(starts_on__isnull=True)
                | Q(ends_on__isnull=True)
                | Q(ends_on__gte=models.F("starts_on")),
                name="delegation_logistics_event_dates_order",
            ),
        ]

    def __str__(self) -> str:
        return self.name or f"finał edycji {self.edition_id}"

    def deadline_for(self, group: str):
        return getattr(self, f"deadline_{str(group).lower()}", None)

    def purge_due_on(self) -> date | None:
        """Dzień, od którego dane członków są do usunięcia – ``None``, dopóki finał nie ma końca."""
        if self.ends_on is None:
            return None
        from datetime import timedelta

        return self.ends_on + timedelta(days=self.retention_days)


# --- delegacja: goście i członkowie ---------------------------------------------------------------


class DelegationGuest(models.Model):
    """Osoba w delegacji **bez konta**: obserwator albo gość (DEL-01 tej roli nie przewidział).

    Bez konta, bo obserwator nie loguje się do zawodów – przyjeżdża, nocuje, je i nosi identyfikator.
    Dane wpisuje opiekun drużyny; adres e-mail jest opcjonalny i służy wyłącznie organizatorowi
    do kontaktu w sprawach pobytu.
    """

    delegation = models.ForeignKey(
        "accounts.Delegation", on_delete=models.CASCADE, related_name="guests", verbose_name="delegacja"
    )
    first_name = models.CharField("imię", max_length=150)
    last_name = models.CharField("nazwisko", max_length=150)
    email = models.EmailField("adres e-mail", blank=True)
    role = models.CharField("rola", max_length=16, choices=GuestRole.choices, default=GuestRole.OBSERVER)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField("dodany", default=timezone.now)

    objects = competition_scoped_manager("delegation__competition")

    class Meta:
        verbose_name = "gość delegacji"
        verbose_name_plural = "goście delegacji"
        ordering = ("last_name", "first_name", "id")

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name}"


class Room(models.Model):
    """Pokój do przydziału w rooming liście finału."""

    competition = models.ForeignKey(
        "tenancy.Competition", on_delete=models.CASCADE, related_name="+", verbose_name="konkurs"
    )
    edition = models.ForeignKey(
        "competitions.Edition", on_delete=models.CASCADE, related_name="final_rooms", verbose_name="edycja"
    )
    name = models.CharField("numer / nazwa", max_length=60)
    building = models.CharField("budynek / hotel", max_length=120, blank=True)
    capacity = models.PositiveSmallIntegerField("liczba miejsc")
    gender = models.CharField("płeć pokoju", max_length=3, choices=RoomGender.choices)
    note = models.CharField("uwagi", max_length=200, blank=True)

    objects = competition_scoped_manager()

    class Meta:
        verbose_name = "pokój"
        verbose_name_plural = "pokoje"
        ordering = ("building", "name", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["edition", "building", "name"], name="delegation_logistics_room_unique"
            ),
            models.CheckConstraint(condition=Q(capacity__gte=1), name="delegation_logistics_room_capacity"),
        ]

    def __str__(self) -> str:
        return f"{self.building} {self.name}".strip()


class DelegationMember(models.Model):
    """Osoba w delegacji z punktu widzenia finału – uczeń, opiekun albo gość – i jej dane pobytu.

    Dokładnie jedno z trzech powiązań (więz bazy): ``participant`` (uczeń), ``user`` (opiekun
    drużyny), ``guest``. Wiersze uczniów i opiekunów powstają same (``services.sync_members``) –
    lista uczniów należy do DEL-01 i tu jej nie powielamy, tylko dokładamy do niej dane pobytu.
    """

    delegation = models.ForeignKey(
        "accounts.Delegation",
        on_delete=models.CASCADE,
        related_name="logistics_members",
        verbose_name="delegacja",
    )
    kind = models.CharField("rola w delegacji", max_length=16, choices=MemberKind.choices)
    participant = models.ForeignKey(
        "accounts.Participant", on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    guest = models.OneToOneField(
        DelegationGuest, on_delete=models.CASCADE, null=True, blank=True, related_name="member"
    )
    #: Token w kodzie QR identyfikatora. Nie niesie żadnej informacji o osobie; „wydaj nowy
    #: identyfikator” zmienia go, a stara karta przestaje działać przy skanowaniu.
    badge_token = models.CharField(
        "token identyfikatora", max_length=32, unique=True, default=new_badge_token
    )

    # --- 1. dokument podróży (szyfrowane) ---
    passport_name = EncryptedTextField("imię i nazwisko jak w paszporcie", blank=True)
    nationality = models.CharField("obywatelstwo (ISO)", max_length=2, blank=True)
    date_of_birth = EncryptedTextField("data urodzenia", blank=True)
    passport_number = EncryptedTextField("numer paszportu", blank=True)
    passport_expiry = EncryptedTextField("paszport ważny do", blank=True)

    # --- 2. przyjazd i wyjazd ---
    arrival_date = models.DateField("przyjazd – dzień", null=True, blank=True)
    arrival_time = models.TimeField("przyjazd – godzina", null=True, blank=True)
    arrival_mode = models.CharField("przyjazd – środek", max_length=8, choices=TravelMode.choices, blank=True)
    arrival_number = models.CharField("przyjazd – nr lotu / pociągu", max_length=40, blank=True)
    arrival_place = models.CharField("przyjazd – lotnisko / dworzec", max_length=120, blank=True)
    departure_date = models.DateField("wyjazd – dzień", null=True, blank=True)
    departure_time = models.TimeField("wyjazd – godzina", null=True, blank=True)
    departure_mode = models.CharField("wyjazd – środek", max_length=8, choices=TravelMode.choices, blank=True)
    departure_number = models.CharField("wyjazd – nr lotu / pociągu", max_length=40, blank=True)
    departure_place = models.CharField("wyjazd – lotnisko / dworzec", max_length=120, blank=True)

    # --- 3. zakwaterowanie ---
    needs_accommodation = models.BooleanField("potrzebuje noclegu", default=True)
    gender = models.CharField("płeć (przydział pokoi)", max_length=1, choices=Gender.choices, blank=True)
    roommate_preference = models.CharField("preferowany współlokator", max_length=200, blank=True)
    accommodation_notes = models.CharField("uwagi do zakwaterowania", max_length=300, blank=True)
    room = models.ForeignKey(
        Room, on_delete=models.SET_NULL, null=True, blank=True, related_name="occupants", verbose_name="pokój"
    )

    # --- 4. wyżywienie i zdrowie (szyfrowane; tylko przy D21 i ze zgodą) ---
    #: Szyfrowana jak reszta grupy (poprawka po przeglądzie): dieta religijna albo medyczna jest daną
    #: szczególną. Lista wyboru zostaje (etykiety, walidacja); sprawdza ją też serwis.
    diet = EncryptedTextField("dieta", choices=Diet.choices, blank=True)
    diet_notes = EncryptedTextField("uwagi do diety", blank=True)
    allergies = EncryptedTextField("alergie", blank=True)
    medical_notes = EncryptedTextField("uwagi medyczne", blank=True)
    health_consent_at = models.DateTimeField("zgoda na dane o zdrowiu", null=True, blank=True)
    health_consent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    health_consent_version = models.CharField("wersja zgody", max_length=20, blank=True)

    # --- 5. identyfikator i kontakt alarmowy ---
    tshirt_size = models.CharField("rozmiar koszulki", max_length=5, choices=TshirtSize.choices, blank=True)
    emergency_name = EncryptedTextField("kontakt alarmowy – imię i nazwisko", blank=True)
    emergency_phone = EncryptedTextField("kontakt alarmowy – telefon", blank=True)
    #: Klucz zdjęcia w prywatnym storage (``apps.submissions.storage``, prefiks ``final-badges/``).
    #: Pusty = brak zdjęcia. Nazwa pliku od opiekuna nie bierze udziału w kluczu.
    photo_key = models.CharField("zdjęcie – klucz", max_length=300, blank=True)
    photo_mime = models.CharField("zdjęcie – typ", max_length=40, blank=True)
    photo_size = models.PositiveIntegerField("zdjęcie – rozmiar", default=0)
    photo_scan = models.CharField("zdjęcie – skan", max_length=16, choices=ScanStatus.choices, blank=True)
    photo_uploaded_at = models.DateTimeField("zdjęcie – przesłane", null=True, blank=True)

    created_at = models.DateTimeField("utworzony", default=timezone.now)
    updated_at = models.DateTimeField("zmieniony", default=timezone.now)

    objects = competition_scoped_manager("delegation__competition")

    class Meta:
        verbose_name = "członek delegacji (logistyka)"
        verbose_name_plural = "członkowie delegacji (logistyka)"
        ordering = ("delegation", "kind", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["delegation", "participant"],
                condition=Q(participant__isnull=False),
                name="delegation_logistics_member_student_once",
            ),
            models.UniqueConstraint(
                fields=["delegation", "user"],
                condition=Q(user__isnull=False),
                name="delegation_logistics_member_leader_once",
            ),
            # Rola i powiązanie muszą się zgadzać: „uczeń” bez profilu uczestnika albo „gość”
            # z kontem opiekuna byłby wierszem, którego nikt nie umie nazwać na identyfikatorze.
            models.CheckConstraint(
                condition=(
                    Q(kind="STUDENT", participant__isnull=False, user__isnull=True, guest__isnull=True)
                    | Q(kind="LEADER", participant__isnull=True, user__isnull=False, guest__isnull=True)
                    | Q(kind="GUEST", participant__isnull=True, user__isnull=True, guest__isnull=False)
                ),
                name="delegation_logistics_member_kind_link",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.pk} ({self.delegation_id})"

    # --- tożsamość do wyświetlenia ---

    @property
    def account_user(self):
        """Konto osoby (uczeń, opiekun) albo ``None`` (gość)."""
        if self.participant_id:
            return self.participant.user
        return self.user if self.user_id else None

    @property
    def first_name(self) -> str:
        if self.guest_id:
            return self.guest.first_name
        user = self.account_user
        return user.first_name if user is not None else ""

    @property
    def last_name(self) -> str:
        if self.guest_id:
            return self.guest.last_name
        user = self.account_user
        return user.last_name if user is not None else ""

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def role_label(self) -> str:
        """Rola na identyfikatorze: uczeń, opiekun drużyny, obserwator, gość."""
        if self.guest_id:
            return str(GuestRole(self.guest.role).label)
        return str(MemberKind(self.kind).label)

    @property
    def birth_date(self) -> date | None:
        """Data urodzenia z dokumentu podróży, a u ucznia – z profilu, dopóki dokumentu nie ma."""
        if self.date_of_birth:
            try:
                return date.fromisoformat(self.date_of_birth)
            except ValueError:
                return None
        if self.participant_id:
            return self.participant.birth_date
        return None

    def age_on(self, day: date | None) -> int | None:
        born = self.birth_date
        if born is None or day is None:
            return None
        return day.year - born.year - ((day.month, day.day) < (born.month, born.day))

    def is_minor_on(self, day: date | None) -> bool:
        """Niepełnoletni w dniu rozpoczęcia finału. **Brak daty u ucznia = niepełnoletni.**

        Odwrót ostrożny z premedytacją: pomyłka „dorosły” przy niepełnoletnim kładzie dziecko do
        pokoju z obcym dorosłym, pomyłka odwrotna – co najwyżej utrudnia przydział. Opiekun i gość
        bez daty są dorosłymi, bo do tej roli DEL-01 i regulamin wymagają pełnoletności.
        """
        age = self.age_on(day or timezone.localdate())
        if age is None:
            return self.kind == MemberKind.STUDENT
        return age < 18

    @property
    def photo_clean(self) -> bool:
        return bool(self.photo_key) and self.photo_scan == ScanStatus.CLEAN


# --- obecność --------------------------------------------------------------------------------------


class Checkpoint(models.Model):
    """Punkt kontroli obecności: „przyjazd do hotelu”, „ceremonia otwarcia”, „dzień zawodów 1”."""

    competition = models.ForeignKey(
        "tenancy.Competition", on_delete=models.CASCADE, related_name="+", verbose_name="konkurs"
    )
    edition = models.ForeignKey(
        "competitions.Edition",
        on_delete=models.CASCADE,
        related_name="final_checkpoints",
        verbose_name="edycja",
    )
    name = models.CharField("nazwa", max_length=120)
    position = models.PositiveSmallIntegerField("kolejność", default=0)

    objects = competition_scoped_manager()

    class Meta:
        verbose_name = "punkt kontroli obecności"
        verbose_name_plural = "punkty kontroli obecności"
        ordering = ("position", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["edition", "name"], name="delegation_logistics_checkpoint_unique"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class CheckIn(models.Model):
    """Odhaczenie osoby w punkcie kontroli – jedno na parę (punkt, osoba)."""

    checkpoint = models.ForeignKey(Checkpoint, on_delete=models.CASCADE, related_name="check_ins")
    member = models.ForeignKey(DelegationMember, on_delete=models.CASCADE, related_name="check_ins")
    at = models.DateTimeField("odhaczono", default=timezone.now)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    objects = competition_scoped_manager("checkpoint__competition")

    class Meta:
        verbose_name = "odhaczenie"
        verbose_name_plural = "odhaczenia"
        ordering = ("-at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=["checkpoint", "member"], name="delegation_logistics_checkin_once"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.member_id} @ {self.checkpoint_id}"


# --- listy zapraszające (wizy) --------------------------------------------------------------------


class InvitationLetter(models.Model):
    """Wpis rejestru listów zapraszających do wizy – numer, adresat, kto i kiedy wystawił.

    PDF nie jest przechowywany: powstaje przy pobraniu z **migawki** (``content``) – danych osób
    z chwili wystawienia, zaszyfrowanych. Migawka, a nie bieżące dane, bo list jest dokumentem:
    konsulat ma dostać ten sam papier, który wystawiliśmy, także gdy opiekun poprawi później
    numer paszportu (wtedy wystawia się **nowy** list z nowym numerem). Po terminie retencji
    migawka znika, a wiersz rejestru (numer, kraj, data, liczba osób) zostaje.
    """

    competition = models.ForeignKey(
        "tenancy.Competition", on_delete=models.CASCADE, related_name="+", verbose_name="konkurs"
    )
    edition = models.ForeignKey(
        "competitions.Edition", on_delete=models.CASCADE, related_name="+", verbose_name="edycja"
    )
    number = models.CharField("numer", max_length=40)
    year = models.PositiveSmallIntegerField("rok")
    sequence = models.PositiveIntegerField("numer kolejny")
    scope = models.CharField("rodzaj", max_length=16, choices=LetterScope.choices)
    delegation = models.ForeignKey(
        "accounts.Delegation", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    member = models.ForeignKey(
        DelegationMember, on_delete=models.SET_NULL, null=True, blank=True, related_name="letters"
    )
    country_name = models.CharField("kraj", max_length=120, blank=True)
    people_count = models.PositiveSmallIntegerField("liczba osób", default=0)
    #: JSON z danymi osób z chwili wystawienia – zaszyfrowany. Pusty po retencji.
    content = EncryptedTextField("migawka treści", blank=True)
    content_purged_at = models.DateTimeField("migawka usunięta", null=True, blank=True)
    template_version = models.CharField("wersja szablonu", max_length=100, blank=True)
    issued_at = models.DateTimeField("wystawiono", default=timezone.now)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    # --- VISA-01: język, weryfikacja publiczna i unieważnienie ---
    #: Kod ze strony weryfikacji (``/visa/verify/<kod>/``) – losowy, nie numer. ``null`` dopuszczone
    #: wyłącznie dla wierszy sprzed VISA-01 (migracja nadaje im kody); każdy nowy list dostaje kod
    #: w ``letters.issue_letter``. Unikalny globalnie, choć strona szuka w obrębie konkursu.
    verification_code = models.CharField(
        "kod weryfikacyjny", max_length=12, unique=True, null=True, blank=True, editable=False
    )
    #: Język tekstu listu (``letter_texts.LETTER_TEXTS``). Angielski – język olimpiady i konsulatów.
    language = models.CharField("język listu", max_length=10, default="en")
    #: Migawka wydarzenia z chwili wystawienia – strona weryfikacji pokazuje to, co stoi na papierze,
    #: a nie dzisiejsze ustawienia finału (przesunięcie dat po wystawieniu listu nie może sprawić, że
    #: konsulat zobaczy inne daty niż w dokumencie). Bez danych osobowych, więc bez szyfrowania.
    event_name = models.CharField("wydarzenie (migawka)", max_length=200, blank=True)
    event_city = models.CharField("miasto (migawka)", max_length=120, blank=True)
    event_starts_on = models.DateField("pierwszy dzień (migawka)", null=True, blank=True)
    event_ends_on = models.DateField("ostatni dzień (migawka)", null=True, blank=True)
    revoked_at = models.DateTimeField("unieważniono", null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    #: Powód unieważnienia – dla organizatora i opiekuna, **nie** na stronie weryfikacji (wolny tekst
    #: bywa opisem osoby: „odmowa wizy”, „zmiana paszportu”).
    revoke_reason = models.CharField("powód unieważnienia", max_length=300, blank=True)

    objects = competition_scoped_manager()

    class Meta:
        verbose_name = "list zapraszający"
        verbose_name_plural = "listy zapraszające"
        ordering = ("-year", "-sequence")
        constraints = [
            models.UniqueConstraint(
                fields=["competition", "number"], name="delegation_logistics_letter_number"
            ),
            models.UniqueConstraint(
                fields=["competition", "year", "sequence"], name="delegation_logistics_letter_sequence"
            ),
        ]

    def __str__(self) -> str:
        return self.number

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def display_code(self) -> str:
        """Kod w grupach po cztery znaki (``ABCD-EFGH-JKMN``) – tak, jak stoi na papierze."""
        code = self.verification_code or ""
        return "-".join(code[i : i + 4] for i in range(0, len(code), 4))


class LetterRequest(models.Model):
    """Wniosek opiekuna drużyny o list imienny dla członka delegacji (VISA-01 § 3).

    Wniosek **nie kopiuje** danych paszportowych: wskazuje osobę, a list przy zatwierdzeniu bierze jej
    dane z wiersza członka (świeży odczyt w ``letters.issue_letter``) i zamraża je w migawce listu.
    Dzięki temu wniosek nie jest trzecim miejscem numeru paszportu – i znika razem z osobą
    (``CASCADE`` po członku: retencja finału, wypisanie z delegacji, usunięcie konta).
    """

    delegation = models.ForeignKey(
        "accounts.Delegation", on_delete=models.CASCADE, related_name="+", verbose_name="delegacja"
    )
    member = models.ForeignKey(
        DelegationMember, on_delete=models.CASCADE, related_name="letter_requests", verbose_name="osoba"
    )
    language = models.CharField("język listu", max_length=10, default="en")
    status = models.CharField(
        "stan", max_length=16, choices=LetterRequestStatus.choices, default=LetterRequestStatus.PENDING
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    requested_at = models.DateTimeField("złożony", default=timezone.now)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    decided_at = models.DateTimeField("rozstrzygnięty", null=True, blank=True)
    reject_reason = models.CharField("powód odrzucenia", max_length=500, blank=True)
    letter = models.ForeignKey(
        InvitationLetter, on_delete=models.SET_NULL, null=True, blank=True, related_name="requests"
    )

    objects = competition_scoped_manager("delegation__competition")

    class Meta:
        verbose_name = "wniosek o list zapraszający"
        verbose_name_plural = "wnioski o listy zapraszające"
        ordering = ("-requested_at", "-id")
        constraints = [
            # Jeden **oczekujący** wniosek na osobę: drugie kliknięcie „Poproś o list” (albo dwóch
            # opiekunów jednej drużyny naraz) nie może dać oficerowi dwóch wierszy do rozstrzygnięcia,
            # z których zatwierdzenie obu wystawiłoby dwa listy.
            models.UniqueConstraint(
                fields=["member"],
                condition=Q(status="PENDING"),
                name="delegation_logistics_letter_request_one_pending",
            ),
        ]

    def __str__(self) -> str:
        return f"wniosek {self.pk} ({self.member_id}, {self.status})"

    @property
    def is_pending(self) -> bool:
        return self.status == LetterRequestStatus.PENDING


class LogisticsReminder(models.Model):
    """Ślad wysłanego przypomnienia o brakach – żeby koordynator widział, kiedy ostatnio pisał."""

    delegation = models.ForeignKey(
        "accounts.Delegation", on_delete=models.CASCADE, related_name="+", verbose_name="delegacja"
    )
    sent_at = models.DateTimeField("wysłano", default=timezone.now)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    missing_count = models.PositiveSmallIntegerField("braki", default=0)
    recipients = models.PositiveSmallIntegerField("adresaci", default=0)

    objects = competition_scoped_manager("delegation__competition")

    class Meta:
        verbose_name = "przypomnienie o brakach"
        verbose_name_plural = "przypomnienia o brakach"
        ordering = ("-sent_at", "-id")

    def __str__(self) -> str:
        return f"przypomnienie {self.delegation_id} ({self.sent_at:%Y-%m-%d})"
