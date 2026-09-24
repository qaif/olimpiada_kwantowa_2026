"""Model użytkownika (e-mail jako login), profile ról i kody zaproszeń.

Zasady:
- kod zaproszenia nigdy nie jest przechowywany jawnie – w bazie jest wyłącznie sha256,
- czas zawsze przez ``django.utils.timezone.now()``,
- dostęp do danych wyłącznie przez ORM.
"""

import hashlib
import re
import secrets
from datetime import date

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from apps.core.text import fold as _fold
from apps.tenancy.managers import CompetitionScopedManager, CompetitionScopedQuerySet

from .consents import ConsentKind, ConsentSource

# Grupy RBAC tworzone migracją danych (apps/accounts/migrations/0002_rbac_groups.py).
GROUP_PARTICIPANT = "participant"
GROUP_REVIEWER = "reviewer"
GROUP_APPEALS = "appeals"
GROUP_COORDINATOR = "coordinator"
#: Opiekun szkolny – nauczyciel, który zgłosił swoich uczniów i śledzi ich przebieg w zawodach.
#: Rola jest **wyłącznie do odczytu**: opiekun nie ocenia, nie widzi punktów przed publikacją
#: i niczego nie zmienia poza własnym potwierdzeniem udziału szkoły.
GROUP_SUPERVISOR = "supervisor"
RBAC_GROUPS = (
    GROUP_PARTICIPANT,
    GROUP_REVIEWER,
    GROUP_APPEALS,
    GROUP_COORDINATOR,
    GROUP_SUPERVISOR,
)


class CompetitionRole(models.TextChoices):
    """Rola człowieka **w jednym konkursie** – odpowiednik grupy Django, ale z właścicielem.

    Grupa Django odpowiada na pytanie „czy ta osoba jest recenzentem w tej instalacji”. W bazie
    wielokonkursowej to pytanie jest za szerokie: recenzent Olimpiady Kwantowej nie ma być
    recenzentem olimpiady fizycznej tylko dlatego, że obie stoją na jednym serwerze. ``Membership``
    dokłada brakujący wymiar, a ta lista nazywa role, które w nim występują.

    **Wartości są identyczne z ``GROUP_*``** i to nie jest zbieg okoliczności, tylko warunek
    taniego przejścia: backfill członkostw z ``auth_user_groups`` jest dzięki temu przepisaniem
    kolumny, a nie mapowaniem nazw, którego pierwsza literówka odcięłaby komuś rolę. Kontrolę tej
    zgodności robi asercja niżej – w module, a nie w teście, bo rozjazd ma zatrzymać start
    aplikacji, a nie dopiero przebieg pakietu testów.
    """

    PARTICIPANT = GROUP_PARTICIPANT, "uczestnik"
    REVIEWER = GROUP_REVIEWER, "recenzent"
    APPEALS = GROUP_APPEALS, "komisja odwoławcza"
    COORDINATOR = GROUP_COORDINATOR, "koordynator"
    SUPERVISOR = GROUP_SUPERVISOR, "opiekun szkolny"


# Grupy Django **zostają** (patrz docstring ``Membership``), więc obie listy muszą opisywać ten sam
# zbiór ról. Gdyby kiedyś rozeszły się o jedną pozycję, backfill po cichu pominąłby tę rolę.
assert set(CompetitionRole.values) == set(RBAC_GROUPS), (
    "Role konkursu i grupy RBAC muszą opisywać ten sam zbiór ról."
)

# Alfabet bez znaków mylących (0/O, 1/I/L) – kod bywa przepisywany ręcznie z listy wyników.
PUBLIC_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
#: Prefiks **zastępczy**: właściwy nosi ``tenancy.Competition.public_code_prefix``. Stała zostaje,
#: bo jest odpowiedzią na pytanie „a jeżeli konkursu nie wiadomo” – profil zakładany poza żądaniem
#: i bez wskazania konkursu (komenda, import, ``default`` kolumny) musi dostać jakiś kod, a jedyną
#: wartością, która niczego nie zmienia w zastanej bazie, jest ta, którą ta baza miała dotąd.
#: Konkurs #1 ma w polu dokładnie tę samą wartość, więc obie drogi dają ten sam kod.
PUBLIC_CODE_PREFIX = "OLM-"
PUBLIC_CODE_RANDOM_LENGTH = 6

# Klasa uczestnika. Pięć roczników, bo tyle trwa najdłuższa szkoła ponadpodstawowa (technikum);
# liceum kończy się na czwartej, a szkoła branżowa I stopnia na trzeciej – węższych limitów
# nie narzucamy, bo zależą od typu szkoły, a ten bywa wpisany ręcznie i nie da się go sprawdzić.
MIN_GRADE = 1
MAX_GRADE = 5
GRADE_CHOICES = [(number, str(number)) for number in range(MIN_GRADE, MAX_GRADE + 1)]

#: Najwcześniejsza data urodzenia przyjmowana przez formularze, API i model. Nie jest to reguła
#: wieku, tylko **sito na literówki**: „1089” w polu roku ma wrócić jako błąd pola, a nie wjechać
#: do bazy i rozstrzygnąć o zgodzie opiekuna. Górną granicą jest zawsze dzień dzisiejszy.
MIN_BIRTH_DATE = date(1900, 1, 1)

#: Rocznik wpisywany, gdy wieku **nie znamy** – czyli w konkursie, którego profil rejestracji
#: o datę urodzenia nie pyta (``RegistrationProfile.require_birth_year`` odznaczone).
#: ``Participant.birth_year`` jest kolumną ``NOT NULL`` i taką zostaje, więc „nie wiem” musi mieć
#: swoją wartość; zero jest jedyną, której nikt nie weźmie za rocznik. Reguła wieku rozumie ją bez
#: żadnego dodatkowego warunku: ``consents.is_minor`` na fałszywym roczniku odpowiada „małoletni”,
#: czyli tak, jak ma odpowiadać przy nieznanym wieku. W eksportach i w odpowiedziach API zero
#: wychodzi jako pusta wartość (:attr:`Participant.known_birth_year`), a nie jako liczba.
UNKNOWN_BIRTH_YEAR = 0


def generate_public_code(competition=None) -> str:
    """Losowy, anonimowy identyfikator uczestnika w formacie ``<prefiks>XXXXXX``.

    Prefiks jest własnością **konkursu** (``tenancy.Competition.public_code_prefix``), bo kod
    publiczny jest identyfikatorem w tabelach wyników jednego konkursu i bywa przepisywany ręcznie
    (``docs/UNIWERSALNY-ETAP-1.md`` § 3.3). Brak konkursu – i puste pole – schodzą do
    ``PUBLIC_CODE_PREFIX``, czyli do wartości sprzed wielokonkursowości.

    Argument jest opcjonalny, bo ta funkcja jest też ``default`` kolumny ``public_code``: Django
    woła ``default`` bez argumentów i nie ma skąd znać konkursu wiersza. Kod nadany tą drogą
    poprawia dopiero serwis (``create_participant_with_public_code``), który konkurs zna.
    """
    prefix = getattr(competition, "public_code_prefix", "") or PUBLIC_CODE_PREFIX
    suffix = "".join(secrets.choice(PUBLIC_CODE_ALPHABET) for _ in range(PUBLIC_CODE_RANDOM_LENGTH))
    return f"{prefix}{suffix}"


def hash_invitation_code(plain_code: str) -> str:
    """sha256 kodu zaproszenia. Jedyna postać kodu, jaka trafia do bazy."""
    return hashlib.sha256(plain_code.strip().encode("utf-8")).hexdigest()


class Voivodeship(models.TextChoices):
    """Zamknięta lista 16 województw – jedyne dopuszczalne wartości pól ``district``.

    Wartość jest slugiem ASCII, a nie nazwą z diakrytykami, bo trafia do adresów URL, filtrów
    administracji, kluczy grupowania wyników i wpisów audytu – w każdym z tych miejsc „łódzkie”
    zapisane raz jako ``łódzkie``, a raz jako ``lodzkie`` znaczyłoby dwa różne województwa.
    Człowiekowi pokazujemy etykietę (``get_district_display``), która diakrytyki ma.
    """

    DOLNOSLASKIE = "dolnoslaskie", "dolnośląskie"
    KUJAWSKO_POMORSKIE = "kujawsko-pomorskie", "kujawsko-pomorskie"
    LUBELSKIE = "lubelskie", "lubelskie"
    LUBUSKIE = "lubuskie", "lubuskie"
    LODZKIE = "lodzkie", "łódzkie"
    MALOPOLSKIE = "malopolskie", "małopolskie"
    MAZOWIECKIE = "mazowieckie", "mazowieckie"
    OPOLSKIE = "opolskie", "opolskie"
    PODKARPACKIE = "podkarpackie", "podkarpackie"
    PODLASKIE = "podlaskie", "podlaskie"
    POMORSKIE = "pomorskie", "pomorskie"
    SLASKIE = "slaskie", "śląskie"
    SWIETOKRZYSKIE = "swietokrzyskie", "świętokrzyskie"
    WARMINSKO_MAZURSKIE = "warminsko-mazurskie", "warmińsko-mazurskie"
    WIELKOPOLSKIE = "wielkopolskie", "wielkopolskie"
    ZACHODNIOPOMORSKIE = "zachodniopomorskie", "zachodniopomorskie"


# Prefiks „województwo …” / „woj. …” bywa wpisywany razem z nazwą; dla dopasowania jest szumem.
_VOIVODESHIP_PREFIX_RE = re.compile(r"^(wojewodztwo|woj\.?)\s+")


# Klucz → wartość slug. Etykiety po złożeniu diakrytyków dają dokładnie slug, więc mapa jest
# zbudowana z samych wartości; formy przymiotnikowe męskie obsługuje ``normalize_voivodeship``.
_VOIVODESHIPS_BY_FOLDED = {_fold(value): value for value in Voivodeship.values}


def normalize_voivodeship(text: str | None) -> str | None:
    """Sprowadza dowolny zapis województwa do wartości z ``Voivodeship`` albo zwraca ``None``.

    Pole ``district`` przez kilka wersji było zwykłym tekstem, więc w bazie i w starych
    integracjach współistnieją zapisy „Mazowieckie”, „mazowiecki”, „woj. mazowieckie”
    i „MAZOWIECKIE”. Zamiast odrzucać je wszystkie (co zablokowałoby migrację produkcji
    i rejestrację przez API klientów, które jeszcze nie znają listy), tłumaczymy je na
    jedną wartość kanoniczną. ``None`` oznacza „nie umiem tego przypisać” i jest sygnałem
    dla wołającego, żeby zgłosić błąd walidacji – nigdy nie zgadujemy.

    Rozpoznajemy: wartość slug (``mazowieckie``), etykietę z diakrytykami (``łódzkie``),
    formę przymiotnikową męską używaną wcześniej w danych (``mazowiecki`` → ``mazowieckie``,
    stąd druga próba z doklejonym ``e``) oraz prefiks „województwo”/„woj.”.
    """
    if not text:
        return None
    folded = _VOIVODESHIP_PREFIX_RE.sub("", _fold(str(text)).strip()).strip()
    if not folded:
        return None
    return _VOIVODESHIPS_BY_FOLDED.get(folded) or _VOIVODESHIPS_BY_FOLDED.get(f"{folded}e")


class RegionLevel(models.TextChoices):
    """Lista zamknięta z rozmysłem.

    To nie jest hierarchia dowolnej głębokości, tylko trzy poziomy, które organizatorzy naprawdę
    rozróżniają. Drzewo bez ograniczenia głębokości znaczyłoby, że formularz rejestracji musi
    umieć pokazać nieznaną z góry liczbę list rozwijanych – a żaden organizator o to nie prosił.
    """

    COUNTRY = "COUNTRY", "kraj"
    REGION = "REGION", "region"  # województwo, stan, land, okręg
    COUNTY = "COUNTY", "podregion"  # powiat, dystrykt


class RegionQuerySet(CompetitionScopedQuerySet):
    """Queryset regionów. Ścieżka do konkursu jest domyślna – region ma własną kolumnę."""

    def active(self):
        """Regiony, które wolno pokazać człowiekowi do wyboru.

        Jedno miejsce na tę regułę, bo pytają o nią formularz rejestracji, formularz komitetu
        i filtry panelu koordynatora – trzy listy, które muszą podawać ten sam zestaw.
        """
        return self.filter(is_active=True)


class Region(models.Model):
    """Jednostka podziału terytorialnego **konkursu**.

    Podział jest danymi konkursu, a nie instalacji: olimpiada ogólnopolska dzieli się na 16
    województw, olimpiada uczelniana na 5 okręgów akademickich, a konkurs międzynarodowy na kraje.
    Do etapu 2 podział był stałą w kodzie (``Voivodeship``) wspólną dla całej platformy.

    ``Voivodeship`` **zostaje bez zmian** i zostaje jedynym źródłem wartości pól ``district``.
    Region jest warstwą obok, nie zamiast: dopóki konkurs nie włączy flagi ``custom_regions``,
    formularze, zapytania i strony czytają ``district`` dokładnie tak, jak dziś (§ 1.4.2), a wiersze
    tej tabeli – wpisane migracją ``accounts.0026`` jako odwzorowanie szesnastu województw – leżą
    nieużywane. Ich zgodność z listą województw pilnuje
    ``apps/accounts/tests/test_regions.py::test_regions_mirror_the_voivodeship_list``.
    """

    #: ``CASCADE``, tak jak przy ``ConsentDefinition`` i ``DocumentTemplate``, a nie ``PROTECT`` jak
    #: przy ``Participant`` i ``CommitteeMember``. Granica biegnie po tym, czyje dane niesie wiersz:
    #: profil jest **cudzymi** danymi i konkursu z nim skasować nie wolno, a region jest słownikiem
    #: **samego konkursu**. ``PROTECT`` znaczyłby, że migracja ``0026`` – zakładająca zestaw
    #: startowy **każdemu** konkursowi – uczyniła każdy konkurs nieusuwalnym, także ten założony
    #: omyłkowo przez kreator ``/setup/``. Uczestnicy nie zniknęliby po cichu razem z regionami:
    #: ``Participant.region`` i ``CommitteeMember.region`` zostają ``PROTECT``, więc konkurs
    #: z profilami zatrzyma się na nich – czyli tam, gdzie stoją cudze dane.
    competition = models.ForeignKey(
        "tenancy.Competition",
        verbose_name="konkurs",
        on_delete=models.CASCADE,
        related_name="regions",
    )
    #: Kod jest **ASCII-owym slugiem** – przepisane wprost z ``Voivodeship`` i z tego samego
    #: powodu: trafia do adresów, filtrów administracji, kluczy grupowania wyników i wpisów
    #: audytu. Dla szesnastu województw kod jest **dosłownie** dzisiejszą wartością ``district``,
    #: więc backfill jest złączeniem po kolumnie, a nie ręcznie przepisaną mapą.
    code = models.SlugField("kod", max_length=40)
    name = models.CharField("nazwa", max_length=120)
    level = models.CharField("poziom", max_length=16, choices=RegionLevel.choices, default=RegionLevel.REGION)
    #: ``CASCADE``, choć § 1.4.2 pisze ``PROTECT``: poddrzewo bez korzenia nie jest drzewem, a przy
    #: ``PROTECT`` **każde** kasowanie konkursu kończyłoby się wyjątkiem – szesnaście województw
    #: wskazuje na kraj ``pl``, a kolektor Django nie zwalnia z ``PROTECT`` wierszy kasowanych w tej
    #: samej partii. Bezpieczeństwo niesie tu warstwa niżej i niesie je lepiej: ``Participant.region``
    #: i ``CommitteeMember.region`` są ``PROTECT``, więc region, który komuś przypisano, zatrzyma
    #: kasowanie razem z całym poddrzewem nad sobą.
    parent = models.ForeignKey(
        "self",
        verbose_name="nadrzędny",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
    )
    position = models.PositiveSmallIntegerField("kolejność", default=0)
    #: Wycofanie regionu z listy jest przestawieniem tej flagi, a nie skasowaniem wiersza: profile
    #: z lat poprzednich mają dalej wskazywać region, w którym wtedy startowały.
    is_active = models.BooleanField("aktywny", default=True)
    #: Region „poza Polską” ma tu ``False``: dwóch uczestników z zagranicy nie jest ze sobą
    #: w konflikcie z tytułu miejsca zamieszkania.
    counts_for_conflict = models.BooleanField("liczy się do konfliktu", default=True)

    #: Własna kolumna konkursu – domyślna ścieżka queryseta.
    objects = models.Manager.from_queryset(RegionQuerySet)()

    class Meta:
        verbose_name = "region"
        verbose_name_plural = "regiony"
        ordering = ("competition", "position", "name", "id")
        constraints = [
            # Kod identyfikuje region **w konkursie**, nie na platformie: dwie olimpiady mogą mieć
            # region ``mazowieckie`` i nie jest to ten sam wiersz.
            models.UniqueConstraint(fields=["competition", "code"], name="accounts_region_unique_code"),
        ]

    def __str__(self) -> str:
        return self.name


def region_for_district(competition, district: str | None) -> Region | None:
    """Region konkursu odpowiadający wartości ``district`` albo ``None``.

    Jedyna droga od starego słownika do nowego – wołają ją rejestracja, import grupowy uczniów
    i przydział recenzentów, żeby profil zapisany z samym województwem dostał też region, a profil
    zapisany z regionem dał się porównać z profilem sprzed flagi.

    Dopasowanie idzie po **kodzie**, bo kody szesnastu regionów startowych są dosłownie wartościami
    ``Voivodeship`` (migracja ``accounts.0026``). Najpierw próbujemy napisu **takiego, jaki
    przyszedł** – konkurs z własnym podziałem ma kody spoza listy województw i jego wartość ma
    wygrywać z każdym domysłem. Dopiero potem idzie w ruch :func:`normalize_voivodeship`, żeby
    „woj. Mazowieckie” z dawnych danych i ze starych integracji trafiło tam, gdzie trafia dziś.

    ``None`` znaczy „nie umiem tego przypisać” i nigdy nie zgadujemy – tak samo jak
    :func:`normalize_voivodeship`.
    """
    if competition is None:
        return None
    raw = (district or "").strip()
    if not raw:
        return None
    codes = [raw] + [code for code in (normalize_voivodeship(raw),) if code and code != raw]
    found = {
        region.code: region for region in Region.objects.for_competition(competition).filter(code__in=codes)
    }
    for code in codes:
        if code in found:
            return found[code]
    return None


class UserManager(BaseUserManager):
    """Manager użytkownika logującego się adresem e-mail."""

    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra_fields):
        if not email:
            raise ValueError("Adres e-mail jest wymagany.")
        user = self.model(email=self.normalize_email(email).lower(), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superużytkownik musi mieć is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superużytkownik musi mieć is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """Konto w systemie. Loginem jest adres e-mail, hasło trzymane jest wyłącznie jako hash."""

    email = models.EmailField("adres e-mail", unique=True)
    first_name = models.CharField("imię", max_length=150, blank=True)
    last_name = models.CharField("nazwisko", max_length=150, blank=True)
    is_active = models.BooleanField("aktywne", default=True)
    is_staff = models.BooleanField("dostęp do panelu admina", default=False)
    date_joined = models.DateTimeField("data rejestracji", default=timezone.now)
    # Moment potwierdzenia adresu e-mail kliknięciem w link aktywacyjny (albo ręcznie przez
    # koordynatora). ``None`` znaczy „adres jeszcze niepotwierdzony” i jest jedynym warunkiem
    # jednorazowości linku: aktywacja odmawia, gdy pole jest już wypełnione
    # (``apps.accounts.activation``). Oddzielne od ``is_active``, bo ``is_active=False`` niesie też
    # inne znaczenie – konto zablokowane przez organizatora, które adres ma potwierdzony dawno.
    # Domyślnie nullowalne, a nie ``default=now``: pole opisuje zdarzenie, a nie stan początkowy.
    email_verified_at = models.DateTimeField("adres e-mail potwierdzony", null=True, blank=True)

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()

    class Meta:
        verbose_name = "użytkownik"
        verbose_name_plural = "użytkownicy"
        ordering = ("email",)
        constraints = [
            models.UniqueConstraint(Lower("email"), name="accounts_user_email_ci_uniq"),
        ]

    def __str__(self) -> str:
        return self.email

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    def get_full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self) -> str:
        return self.first_name or self.email

    @property
    def role_names(self) -> list[str]:
        """Nazwy grup RBAC użytkownika (posortowane, stabilne w odpowiedziach API)."""
        return sorted(self.groups.values_list("name", flat=True))


class UserPreference(models.Model):
    """Jak ten człowiek chce widzieć serwis: język interfejsu i tryb wysokiego kontrastu.

    Osobna tabela, a nie kolumny w ``User``: to są ustawienia **prezentacji**, a nie dane konta.
    Konto bez wiersza zachowuje się dokładnie tak, jak przed wprowadzeniem tej funkcji (język
    z przeglądarki, kontrast wyłączony), więc migracja niczego nikomu nie zmienia, a wiersz
    powstaje dopiero wtedy, gdy ktoś świadomie coś przestawi.

    Dlaczego po stronie konta, a nie tylko w ciasteczku: ustawienie ma jechać za człowiekiem na
    drugie urządzenie. Uczeń, który włączył wysoki kontrast na szkolnym komputerze, nie ma go
    włączać jeszcze raz na telefonie w dniu zawodów. Gość, który konta nie ma, dostaje to samo
    w sesji i w ciasteczku języka (``apps.accounts.preferences``).

    ``language`` **nie ma** listy wartości w bazie i to jest świadome: zbiór języków serwisu stoi
    w ``settings.LANGUAGES`` i bywa poszerzany, a ``choices`` w modelu znaczyłoby migrację przy
    każdym dołożonym języku. Wartość sprawdza warstwa zapisu wobec ustawień; pusta znaczy „nie
    wybierałem, idź za przeglądarką”, a nie „polski”.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="preference")
    language = models.CharField("język interfejsu", max_length=8, blank=True)
    high_contrast = models.BooleanField("tryb wysokiego kontrastu", default=False)
    updated_at = models.DateTimeField("zmienione", auto_now=True)

    class Meta:
        verbose_name = "ustawienia interfejsu"
        verbose_name_plural = "ustawienia interfejsu"

    def __str__(self) -> str:
        return f"{self.user_id}: {self.language or 'auto'}{', kontrast' if self.high_contrast else ''}"


class Membership(models.Model):
    """Rola jednej osoby w jednym konkursie. Jedno konto, wiele konkursów, osobne role w każdym.

    Po co osobna tabela, skoro grupy Django już są: bo grupa jest **globalna dla instalacji**.
    Nauczyciel bywa opiekunem w trzech olimpiadach, uczeń uczestnikiem w dwóch, a koordynator
    olimpiady fizycznej nie ma mieć wglądu w prace olimpiady kwantowej – wszystkie trzy zdania
    są niewyrażalne zbiorem nazw grup bez właściciela.

    **Grupy Django nie znikają** i to jest decyzja, a nie zaległość. Od uprawnień grupy
    ``coordinator`` zależy dostęp do ``/cms/`` (migracja ``cms.0003_coordinator_permissions``
    kopiuje tam komplet uprawnień ``Editors`` + ``Moderators``, w tym ``access_admin``), a te
    uprawnienia są własnością Django i Wagtaila, nie naszą. Po tej zmianie grupa znaczy
    **uprawnienie do panelu redakcyjnego**, a ``Membership`` – **rolę w konkursie**; serwis
    nadający rolę zapisuje oba (``apps.accounts.services.grant_role``).

    **Superużytkownik nie ma tu wiersza i mieć nie musi.** ``is_superuser``/``is_staff`` opisują
    operatora platformy, a ``/admin/`` jest jego narzędziem. Autoryzacja konkursu nie eskaluje
    superusera po cichu – tak samo, jak nie robi tego dzisiejsze ``IsCoordinator``.

    ``CASCADE`` po obu stronach: skasowane konto nie zostawia roli, a skasowany konkurs (co
    w ogóle jest możliwe dopiero po usunięciu jego danych, bo reszta stoi na ``PROTECT``) nie
    zostawia członkostw wskazujących w pustkę. Tu nie ma czego archiwizować – dowodem, kto co
    zrobił, jest ``core.AuditLog``, a nie ta tabela.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name="konkurs",
    )
    role = models.CharField("rola", max_length=16, choices=CompetitionRole.choices)
    granted_at = models.DateTimeField("nadana", default=timezone.now)
    #: Kto nadał. ``SET_NULL``, bo skasowanie konta koordynatora nie może unieważnić ról, które
    #: on nadał – a ``NULL`` jest tu poprawną odpowiedzią „nadał system” (rejestracja, backfill).
    granted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="memberships_granted",
        verbose_name="nadał",
    )

    #: Członkostwo ma własną kolumnę konkursu, więc domyślna ścieżka ``competition`` wystarcza.
    objects = CompetitionScopedManager()

    class Meta:
        verbose_name = "członkostwo"
        verbose_name_plural = "członkostwa"
        ordering = ("competition", "user", "role")
        constraints = [
            # Rola jest faktem, a nie zdarzeniem: „uczestnik konkursu #1” albo jest, albo go nie
            # ma. Bez tego więzu ponowne nadanie roli (druga rejestracja, powtórzony backfill)
            # dokładałoby wiersze, a odebranie roli musiałoby kasować „wszystkie”.
            models.UniqueConstraint(fields=["user", "competition", "role"], name="accounts_membership_unique")
        ]
        indexes = [
            # Pytanie zadawane na **każdym** żądaniu panelu: „jakie role ma ta osoba tutaj”.
            models.Index(fields=["user", "competition"], name="accounts_membership_uc_idx")
        ]

    def __str__(self) -> str:
        return f"{self.user_id} → {self.competition_id}: {self.role}"


class Participant(models.Model):
    """Profil uczestnika. ``public_code`` jest jedynym identyfikatorem w publikowanych wynikach.

    Profil należy do **jednego** konkursu i nie da się go współdzielić: ``school``, ``grade``,
    ``district``, ``birth_year``, ``guardian_email``, ``publish_full_name`` i komplet zgód
    (``ConsentRecord``) są oświadczeniami złożonymi konkretnemu administratorowi danych pod
    konkretnym regulaminem. Jeden profil dla dwóch olimpiad znaczyłby, że zgoda złożona
    organizatorowi A obowiązuje organizatora B – czego nie da się obronić ani prawnie, ani
    technicznie (``public_code`` jest identyfikatorem w tabelach wyników **jednego** konkursu).

    ``user`` jest ``ForeignKey`` z ``related_name="participations"``, a nie ``OneToOneField``:
    jedno konto ma **tyle** profili, w ilu konkursach startuje (§ 3.3). Nazwy ``participant``
    w relacji odwrotnej celowo nie ma i mieć nie będzie – ``user.participant`` podnosi
    ``AttributeError``, więc każde przeoczone miejsce widać od razu, zamiast cicho oddawać profil
    z przypadkowego konkursu. Jedyną drogą do profilu jest
    ``apps.accounts.services.participant_for(user, competition)``, a do kompletu profili –
    ``participations_of(user)``.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="participations")
    #: Właściciel profilu. ``PROTECT``: skasowanie konkursu nie może zabrać ze sobą uczestników
    #: razem z ich zgodami. ``NOT NULL`` od wydania D (§ 4.1) – kolumna była nullowalna wyłącznie
    #: na czas współistnienia starego i nowego kodu i domknęła ją migracja
    #: ``accounts.0022_competition_not_null`` po zapytaniu kontrolnym (§ 4.4).
    competition = models.ForeignKey(
        "tenancy.Competition",
        verbose_name="konkurs",
        db_index=True,
        on_delete=models.PROTECT,
        related_name="participants",
    )
    #: Unikalny **w konkursie**, a nie globalnie: prefiks jest własnością konkursu
    #: (``Competition.public_code_prefix``), a tabela wyników, w której ten kod stoi, należy do
    #: jednego konkursu. Więz jest w ``Meta.constraints``, bo dotyczy pary kolumn.
    public_code = models.CharField("kod publiczny", max_length=16, default=generate_public_code)
    # Nazwa szkoły **do pokazania** – wypełniona zawsze, niezależnie od tego, czy uczestnik wybrał
    # szkołę ze słownika, czy wpisał ją ręcznie. To ona idzie do snapshotu wyników (grupowanie
    # k-anonimowe po szkole) i do podglądu koordynatora, więc żadne miejsce w systemie nie musi
    # wiedzieć, którą drogą uczestnik się zarejestrował. 255 znaków, bo tyle ma najdłuższa nazwa
    # w wykazie SIO (``apps.schools``).
    school = models.CharField("szkoła", max_length=255)
    # Dowiązanie do rejestru – opcjonalne z założenia, a nie z niedoróbki: wykaz SIO nie zna szkół
    # zagranicznych ani placówek założonych po dacie wykazu, a brak swojej szkoły na liście nie
    # może zamykać drogi do rejestracji. PROTECT, bo skasowanie wiersza słownika zabrałoby
    # uczestnikowi informację o szkole; ``seed_schools`` wygasza (``is_active=False``), nie kasuje.
    school_ref = models.ForeignKey(
        "schools.School",
        verbose_name="szkoła z rejestru",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="participants",
    )
    # Dowiązanie do słownika **własnego organizatora** (``schools.CustomInstitution``, § 1.3.3).
    # Stoi **obok** ``school_ref``, a nie zamiast: to są dwa różne wykazy – publiczny rejestr SIO
    # wspólny dla instalacji i lista jednego konkursu. Dwa nullowalne klucze obce zamiast relacji
    # ogólnej to ta sama decyzja i to samo uzasadnienie, co przy ``Certificate.entry``/``supervisor``
    # (``apps/results/models.py``): rodzajów wykazu są dwa i nigdy nie będzie ich więcej, a klucz
    # obcy daje integralność, której ``GenericForeignKey`` nie daje. Nazwa ``school`` (wolny tekst)
    # jest wypełniona tak czy inaczej, więc tabela wyników i próg k-anonimowości nie muszą wiedzieć,
    # z którego wykazu wiersz pochodzi. PROTECT z tego samego powodu, co przy ``school_ref``:
    # skasowanie wiersza słownika zabrałoby uczestnikowi informację o placówce, a import wykazu
    # wygasza (``is_active=False``), nie kasuje.
    custom_institution_ref = models.ForeignKey(
        "schools.CustomInstitution",
        verbose_name="placówka ze słownika organizatora",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="participants",
    )
    # Kraj uczestnika w zapisie ISO 3166-1 alpha-2. **Pusty znaczy Polska**, a nie „nie podano”:
    # wpisanie ``"PL"`` istniejącym profilom byłoby migracją danych osobowych bez powodu, a odczyt
    # ``country or "PL"`` ma jedno miejsce (§ 1.3.2). Kolumna powstaje razem z ``RegistrationProfile``
    # i przy wyłączonej fladze ``institution_types`` nikt jej nie wypełnia – formularz Konkursu #1
    # o kraj nie pyta ani razu.
    country = models.CharField("kraj", max_length=2, blank=True)
    # Nazwa placówki wpisana wolnym tekstem przy rodzaju spoza wykazu (``FOREIGN``, ``OTHER``).
    # Pole jest **niezależne** od ``school``: ``school`` zostaje nazwą do pokazania (wchodzi do
    # progu k-anonimowości w publikacji wyników), a serwis rejestracji kopiuje do niego tę wartość
    # – dokładnie tak, jak dziś kopiuje nazwę z wykazu (``_resolve_school``). Dzięki temu żadne
    # miejsce liczące statystyki nie musi wiedzieć, którą drogą uczestnik się zarejestrował.
    institution_name = models.CharField("nazwa placówki", max_length=255, blank=True)
    # Nullowalna wyłącznie ze względu na profile sprzed wprowadzenia pola – formularz, API
    # i serwis rejestracji wymagają klasy od każdego nowego uczestnika.
    grade = models.PositiveSmallIntegerField("klasa", choices=GRADE_CHOICES, null=True, blank=True)
    district = models.CharField("województwo", max_length=100, choices=Voivodeship.choices)
    # Region z podziału terytorialnego konkursu (``Region``). Stoi **obok** ``district``, a nie
    # zamiast: przy wyłączonej fladze ``custom_regions`` czyta się wyłącznie ``district``, dokładnie
    # jak dziś, a przy włączonej ``region`` jest źródłem prawdy i to z niego serwis rejestracji
    # wypełnia ``district`` (§ 1.4.2). Denormalizacja jest świadoma i ma termin ważności: wykreślenie
    # ``district`` jest pozycją backlogu na sezon po włączeniu flagi wszędzie – bez niej trzeba by
    # dotknąć ponad sześćdziesięciu miejsc naraz, w tym tabel wyników.
    # Nullowalne: profile sprzed tej zmiany i konkursy, które regionów nie używają.
    region = models.ForeignKey(
        "accounts.Region",
        verbose_name="region",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="participants",
    )
    # Pełna data urodzenia. Od zgłoszenia organizatora z 22.09.2026 rejestracja pyta **o nią**,
    # a nie o sam rocznik: od wieku zależy podstawa prawna zapisu (zgoda opiekuna dla małoletniego),
    # a rocznik potrafił odpowiedzieć na to pytanie wyłącznie zachowawczo – osoba kończąca 18 lat
    # w tym roku była przez większość roku liczona jak dziecko, także po swoich urodzinach.
    #
    # ``null=True`` jest **stanem historycznym, nie opcją**: profile sprzed tej zmiany znają sam
    # rocznik i nikt im dnia nie dopisze (zgadnięty dzień urodzin byłby danymi wymyślonymi, a nie
    # uzupełnionymi). Puste pole znaczy „nie wiemy dokładnie” i reguła wieku spada wtedy na starą,
    # rocznikową (``apps.accounts.consents.is_minor``). Uczestnik uzupełnia datę sam
    # w ``/me/profile/``, a panel przypomina mu o tym jednym zdaniem.
    birth_date = models.DateField("data urodzenia", null=True, blank=True)
    #: Rocznik. Zostaje kolumną ``NOT NULL`` i od tej zmiany jest **wyliczany**: gdy ``birth_date``
    #: jest wypełnione, ``birth_year`` musi być równe ``birth_date.year`` i pilnuje tego ``save()``
    #: (oraz ``clean()`` dla formularzy i administracji). Nie ma tu więzu bazodanowego, bo
    #: „wyciągnij rok z daty” nie zapisuje się przenośnie w ``CheckConstraint`` – zamiast tego
    #: jedno miejsce zapisu i test niezmienniczy.
    #:
    #: Po co w ogóle zostaje, skoro data ją zawiera: bo eksporty dla podmiotów zewnętrznych mają
    #: dostawać **rocznik i nic więcej** (minimalizacja), a wiersze historyczne nie mają daty
    #: w ogóle – bez tej kolumny nie dałoby się ich porównać z nowymi jednym zapytaniem.
    birth_year = models.PositiveSmallIntegerField("rok urodzenia")
    # Telefon kontaktowy. ``blank=True`` w modelu, choć formularz i API wymagają go od każdego
    # nowego uczestnika: profile sprzed wprowadzenia pola nie mają numeru i nie wolno ich
    # unieważnić – uczestnik w trakcie edycji zostałby wtedy odcięty od panelu do czasu, aż
    # uzupełni dane. Kształt numeru pilnuje ``apps.accounts.phones.normalize_phone``; 32 znaki
    # mieszczą numer międzynarodowy z prefiksem i separatorami z zapasem.
    phone = models.CharField("telefon", max_length=32, blank=True)
    # Pola zgód są **projekcjami**, a nie dowodem: dowodem jest ``ConsentRecord`` (patrz niżej).
    # Zostają, bo odpowiadają na pytanie „jak jest teraz” jednym odczytem – publikacja wyników
    # (``apps.results.services``), panel koordynatora i administracja pytają o stan bieżący
    # kilkanaście razy na żądanie i nie mają po co przekopywać historii. Zapisuje je wyłącznie
    # ``apps.accounts.services.record_consents`` – razem z wpisami dowodowymi, jedną transakcją.
    # Nullowalne od wprowadzenia importu uczniów przez opiekuna (``apps.accounts.bulk_registration``):
    # profil zaproszonego ucznia powstaje **zanim** ktokolwiek złożył za niego oświadczenie, bo
    # nauczyciel nie może zgodzić się w cudzym imieniu. Puste pole znaczy więc „zgody jeszcze nie
    # ma i konto jest nieaktywne”, a nie „zgubiliśmy datę”; wypełnia je dopiero ``record_consents``
    # przy przyjęciu zaproszenia. Reguła „bez zgody RODO nie ma udziału w zawodach” nie słabnie:
    # konto bez tej daty nie przechodzi aktywacji, więc nie zaloguje się i niczego nie odda.
    gdpr_consent_at = models.DateTimeField("zgoda RODO z dnia", null=True, blank=True)
    #: Akceptacja regulaminu. Nullowalne wyłącznie ze względu na profile sprzed wprowadzenia
    #: zestawu zgód – od tej zmiany żadna droga rejestracji nie przepuszcza pustej wartości.
    terms_accepted_at = models.DateTimeField("regulamin zaakceptowany", null=True, blank=True)
    guardian_consent = models.BooleanField("zgoda opiekuna", default=False)
    # Adres rodzica albo opiekuna prawnego, na który idzie prośba o zgodę online
    # (``apps.accounts.guardian``). Puste znaczy „uczestnik jeszcze go nie podał”, a nie „nie ma
    # opiekuna”: pole wypełnia sam uczestnik w panelu i wyłącznie wtedy, gdy zgoda jest wymagana
    # (rocznik małoletni). Adres jest częścią podpisanego tokenu, więc jego zmiana unieważnia
    # wysłany wcześniej link – to jedyna droga odwołania prośby.
    guardian_email = models.EmailField("adres e-mail opiekuna", blank=True)
    publish_full_name = models.BooleanField("zgoda na publikację pełnych danych", default=False)
    # Adres opiekuna szkolnego wskazany przez uczestnika – **deklaracja**, a nie klucz obcy.
    # Wiązanie po adresie jest tu celowe i wynika z kolejności zdarzeń: uczeń rejestruje się we
    # wrześniu, a nauczyciel zakłada konto (jeśli w ogóle) w listopadzie. Klucz obcy wymagałby
    # istniejącego konta w chwili rejestracji, czyli zamieniłby opiekuna z udogodnienia w warunek
    # startu. Panel opiekuna dopasowuje uczniów po znormalizowanym adresie
    # (``apps.accounts.supervisors``), a pusty adres nie wiąże z nikim.
    supervisor_email = models.EmailField("adres opiekuna szkolnego", blank=True, db_index=True)
    # --- import listy uczniów przez opiekuna albo koordynatora (apps.accounts.bulk_registration) ---
    # Znacznik pochodzenia profilu, a nie stanu: wypełniony znaczy „to konto założył nauczyciel
    # z listy klasowej, a nie uczeń sam z siebie”. Zostaje **na zawsze**, także po aktywacji, bo
    # odpowiada na pytanie koordynatora „skąd się tu wzięło dwadzieścia kont z jednej szkoły
    # w jednej minucie” (odznaka „z importu” na liście kont). Stanu konta nie duplikuje: „zaproszony
    # czy aktywny” rozstrzyga ``User.email_verified_at``, tak samo jak przy zwykłej rejestracji.
    invited_at = models.DateTimeField("zaproszony z importu", null=True, blank=True, db_index=True)
    #: Kiedy ostatnio poszedł list z zaproszeniem. Osobne pole od ``invited_at``, bo zaproszenie
    #: wolno wysłać ponownie (link żyje 14 dni, a uczeń bywa na wakacjach) – bez tego panel
    #: opiekuna nie umiałby odpowiedzieć „wysłano dziś czy trzy tygodnie temu”.
    invitation_sent_at = models.DateTimeField("zaproszenie wysłane", null=True, blank=True)

    #: Własna kolumna konkursu, więc domyślna ścieżka ``competition`` z queryseta wystarcza.
    objects = CompetitionScopedManager()

    class Meta:
        verbose_name = "uczestnik"
        verbose_name_plural = "uczestnicy"
        ordering = ("public_code",)
        constraints = [
            # Jedno konto – jeden profil **w konkursie**. Bez tego więzu podwójne kliknięcie
            # w rejestrację albo powtórzony import listy klasowej dawałyby dwa profile tej samej
            # osoby w tym samym konkursie, z dwoma kodami publicznymi i dwoma kompletami zgód.
            models.UniqueConstraint(
                fields=["user", "competition"], name="accounts_participant_unique_per_competition"
            ),
            # Kod publiczny jest unikalny w konkursie, a nie w instalacji: ten sam ciąg w dwóch
            # konkursach to dwa różne kody w dwóch różnych tabelach wyników, a globalna unikalność
            # kazałaby drugiemu organizatorowi omijać kody pierwszego.
            models.UniqueConstraint(
                fields=["competition", "public_code"],
                name="accounts_participant_public_code_per_competition",
            ),
        ]

    def __str__(self) -> str:
        return self.public_code

    def save(self, *args, **kwargs):
        """Wypełniona data urodzenia **wyznacza** rocznik – zawsze, niezależnie od drogi zapisu.

        Reguła stoi w ``save()``, a nie w serwisach, bo dróg zapisu jest siedem (rejestracja WWW,
        rejestracja przez dostawcę, API, import listy klasowej, edycja profilu, ekran koordynatora,
        administracja Django) i każda z nich mogłaby zostawić rocznik z innego roku niż data.
        Dwie kolumny mówiące o tej samej rzeczy dwie różne rzeczy są gorsze od jednej niedokładnej.

        ``update_fields`` jest dopisywane, a nie ignorowane: zapis punktowy samej daty (tak działa
        uzupełnienie profilu) musi zapisać także wyliczony z niej rocznik, inaczej kolumny
        rozjechałyby się dokładnie w tym jednym przypadku, dla którego to pole powstało.
        """
        if self.birth_date is not None and self.birth_year != self.birth_date.year:
            self.birth_year = self.birth_date.year
            update_fields = kwargs.get("update_fields")
            if update_fields is not None and "birth_year" not in update_fields:
                kwargs["update_fields"] = [*update_fields, "birth_year"]
        return super().save(*args, **kwargs)

    def clean(self) -> None:
        """Zakres daty urodzenia – zanim wywróci się formularz albo administracja.

        Granice są celowo szerokie i celowo dwie: data z przyszłości jest oczywistą pomyłką (albo
        próbą obejścia reguły wieku), a data sprzed 1900 roku – literówką w polu roku. Górnego
        ograniczenia wieku („najwyżej 120 lat”) tu nie ma: nie chroni przed żadnym nadużyciem,
        a odmawiałoby rejestracji człowiekowi, który po prostu żyje długo.
        """
        super().clean()
        if self.birth_date is not None:
            today = timezone.localdate()
            if self.birth_date > today:
                raise ValidationError({"birth_date": "Data urodzenia nie może być z przyszłości."})
            if self.birth_date < MIN_BIRTH_DATE:
                raise ValidationError(
                    {"birth_date": f"Data urodzenia nie może być wcześniejsza niż {MIN_BIRTH_DATE:%d.%m.%Y}."}
                )

    @property
    def known_birth_year(self) -> int | None:
        """Rocznik albo ``None``, gdy wieku nie znamy (:data:`UNKNOWN_BIRTH_YEAR`).

        Do pokazywania i do eksportu, nigdy do reguły wieku: tam idzie ``birth_year`` wprost, bo
        ``consents.is_minor`` i tak rozumie fałszywą wartość jako „nie wiem, czyli małoletni”.
        """
        return self.birth_year or None


class ConsentRecord(models.Model):
    """Dowód złożenia jednej zgody: co, w jakiej wersji dokumentu, kiedy i którą drogą.

    Model jest **rejestrem zdarzeń, nie stanem**, i dlatego nie ma tu unikalności po parze
    (uczestnik, rodzaj). Zgodę wolno wycofać i wyrazić ponownie, a dokument, którego dotyczy,
    wolno znowelizować – w każdym z tych przypadków powstaje nowy wiersz, a poprzedni zostaje
    nietknięty. Nadpisywanie jednego wiersza „aktualnym stanem” kasowałoby dokładnie tę
    informację, po którą się do tej tabeli sięga: co obowiązywało w chwili, o którą pyta
    organ nadzorczy albo uczestnik.

    ``document_version`` jest kopią, a nie kluczem obcym do strony w CMS-ie. Strona żyje dalej
    (redaktor ją poprawia, organizator wgrywa nową wersję), a dowód ma zamarznąć – wskazanie
    na żywy obiekt znaczyłoby „zgodziła się na to, co jest tam dzisiaj”.

    ``CASCADE``: skasowanie profilu uczestnika (żądanie usunięcia danych) zabiera też jego
    zgody – trzymanie dowodu zgody osoby, której danych już nie mamy, nie ma podstawy.

    **Właściciel wpisu jest jeden z dwóch, nigdy oba naraz** (``accounts_consentrecord_exactly_one_owner``):
    ``participant`` dla zgody uczestnika, ``supervisor`` dla zgody opiekuna szkolnego złożonej przy
    rejestracji (``apps.accounts.supervisors.register_supervisor``). Osobna kolumna, a nie wspólna
    „konto”, bo dowód ma mówić, **czyją** zgodę na co niesie – z samego ``user`` nie dałoby się
    odróżnić zgody złożonej jako uczestnik od zgody złożonej jako opiekun, gdyby kiedyś jedna osoba
    miała oba profile. ``kind`` opiekuna ogranicza się w praktyce do ``TERMS`` i ``PRIVACY`` –
    reguła „opiekun dla niepełnoletniego” i zgoda na publikację nazwiska dotyczą wyłącznie ucznia.
    """

    #: ``null=True``: wpis może zamiast tego wskazywać ``supervisor`` – patrz ograniczenie
    #: ``accounts_consentrecord_exactly_one_owner`` w ``Meta.constraints``.
    participant = models.ForeignKey(
        Participant,
        on_delete=models.CASCADE,
        related_name="consents",
        verbose_name="uczestnik",
        null=True,
        blank=True,
    )
    #: Zgoda opiekuna szkolnego (regulamin, RODO) złożona przy ``/register/supervisor/``.
    #: ``null=True`` z tego samego powodu, co ``participant`` – wpis niesie dokładnie jedną z tych
    #: dwóch relacji.
    supervisor = models.ForeignKey(
        "accounts.SchoolSupervisor",
        on_delete=models.CASCADE,
        related_name="consents",
        verbose_name="opiekun szkolny",
        null=True,
        blank=True,
    )
    kind = models.CharField("rodzaj", max_length=20, choices=ConsentKind.choices)
    document_version = models.CharField("wersja dokumentu", max_length=100, blank=True)
    given_at = models.DateTimeField("wyrażona", default=timezone.now)
    withdrawn_at = models.DateTimeField("wycofana", null=True, blank=True)
    source = models.CharField("droga", max_length=16, choices=ConsentSource.choices)
    # Adres, z którego zgodę złożyła **inna osoba niż uczestnik** – dziś wyłącznie opiekun
    # potwierdzający zgodę podpisanym linkiem (``apps.accounts.guardian``). Puste znaczy „złożył
    # sam uczestnik” i tak wygląda każdy wpis sprzed wprowadzenia zgód opiekuna online. To pole
    # odróżnia dowód woli opiekuna od oświadczenia dziecka o tej woli – bez niego obie postacie
    # byłyby w tabeli nieodróżnialne.
    given_by_email = models.EmailField("potwierdzone z adresu", blank=True)
    # Adres IP w chwili złożenia zgody. Część dowodu, tak samo jak wersja dokumentu i czas:
    # „kiedy i skąd” jest pierwszym pytaniem przy sporze o to, czy zgoda w ogóle padła.
    # Nullowalne, bo wpisy sprzed wprowadzenia pola adresu nie mają, a ``client_ip`` zwraca
    # ``None``, gdy żądania nie da się zidentyfikować (komenda zarządzająca, zadanie w tle).
    ip_address = models.GenericIPAddressField("adres IP", null=True, blank=True)

    class Meta:
        verbose_name = "zgoda uczestnika"
        verbose_name_plural = "zgody uczestników"
        ordering = ("-given_at", "-id")
        indexes = [
            models.Index(fields=["participant", "kind"], name="accounts_consent_pk_idx"),
            models.Index(fields=["supervisor", "kind"], name="accounts_consent_sup_idx"),
        ]
        constraints = [
            # Wpis dowodowy ma dokładnie jednego właściciela – nigdy oba naraz (rozjazd, komu
            # naprawdę dotyczy zgoda) i nigdy żadnego (dowód bez adresata nie jest dowodem niczego).
            models.CheckConstraint(
                condition=(
                    models.Q(participant__isnull=False, supervisor__isnull=True)
                    | models.Q(participant__isnull=True, supervisor__isnull=False)
                ),
                name="accounts_consentrecord_exactly_one_owner",
            ),
        ]

    def __str__(self) -> str:
        state = "wycofana" if self.withdrawn_at else "aktywna"
        return f"{self.get_kind_display()} ({state})"

    @property
    def is_active(self) -> bool:
        return self.withdrawn_at is None


# =================================================================================================
# Definicje zgód per konkurs (``docs/UNIWERSALNY-ETAP-2.md`` § 1.1.2)
# =================================================================================================


class ConsentDefinition(models.Model):
    """Definicja jednej zgody **jednego konkursu**: treść, dokument, wersja, wymagalność.

    Model nie zastępuje ``ConsentRecord`` i nie jest z nim związany kluczem obcym. Dowód
    (``ConsentRecord.document_version``) zostaje **kopią napisu** z chwili złożenia oświadczenia –
    dokładnie jak dziś. Klucz obcy do definicji znaczyłby, że poprawienie literówki w treści zgody
    zmienia wstecz to, na co ludzie się zgodzili, a to jest jedyna rzecz, której przy zgodzie
    zmienić nie wolno.

    Wiersze wpisuje migracja ``accounts.0024`` – każdemu konkursowi zestaw ze stałej
    ``apps.accounts.consents.DEFAULT_CONSENTS``, znak w znak razem z wersjami dokumentów. Dopóki
    konkurs nie włączy flagi ``per_competition_consents``, wiersze **leżą nieużywane**: zestaw
    czyta się ze stałej (:func:`apps.accounts.consents.consent_set`), a ich równość ze stałą
    pilnuje test ``test_consent_definitions_match_the_constant``.

    Czym ten model **nie** jest: nie jest miejscem na nowy *rodzaj* zgody. ``ConsentKind`` zostaje
    zamkniętą listą w kodzie, bo po rodzaju poznaje zgodę model dowodowy, serwis rejestracji
    i reguła „opiekun dla niepełnoletniego” (``consents.is_minor``) – a reguła musi wiedzieć,
    o którą zgodę chodzi. Konkurs zmienia tu **treść, dokument, wersję i wymagalność**.
    """

    #: ``CASCADE``, a nie ``PROTECT`` jak przy uczestniku i komitecie – i różnica jest w tym, czym
    #: jest wiersz. Profil uczestnika i profil członka komitetu niosą **cudze dane**, więc konkursu
    #: z nimi skasować nie wolno. Definicja zgody jest **konfiguracją samego konkursu**: opisem
    #: tego, o co ten konkurs pyta w formularzu. Dowód zostaje nietknięty, bo nie ma do definicji
    #: klucza obcego – ``ConsentRecord.document_version`` jest **kopią napisu** z chwili złożenia
    #: oświadczenia. Kasowany konkurs nie zostawia więc wpisów dowodowych bez treści; zostawia je
    #: dokładnie takimi, jakie były. ``PROTECT`` znaczyłby natomiast, że migracja danych ``0024``
    #: (wiersz dla **każdego** konkursu w bazie) uczyniła każdy konkurs nieusuwalnym.
    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.CASCADE,
        related_name="consent_definitions",
        verbose_name="konkurs",
    )
    kind = models.CharField("rodzaj", max_length=24, choices=ConsentKind.choices)
    #: Nazwa pola w formularzu, w serializerze i w imporcie grupowym. Stoi w wierszu, bo mapowanie
    #: „pole → rodzaj zgody” ma być jedno – rozjazd formularza z serwisem znaczyłby zgodę zapisaną
    #: pod niewłaściwym rodzajem.
    field_name = models.CharField("nazwa pola", max_length=40)
    #: Wzorzec z nazwanymi miejscami ``{link}`` (odnośnik do dokumentu) i ``{organizer}`` (nazwa
    #: organizatora). Inne miejsce w nawiasach klamrowych jest literówką i odbija je ``clean()``:
    #: niepodstawione ``{cokolwiek}`` wywróciłoby renderowanie **formularza rejestracji**.
    text = models.TextField("treść oświadczenia")
    link_text = models.CharField("tekst odnośnika", max_length=200, blank=True)
    document_slug = models.SlugField("dokument", max_length=60, blank=True)
    version = models.CharField("wersja dokumentu", max_length=100)
    required = models.BooleanField("wymagana zawsze", default=False)
    required_for_minor = models.BooleanField("wymagana dla niepełnoletnich", default=False)
    help_text = models.CharField("podpowiedź", max_length=200, blank=True)
    missing_message = models.CharField("komunikat o braku", max_length=300, blank=True)
    #: Kolejność jest treścią, a nie kosmetyką: zgody wymagane stoją przed dobrowolnymi, żeby nikt
    #: nie zaakceptował dobrowolnej, myśląc, że to ta wymagana.
    ordering = models.PositiveSmallIntegerField("kolejność", default=0)
    #: Wycofanie zgody z zestawu jest przestawieniem tego pola, a nie skasowaniem wiersza: wpisy
    #: dowodowe z lat poprzednich mają dalej mieć w bazie treść, pod którą je złożono.
    is_active = models.BooleanField("aktywna", default=True)

    #: Definicja ma własną kolumnę konkursu – nie ma drugiej drogi, którą mogłaby do niego dojść.
    objects = CompetitionScopedManager()

    class Meta:
        verbose_name = "definicja zgody"
        verbose_name_plural = "definicje zgód"
        ordering = ("competition", "ordering", "id")
        constraints = [
            models.UniqueConstraint(fields=["competition", "kind"], name="accounts_consentdef_unique_kind"),
            models.UniqueConstraint(
                fields=["competition", "field_name"], name="accounts_consentdef_unique_field"
            ),
            # Zgoda bez wersji nie jest dowodem: ``ConsentRecord.document_version`` byłby pusty
            # i po pierwszej nowelizacji regulaminu nie dałoby się odpowiedzieć na jedyne pytanie,
            # które przy zgodzie pada.
            models.CheckConstraint(
                condition=~models.Q(version=""), name="accounts_consentdef_version_not_empty"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} ({self.version})"

    def clean(self) -> None:
        """Sprawdza wersję i wzorzec treści – **zanim** wywróci się formularz rejestracji.

        ``text`` składa ``format_html`` z dwoma nazwanymi argumentami. Trzecie miejsce w nawiasach
        klamrowych (albo niedomknięty nawias) podnosi wyjątek dopiero przy renderowaniu, czyli
        u uczestnika próbującego się zarejestrować, a nie u organizatora zapisującego treść.
        """
        super().clean()
        if not (self.version or "").strip():
            raise ValidationError({"version": "Podaj wersję dokumentu – bez niej zgoda nie jest dowodem."})
        try:
            (self.text or "").format(link="", organizer="")
        except (IndexError, KeyError, ValueError) as error:
            raise ValidationError(
                {
                    "text": (
                        "Treść może zawierać wyłącznie miejsca {link} i {organizer}; "
                        f"nawiasy klamrowe wpisz podwójnie. Błąd: {error}"
                    )
                }
            ) from error


#: Rodzaje placówek, które **mają wiersze** w ``schools.School`` – czyli te, dla których pytanie
#: „którą wybierasz z wykazu” w ogóle ma sens. Pozostałe (``FOREIGN``, ``NONE``, ``OTHER``) są
#: sytuacjami uczestnika, a nie pozycjami rejestru: tam nazwę wpisuje się wolnym tekstem
#: (§ 1.3.2 planu etapu 2). Krotka napisów, a nie import ``InstitutionType`` na górze modułu:
#: ``apps.schools.models`` importuje ``apps.accounts.models`` (lista województw), więc import
#: w drugą stronę zamknąłby pętlę zależności między aplikacjami. Zgodność tej krotki z listą
#: rodzajów pilnuje ``apps/accounts/tests/test_registration_profile.py``.
DIRECTORY_INSTITUTION_TYPES: tuple[str, ...] = ("PRIMARY", "SECONDARY", "UNIVERSITY")

#: Rodzaj placówki, o który pyta dzisiejsza rejestracja – i jedyny, jaki zna Konkurs #1.
DEFAULT_INSTITUTION_TYPE = "SECONDARY"


class RegistrationProfile(models.Model):
    """Co konkurs pyta przy rejestracji i co dopuszcza jako placówkę.

    Model, a nie kilkanaście pól na ``Competition``: to kilkanaście pól **jednej sprawy**, a
    ``Competition`` ma już trzydzieści i jest czytany na każdym żądaniu. Wiersz jest
    **opcjonalny** – jego brak znaczy „jak dziś”, i taki jest stan Konkursu #1: migracja
    ``accounts.0028`` nie zakłada go nikomu, bo brak wiersza i wiersz z samymi wartościami
    domyślnymi są równoważne, a brak jest tańszy i jawniejszy (§ 1.3.4).

    **Wartość domyślna każdego pola odtwarza dzisiejszy formularz**: szkoła ponadpodstawowa
    z wykazu SIO albo wpisana ręcznie, klasa 1–5 wymagana, telefon wymagany, województwo
    wymagane, rocznik wymagany, bez kategorii do wyboru. Dlatego jedyne wejście do tej
    konfiguracji – ``apps.accounts.services.registration_profile`` – może przy wyłączonej fladze
    ``institution_types`` oddać **niezapisany** wiersz z samymi domyślnymi i nie zapytać bazy ani
    razu (warunek budżetu ``/register/``, § 5.6).

    Czym ten model **nie** jest: nie jest miejscem na dodatkowe pole formularza. Zestaw pytań
    zostaje w kodzie (formularz, serializer, serwis); konkurs rozstrzyga tu wyłącznie, **czy**
    o coś pytać i w jakim zakresie.
    """

    #: ``CASCADE`` i ``OneToOne``, tak jak przy ``ConsentDefinition``: to jest konfiguracja samego
    #: konkursu, a nie cudze dane. Skasowanie konkursu zabiera jego profil rejestracji i nie ma
    #: powodu, żeby go przed tym bronić – profile uczestników trzyma ``PROTECT`` na
    #: ``Participant.competition``, czyli tam, gdzie stoją dane ludzi.
    competition = models.OneToOneField(
        "tenancy.Competition",
        verbose_name="konkurs",
        on_delete=models.CASCADE,
        related_name="registration_profile",
    )
    #: Lista wartości ``schools.InstitutionType``. **Pusta znaczy wyłącznie ``SECONDARY``**, czyli
    #: dzisiaj – a nie „żadna”: pusty zbiór dopuszczonych placówek zamknąłby rejestrację, co nie
    #: jest konfiguracją, tylko awarią. Lista, a nie tabela wiele-do-wielu: to jest garść napisów
    #: ze **stałej** listy, czytana raz na formularz i nigdy nie filtrowana w zapytaniu.
    allowed_institution_types = models.JSONField("dozwolone placówki", default=list, blank=True)
    #: Czy wyszukiwarka pyta też o słownik organizatora (``schools.CustomInstitution``, T24).
    #: Warunek jest **koniunkcją** z flagą ``custom_school_directory`` – patrz
    #: ``services.custom_directory_enabled``.
    allow_custom_directory = models.BooleanField("słownik organizatora", default=False)
    #: Szkoła spoza wykazu wpisana wolnym tekstem. Domyślnie **wolno**, bo tak jest dziś: wykaz SIO
    #: nie zna szkół założonych po jego dacie, a brak swojej szkoły na liście nie może zamykać
    #: drogi do rejestracji.
    allow_free_text_school = models.BooleanField("szkoła spoza wykazu", default=True)
    allow_foreign = models.BooleanField("uczestnicy spoza Polski", default=False)
    require_grade = models.BooleanField("klasa wymagana", default=True)
    require_phone = models.BooleanField("telefon wymagany", default=True)
    require_region = models.BooleanField("region wymagany", default=True)
    #: Czy rejestracja pyta o **datę urodzenia**. Nazwa kolumny zostaje ``require_birth_year``
    #: i jest to świadoma decyzja: zmiana nazwy kolumny to migracja na żywej bazie i przepisanie
    #: każdego odczytu, a znaczenie i tak jest jedno – „czy pytamy o wiek”. Zmienia się natomiast
    #: to, **o co** pytamy: od wydania z pełną datą urodzenia pole ``Participant.birth_date``
    #: jest nullowalne, więc pierwszy raz naprawdę jest co wyłączyć.
    #:
    #: Wyłączone znaczy „data nieobowiązkowa”, a **nie** „wieku nie sprawdzamy”: uczestnik bez daty
    #: i bez rocznika jest dla ``consents.is_minor`` małoletni, czyli konkurs, który o wiek nie
    #: pyta, zbiera zgodę opiekuna od wszystkich. To jest jedyny bezpieczny odwrót – wiek
    #: rozstrzyga o podstawie prawnej zapisu, a nieznany wiek nie może znaczyć „pełnoletni”.
    require_birth_year = models.BooleanField(
        "data urodzenia wymagana",
        default=True,
        help_text=(
            "Wyłączona znaczy, że data urodzenia jest nieobowiązkowa. Uczestnik bez podanej daty "
            "jest traktowany jak osoba niepełnoletnia, więc zgoda opiekuna będzie wymagana "
            "od każdego."
        ),
    )
    #: ``None`` znaczy „jak dziś”, czyli ``MIN_GRADE``/``MAX_GRADE``. Osobne pola, a nie lista klas:
    #: konkurs zawęża **przedział**, a nie wybiera klasy pojedynczo – a przedział da się pokazać
    #: w komunikacie walidacji jednym zdaniem.
    grade_min = models.PositiveSmallIntegerField("klasa od", null=True, blank=True)
    grade_max = models.PositiveSmallIntegerField("klasa do", null=True, blank=True)
    participant_picks_category = models.BooleanField("kategoria z wyboru", default=False)

    #: Własna kolumna konkursu – domyślna ścieżka queryseta.
    objects = CompetitionScopedManager()

    class Meta:
        verbose_name = "profil rejestracji"
        verbose_name_plural = "profile rejestracji"
        ordering = ("competition",)

    def __str__(self) -> str:
        return f"profil rejestracji: {self.competition_id}"

    # --- odczyt -------------------------------------------------------------------------------

    def institution_types(self) -> tuple[str, ...]:
        """Rodzaje placówek dopuszczone w tym konkursie – zawsze niepuste i zawsze uporządkowane.

        Porządek jest porządkiem deklaracji ``InstitutionType``, a nie kolejnością wpisaną do
        kolumny: lista wyboru w formularzu ma wyglądać tak samo niezależnie od tego, w jakiej
        kolejności koordynator zaznaczał kratki. Wartość spoza listy rodzajów jest pomijana –
        wiersz po ręcznej poprawce w bazie nie ma prawa wywrócić formularza rejestracji.
        """
        from apps.schools.models import InstitutionType

        chosen = {str(value) for value in (self.allowed_institution_types or [])}
        allowed = tuple(value for value in InstitutionType.values if value in chosen)
        return allowed or (DEFAULT_INSTITUTION_TYPE,)

    def directory_types(self) -> tuple[str, ...]:
        """Dopuszczone rodzaje, które mają wiersze w wykazie – czyli czym karmić wyszukiwarkę."""
        return tuple(value for value in self.institution_types() if value in DIRECTORY_INSTITUTION_TYPES)

    def grade_range(self) -> tuple[int, int]:
        """Przedział klas ``(od, do)``. ``None`` w kolumnie znaczy dzisiejszą granicę."""
        low = MIN_GRADE if self.grade_min is None else int(self.grade_min)
        high = MAX_GRADE if self.grade_max is None else int(self.grade_max)
        return low, high

    def is_default(self) -> bool:
        """Czy ten profil pyta dokładnie o to, o co pyta dzisiejszy formularz.

        Pytanie zadaje formularz: profil domyślny **nie dokłada ani jednego pola** i nie zmienia
        ani jednej etykiety, więc ``/register/`` Konkursu #1 zostaje bajt w bajt taki, jak dziś
        (§ 5.3, ``test_registration_form_html_unchanged``).
        """
        return (
            self.institution_types() == (DEFAULT_INSTITUTION_TYPE,)
            and self.allow_free_text_school
            and not self.allow_foreign
            and not self.allow_custom_directory
            and self.require_grade
            and self.require_phone
            and self.require_region
            and self.require_birth_year
            and self.grade_range() == (MIN_GRADE, MAX_GRADE)
        )

    def clean(self) -> None:
        """Sprawdza przedział klas i listę rodzajów – **zanim** wywróci się formularz rejestracji."""
        super().clean()
        from apps.schools.models import InstitutionType

        unknown = sorted(
            {str(value) for value in (self.allowed_institution_types or [])} - set(InstitutionType.values)
        )
        if unknown:
            raise ValidationError(
                {"allowed_institution_types": f"Nieznane rodzaje placówek: {', '.join(unknown)}."}
            )
        low, high = self.grade_range()
        if low > high:
            raise ValidationError({"grade_max": "Klasa „do” nie może być mniejsza niż klasa „od”."})


class CommitteeStatus(models.TextChoices):
    PENDING = "PENDING", "oczekuje"
    ACTIVE = "ACTIVE", "aktywny"
    SUSPENDED = "SUSPENDED", "zawieszony"


class CommitteeMember(models.Model):
    """Profil członka komitetu (recenzent, ewentualnie komisja odwoławcza)."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="committee_member")
    #: Komitet jest komitetem **tego** konkursu – razem z ``district`` i ``is_appeals_committee``:
    #: „zweryfikowany recenzent” jest oświadczeniem jednego organizatora o jednej osobie.
    #: ``NOT NULL`` od wydania D (§ 4.1), ``PROTECT`` jak przy uczestniku.
    competition = models.ForeignKey(
        "tenancy.Competition",
        verbose_name="konkurs",
        db_index=True,
        on_delete=models.PROTECT,
        related_name="committee_members",
    )
    district = models.CharField(  # noqa: DJ001
        "województwo", max_length=100, null=True, blank=True, choices=Voivodeship.choices
    )
    # Flaga mówi wyłącznie, skąd wzięła się wartość pola ``district``: ``True`` dostaje profil
    # z województwem narzuconym przez kod zaproszenia albo ustalonym przez koordynatora
    # (``POST /api/auth/committee/{id}/verify-district/``), ``False`` – deklarację z rejestracji.
    # Przydziału nie bramkuje: województwo członka komitetu jest opcjonalne (decyzja organizatora),
    # a reguła konfliktu interesów porównuje samo ``district`` – patrz
    # ``apps.grading.services.has_district_conflict``.
    district_verified = models.BooleanField("województwo zweryfikowane", default=False)
    # Region z podziału konkursu – obok ``district``, na tych samych zasadach co u uczestnika.
    # ``district_verified`` **nie** dotyczy tego pola i nie zmienia swojej roli: mówi wyłącznie,
    # skąd wzięła się wartość ``district`` (decyzja organizatora z ``docs/BACKLOG.md``).
    region = models.ForeignKey(
        "accounts.Region",
        verbose_name="region",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="committee_members",
    )
    status = models.CharField(
        "status", max_length=16, choices=CommitteeStatus.choices, default=CommitteeStatus.PENDING
    )
    is_appeals_committee = models.BooleanField("komisja odwoławcza", default=False)
    created_at = models.DateTimeField("utworzony", default=timezone.now)
    approved_at = models.DateTimeField("zatwierdzony", null=True, blank=True)
    approved_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="committee_approvals"
    )

    #: Własna kolumna konkursu – domyślna ścieżka queryseta.
    objects = CompetitionScopedManager()

    class Meta:
        verbose_name = "członek komitetu"
        verbose_name_plural = "członkowie komitetu"
        ordering = ("created_at", "id")

    def __str__(self) -> str:
        return f"{self.user.email} ({self.status})"

    @property
    def is_active_reviewer(self) -> bool:
        return self.status == CommitteeStatus.ACTIVE


class SchoolSupervisor(models.Model):
    """Profil opiekuna szkolnego: nauczyciela, który prowadzi uczniów do olimpiady.

    Po co osobna rola, skoro opiekun nie ma żadnych uprawnień do prac: bo dziś jedyną drogą do
    informacji „czy mój uczeń oddał pracę i czy przeszedł dalej” jest zapytanie ucznia. Szkoła
    planuje wyjazd na finał, zwolnienia z lekcji i sprawozdanie do dyrekcji – a robi to na
    podstawie SMS-ów od nastolatków. Panel opiekuna zamienia to w jedną listę, i **tylko** w nią:
    nie ma tu prac, punktów przed publikacją ani żadnej czynności, która zmieniałaby przebieg
    zawodów.

    Dowiązanie do szkoły jest podwójne z tego samego powodu, co u uczestnika: wykaz SIO nie zna
    wszystkich placówek, więc ``school`` (wolny tekst) jest wypełniony zawsze, a ``school_ref``
    tylko wtedy, gdy nauczyciel wybrał szkołę z listy. ``PROTECT`` po stronie rejestru, bo
    wygaszony wiersz słownika nie może zabrać profilowi informacji o szkole.

    ``verified`` jest **oświadczeniem sprawdzonym przez organizatora**, a nie stanem konta:
    każdy może wpisać, że uczy w XIV LO. Flaga nie bramkuje panelu (opiekun widzi wyłącznie
    uczniów, którzy sami podali jego adres – to oni są tu źródłem uprawnienia), ale rozstrzyga
    o tym, czy organizator wystawi tej osobie zaświadczenie dla opiekuna.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="school_supervisor")
    #: Profil opiekuna jest per konkurs, bo ``verified`` jest oświadczeniem sprawdzonym przez
    #: **tego** organizatora, a ``SchoolParticipation`` dotyczy edycji konkretnego konkursu
    #: (``docs/UNIWERSALNY-ETAP-1.md`` § 8, D3). Nauczyciel ma jedno konto i wiele profili.
    #: ``NOT NULL`` od wydania D (§ 4.1).
    competition = models.ForeignKey(
        "tenancy.Competition",
        verbose_name="konkurs",
        db_index=True,
        on_delete=models.PROTECT,
        related_name="school_supervisors",
    )
    school = models.CharField("szkoła", max_length=255, blank=True)
    school_ref = models.ForeignKey(
        "schools.School",
        verbose_name="szkoła z rejestru",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="supervisors",
    )
    phone = models.CharField("telefon", max_length=32, blank=True)
    verified = models.BooleanField("dane szkoły zweryfikowane", default=False)
    created_at = models.DateTimeField("utworzony", default=timezone.now)

    #: Własna kolumna konkursu – domyślna ścieżka queryseta.
    objects = CompetitionScopedManager()

    class Meta:
        verbose_name = "opiekun szkolny"
        verbose_name_plural = "opiekunowie szkolni"
        ordering = ("created_at", "id")

    def __str__(self) -> str:
        return f"{self.user.email} ({self.school or 'bez szkoły'})"

    @property
    def display_school(self) -> str:
        """Nazwa szkoły do pokazania – pusty wpis ma swoją etykietę, a nie pustą komórkę."""
        return self.school or "— nie podano —"


class SchoolParticipation(models.Model):
    """Potwierdzenie opiekuna, że szkoła bierze udział w danej edycji.

    Jest to **oświadczenie**, nie zgoda i nie warunek startu ucznia: uczestnik zapisuje się sam
    i nikt tego potwierdzenia nie sprawdza, zanim przyjmie jego pracę. Organizator potrzebuje go
    do czego innego – do policzenia szkół, które świadomie prowadzą uczniów, i do wystawienia
    zaświadczeń dla opiekunów (bez tego trzeba by uznać za opiekuna każdy adres wpisany przez
    ucznia w formularzu rejestracji).

    Jeden wiersz na parę (opiekun, edycja): potwierdzenie dotyczy konkretnego rocznika zawodów,
    a nie „w ogóle”. Nauczyciel, który w tym roku nie prowadzi nikogo, po prostu nie potwierdza.

    Odwołanie potwierdzenia jest skasowaniem wiersza, a nie znacznikiem: oświadczenie „szkoła
    bierze udział” albo obowiązuje, albo nie – historia jego zmian mieszka w audycie.
    """

    supervisor = models.ForeignKey(SchoolSupervisor, on_delete=models.CASCADE, related_name="participations")
    edition = models.ForeignKey(
        "competitions.Edition", on_delete=models.CASCADE, related_name="school_participations"
    )
    confirmed_at = models.DateTimeField("potwierdzone", default=timezone.now)

    class Meta:
        verbose_name = "udział szkoły w edycji"
        verbose_name_plural = "udziały szkół w edycjach"
        ordering = ("-confirmed_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=["supervisor", "edition"], name="accounts_school_participation_unique"
            )
        ]

    def __str__(self) -> str:
        return f"{self.supervisor_id} @ {self.edition_id}"


class InvitationGrantsStatus(models.TextChoices):
    ACTIVE = CommitteeStatus.ACTIVE.value, "od razu aktywny"
    PENDING = CommitteeStatus.PENDING.value, "wymaga zatwierdzenia"


class InvitationStatus(models.TextChoices):
    """Stan zaproszenia **wyliczany** z pól kodu – nie ma go w bazie i nie da się go ustawić ręcznie.

    Osobne pole statusu musiałoby być utrzymywane w zgodzie z ``used_count``, ``expires_at``
    i ``revoked_at`` przy każdej ścieżce zapisu (rejestracja, panel, komenda CLI, upływ czasu),
    a upływu czasu żaden zapis i tak nie zauważy. Wyliczanie z faktów nie może się rozjechać.
    """

    USED = "used", "użyte"
    REVOKED = "revoked", "unieważnione"
    EXPIRED = "expired", "wygasłe"
    PENDING = "pending", "wysłane"


class InvitationCode(models.Model):
    """Kod zaproszenia do komitetu. W bazie wyłącznie sha256 – kodu nie da się odtworzyć."""

    code_hash = models.CharField("sha256 kodu", max_length=64, unique=True, editable=False)
    #: Kod nadaje status w komitecie **jednego** konkursu, więc i sam należy do tego konkursu:
    #: bez tej kolumny zaproszenie wystawione przez organizatora A wpuszczałoby recenzenta do
    #: komitetu B. ``NOT NULL`` od wydania D (§ 4.1).
    competition = models.ForeignKey(
        "tenancy.Competition",
        verbose_name="konkurs",
        db_index=True,
        on_delete=models.PROTECT,
        related_name="invitation_codes",
    )
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="invitations_created")
    created_at = models.DateTimeField("utworzony", default=timezone.now)
    expires_at = models.DateTimeField("wygasa")
    max_uses = models.PositiveSmallIntegerField("limit użyć", default=1)
    used_count = models.PositiveSmallIntegerField("liczba użyć", default=0)
    grants_status = models.CharField(
        "nadawany status",
        max_length=16,
        choices=InvitationGrantsStatus.choices,
        default=InvitationGrantsStatus.ACTIVE,
    )
    is_appeals = models.BooleanField("uprawnia do komisji odwoławczej", default=False)
    # Okręg narzucony przez koordynatora przy generowaniu kodu. Jeśli jest ustawiony, wygrywa z
    # deklaracją z formularza rejestracji, a profil powstaje od razu z ``district_verified=True``.
    district = models.CharField(  # noqa: DJ001
        "województwo", max_length=100, null=True, blank=True, choices=Voivodeship.choices
    )
    # Region narzucony przez koordynatora – obok ``district``, na tych samych zasadach: przy
    # wyłączonej fladze ``custom_regions`` kod zaproszenia niesie wyłącznie województwo.
    region = models.ForeignKey(
        "accounts.Region",
        verbose_name="region",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="invitation_codes",
    )
    # Adres, na który kod pojechał listem. Puste dla kodów wygenerowanych „do ręki” (komenda CLI,
    # sekcja „Kod zaproszenia” w panelu) – tam kod przekazuje człowiek i serwis nie wie komu.
    # Adres jest tu jedyną wskazówką, kogo dotyczy wiersz „unieważnij / wyślij ponownie”; bez niego
    # koordynator miałby przed sobą listę skrótów sha256.
    email = models.EmailField(  # noqa: DJ001
        "adres, na który wysłano", null=True, blank=True, db_index=True
    )
    sent_at = models.DateTimeField("wysłano", null=True, blank=True)
    # Unieważnienie jest znacznikiem, a nie skasowaniem wiersza: po pomyłkowym zaproszeniu musi
    # zostać ślad, że kod istniał i został odebrany, inaczej audyt nie tłumaczy własnych wpisów.
    revoked_at = models.DateTimeField("unieważniono", null=True, blank=True)

    #: Własna kolumna konkursu – domyślna ścieżka queryseta.
    objects = CompetitionScopedManager()

    class Meta:
        verbose_name = "kod zaproszenia"
        verbose_name_plural = "kody zaproszeń"
        ordering = ("-created_at", "id")

    def __str__(self) -> str:
        return f"zaproszenie {self.code_hash[:8]}… ({self.used_count}/{self.max_uses})"

    def is_usable(self, now=None) -> bool:
        """Czy kod wolno jeszcze zużyć. Jedyne miejsce, w którym ta reguła jest zapisana.

        ``revoked_at`` wchodzi tu razem z terminem i limitem użyć, a nie osobnym warunkiem
        w ``redeem_invitation``: gdyby unieważnienie sprawdzała wyłącznie rejestracja, każdy
        następny czytelnik kodu (panel, admin, przyszłe API) musiałby pamiętać o dopisaniu go
        po swojej stronie – a pierwszy, który zapomni, wpuści unieważniony kod.
        """
        now = now or timezone.now()
        return self.revoked_at is None and self.expires_at > now and self.used_count < self.max_uses

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def status(self, now=None) -> str:
        """Stan kodu w kolejności rozstrzygania: użyte → unieważnione → wygasłe → wysłane.

        Kolejność nie jest dowolna. Kod **zużyty** opisuje fakt, który już się wydarzył (ktoś
        założył na niego konto), więc wygrywa z każdym późniejszym unieważnieniem i z upływem
        terminu – po tych dwóch nie da się konta cofnąć. Unieważnienie wyprzedza wygaśnięcie,
        bo mówi o decyzji człowieka, a nie o tym, że minął czas.
        """
        if self.used_count >= self.max_uses:
            return InvitationStatus.USED
        if self.revoked_at is not None:
            return InvitationStatus.REVOKED
        if self.expires_at <= (now or timezone.now()):
            return InvitationStatus.EXPIRED
        return InvitationStatus.PENDING

    @property
    def status_label(self) -> str:
        """Etykieta stanu dla szablonu – ``status`` jest wyliczany, więc nie ma ``get_..._display``."""
        return InvitationStatus(self.status()).label


class BroadcastGroup(models.TextChoices):
    """Grupy odbiorców komunikatu organizatora.

    Lista jest zamknięta z premedytacją. Pole „wpisz zapytanie do bazy” dawałoby koordynatorowi
    możliwość wysłania listu do dowolnego zbioru osób, a każda taka wysyłka jest przetwarzaniem
    danych kontaktowych w celu, który trzeba umieć nazwać. Tutaj cel jest nazwany etykietą grupy
    i to on trafia do ``MessageBroadcast.group``, czyli do rejestru wysyłek.

    ``CUSTOM`` (wklejona lista adresów) jest wyjątkiem świadomym: organizator musi móc odpisać
    grupie osób, której system nie zna (opiekunowie, patroni, dziennikarze). Adresy z tej listy
    **nie są nigdzie zapisywane** – jadą prosto do zadania wysyłkowego.

    **Kolejność członków jest kolejnością listy wyboru na ekranie** i jest decyzją, a nie
    przypadkiem. Na górze stoi grupa najszersza i najczęściej potrzebna („wszyscy uczestnicy
    konkursu” – prośba organizatora z 24.09.2026), pod nią zawężenia uczestników, potem opiekunowie
    i komitet, a na końcu wklejona lista – wyjątek od zasady zamkniętej listy.

    Grupy z parametrem (etap, region, szkoła, klasa, warsztat) zapisują ten parametr obok grupy
    (``MessageBroadcast.target``): sama etykieta „uczestnicy z wybranej szkoły” w historii wysyłek
    nie odpowiada na pytanie, **której** szkoły dotyczył list.
    """

    ALL_PARTICIPANTS = "ALL_PARTICIPANTS", "wszyscy uczestnicy konkursu"
    EDITION_PARTICIPANTS = "EDITION_PARTICIPANTS", "uczestnicy bieżącej edycji (zapisani do etapu)"
    STAGE_REGISTERED = "STAGE_REGISTERED", "zapisani do etapu"
    STAGE_QUALIFIED = "STAGE_QUALIFIED", "zakwalifikowani do etapu"
    STAGE_NO_SUBMISSION = "STAGE_NO_SUBMISSION", "zapisani do etapu, bez wysłanej pracy"
    REGION_PARTICIPANTS = "REGION_PARTICIPANTS", "uczestnicy z wybranego województwa (regionu)"
    SCHOOL_PARTICIPANTS = "SCHOOL_PARTICIPANTS", "uczestnicy z wybranej szkoły (placówki)"
    GRADE_PARTICIPANTS = "GRADE_PARTICIPANTS", "uczestnicy z wybranej klasy"
    WORKSHOP_ATTENDEES = "WORKSHOP_ATTENDEES", "uczestnicy obecni na wybranym warsztacie"
    SUPERVISORS = "SUPERVISORS", "opiekunowie szkolni (nauczyciele)"
    COMMITTEE = "COMMITTEE", "członkowie komitetu"
    COMMITTEE_DISTRICT = "COMMITTEE_DISTRICT", "komitet jednego województwa"
    CUSTOM = "CUSTOM", "wklejona lista adresów"


class BroadcastStatus(models.TextChoices):
    """Stan wysyłki. ``SENT`` znaczy „wszystkie listy trafiły do kolejki”, nie „doręczono”.

    Doręczenia ta tabela nie zna i znać nie może: o tym, czy list dotarł, rozstrzyga serwer
    odbiorcy, a my dostajemy najwyżej zwrotkę do skrzynki organizatora. Rozróżnienie jest
    w etykietach, żeby nikt nie wziął „wysłana” za dowód doręczenia.
    """

    QUEUED = "QUEUED", "w kolejce"
    SENT = "SENT", "przekazana do wysyłki"
    FAILED = "FAILED", "nieudana"


class MessageBroadcast(models.Model):
    """Rejestr komunikatów rozesłanych przez organizatora (jeden wiersz = jedna wysyłka).

    Po co w ogóle zapis, skoro listy i tak wychodzą: bez niego nie da się odpowiedzieć na pytanie
    „czy uczestnicy dostali informację o przesunięciu terminu i kiedy”. Audyt odnotowuje sam fakt
    (``broadcast.sent`` z licznikami), ale nie trzyma treści – a to treść jest tu przedmiotem
    sporu, gdy ktoś twierdzi, że nic nie dostał albo że dostał co innego.

    Czego w tabeli **nie ma**: listy odbiorców. Wiersz niesie grupę i liczbę adresatów, nigdy
    adresy. Grupę da się odtworzyć zapytaniem, gdyby kiedyś trzeba było sprawdzić, kto się w niej
    mieścił, a przechowywanie kopii adresów przy każdym komunikacie mnożyłoby zbiory danych
    kontaktowych bez żadnego pożytku.

    ``sent_count`` rośnie w miarę jak kolejne porcje trafiają do kolejki wysyłkowej; przy
    wysyłce, która padła w połowie, różnica względem ``recipient_count`` mówi, ile listów nie
    wyszło. ``SET_NULL`` przy autorze, bo skasowanie konta koordynatora nie może wymazać historii
    komunikatów wysłanych do tysięcy osób.
    """

    #: Rejestr wysyłek należy do organizatora, który je zrobił. Grupy odbiorców są z definicji
    #: zakresowane (``EDITION_PARTICIPANTS`` to uczestnicy bieżącej edycji **tego** konkursu),
    #: więc wiersz bez właściciela nie dałby się odczytać: „ilu odbiorców” zależy od tego, czyja
    #: to była edycja. ``NOT NULL`` od wydania D (§ 4.1).
    competition = models.ForeignKey(
        "tenancy.Competition",
        verbose_name="konkurs",
        db_index=True,
        on_delete=models.PROTECT,
        related_name="broadcasts",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="broadcasts",
        verbose_name="wysłał",
    )
    created_at = models.DateTimeField("wysłana", default=timezone.now, db_index=True)
    group = models.CharField("grupa odbiorców", max_length=32, choices=BroadcastGroup.choices)
    #: Parametr grupy **tak, jak wyglądał w chwili wysyłki**: ``{"stage": 12, "label": "etap I
    #: (eliminacje)"}``, ``{"school": "sio:345", "label": "XIV LO im. …, Warszawa"}`` itd. Pusty
    #: słownik przy grupach bez parametru (komitet, opiekunowie, wklejona lista). Grupy zależne od
    #: edycji (wszyscy uczestnicy, region, szkoła, klasa) niosą też ``"past_editions": true/false`` –
    #: czy list objął wyłącznie bieżącą edycję, czy także poprzednie.
    #:
    #: Po co kopia etykiety, skoro jest identyfikator: bo historia ma odpowiadać na pytanie „do kogo
    #: poszło”, także wtedy, gdy etap przemianowano, harmonogram warsztatów zredagowano, a szkoła
    #: zniknęła ze słownika. Identyfikator zostaje obok, żeby grupę dało się odtworzyć zapytaniem.
    #:
    #: Czego tu **nie ma i nie będzie**: adresów. Parametr opisuje grupę (szkoła, region, warsztat),
    #: nigdy osobę – ta sama zasada, co przy ``recipient_count`` zamiast listy odbiorców.
    target = models.JSONField("parametry grupy", default=dict, blank=True)
    subject = models.CharField("temat", max_length=200)
    body = models.TextField("treść")
    recipient_count = models.PositiveIntegerField("liczba odbiorców", default=0)
    sent_count = models.PositiveIntegerField("przekazanych do wysyłki", default=0)
    status = models.CharField(
        "stan", max_length=16, choices=BroadcastStatus.choices, default=BroadcastStatus.QUEUED
    )

    #: Własna kolumna konkursu – domyślna ścieżka queryseta.
    objects = CompetitionScopedManager()

    class Meta:
        verbose_name = "komunikat"
        verbose_name_plural = "komunikaty"
        ordering = ("-created_at", "-id")

    def __str__(self) -> str:
        return f"{self.subject} → {self.recipient_count} odbiorców"

    @property
    def target_label(self) -> str:
        """Parametr grupy do pokazania w historii – pusty napis, gdy grupa parametru nie ma."""
        return str((self.target or {}).get("label") or "")


# Drugi składnik logowania (TOTP) mieszka razem z resztą swojej logiki w ``apps.accounts.twofactor``
# – model, protokół, warstwa wymuszająca i czynności w jednym pliku, bo czyta się je wyłącznie
# razem. Django rejestruje modele wtedy, gdy importuje ``models`` aplikacji, więc bez tej linijki
# ``makemigrations`` nie zobaczyłby tabeli. Import stoi na końcu pliku: ``twofactor`` nie sięga
# do niczego z tego modułu w czasie importu (klucz obcy podaje przez ``settings.AUTH_USER_MODEL``),
# ale kolejność i tak ma być jednoznaczna.
from .twofactor import TwoFactorDevice  # noqa: E402,F401  (import dla rejestracji modelu)
