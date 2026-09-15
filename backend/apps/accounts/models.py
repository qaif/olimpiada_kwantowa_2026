"""Model użytkownika (e-mail jako login), profile ról i kody zaproszeń.

Zasady:
- kod zaproszenia nigdy nie jest przechowywany jawnie – w bazie jest wyłącznie sha256,
- czas zawsze przez ``django.utils.timezone.now()``,
- dostęp do danych wyłącznie przez ORM.
"""

import hashlib
import re
import secrets

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from apps.core.text import fold as _fold

from .consents import ConsentKind, ConsentSource

# Grupy RBAC tworzone migracją danych (apps/accounts/migrations/0002_rbac_groups.py).
GROUP_PARTICIPANT = "participant"
GROUP_REVIEWER = "reviewer"
GROUP_APPEALS = "appeals"
GROUP_COORDINATOR = "coordinator"
RBAC_GROUPS = (GROUP_PARTICIPANT, GROUP_REVIEWER, GROUP_APPEALS, GROUP_COORDINATOR)

# Alfabet bez znaków mylących (0/O, 1/I/L) – kod bywa przepisywany ręcznie z listy wyników.
PUBLIC_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PUBLIC_CODE_PREFIX = "OLM-"
PUBLIC_CODE_RANDOM_LENGTH = 6

# Klasa uczestnika. Pięć roczników, bo tyle trwa najdłuższa szkoła ponadpodstawowa (technikum);
# liceum kończy się na czwartej, a szkoła branżowa I stopnia na trzeciej – węższych limitów
# nie narzucamy, bo zależą od typu szkoły, a ten bywa wpisany ręcznie i nie da się go sprawdzić.
MIN_GRADE = 1
MAX_GRADE = 5
GRADE_CHOICES = [(number, str(number)) for number in range(MIN_GRADE, MAX_GRADE + 1)]


def generate_public_code() -> str:
    """Losowy, anonimowy identyfikator uczestnika w formacie ``OLM-XXXXXX``."""
    suffix = "".join(secrets.choice(PUBLIC_CODE_ALPHABET) for _ in range(PUBLIC_CODE_RANDOM_LENGTH))
    return f"{PUBLIC_CODE_PREFIX}{suffix}"


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


class Participant(models.Model):
    """Profil uczestnika. ``public_code`` jest jedynym identyfikatorem w publikowanych wynikach."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="participant")
    public_code = models.CharField("kod publiczny", max_length=16, unique=True, default=generate_public_code)
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
    # Nullowalna wyłącznie ze względu na profile sprzed wprowadzenia pola – formularz, API
    # i serwis rejestracji wymagają klasy od każdego nowego uczestnika.
    grade = models.PositiveSmallIntegerField("klasa", choices=GRADE_CHOICES, null=True, blank=True)
    district = models.CharField("województwo", max_length=100, choices=Voivodeship.choices)
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
    gdpr_consent_at = models.DateTimeField("zgoda RODO z dnia")
    #: Akceptacja regulaminu. Nullowalne wyłącznie ze względu na profile sprzed wprowadzenia
    #: zestawu zgód – od tej zmiany żadna droga rejestracji nie przepuszcza pustej wartości.
    terms_accepted_at = models.DateTimeField("regulamin zaakceptowany", null=True, blank=True)
    guardian_consent = models.BooleanField("zgoda opiekuna", default=False)
    publish_full_name = models.BooleanField("zgoda na publikację pełnych danych", default=False)

    class Meta:
        verbose_name = "uczestnik"
        verbose_name_plural = "uczestnicy"
        ordering = ("public_code",)

    def __str__(self) -> str:
        return self.public_code


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
    """

    participant = models.ForeignKey(
        Participant, on_delete=models.CASCADE, related_name="consents", verbose_name="uczestnik"
    )
    kind = models.CharField("rodzaj", max_length=20, choices=ConsentKind.choices)
    document_version = models.CharField("wersja dokumentu", max_length=100, blank=True)
    given_at = models.DateTimeField("wyrażona", default=timezone.now)
    withdrawn_at = models.DateTimeField("wycofana", null=True, blank=True)
    source = models.CharField("droga", max_length=16, choices=ConsentSource.choices)

    class Meta:
        verbose_name = "zgoda uczestnika"
        verbose_name_plural = "zgody uczestników"
        ordering = ("-given_at", "-id")
        indexes = [models.Index(fields=["participant", "kind"], name="accounts_consent_pk_idx")]

    def __str__(self) -> str:
        state = "wycofana" if self.withdrawn_at else "aktywna"
        return f"{self.get_kind_display()} ({state})"

    @property
    def is_active(self) -> bool:
        return self.withdrawn_at is None


class CommitteeStatus(models.TextChoices):
    PENDING = "PENDING", "oczekuje"
    ACTIVE = "ACTIVE", "aktywny"
    SUSPENDED = "SUSPENDED", "zawieszony"


class CommitteeMember(models.Model):
    """Profil członka komitetu (recenzent, ewentualnie komisja odwoławcza)."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="committee_member")
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
    status = models.CharField(
        "status", max_length=16, choices=CommitteeStatus.choices, default=CommitteeStatus.PENDING
    )
    is_appeals_committee = models.BooleanField("komisja odwoławcza", default=False)
    created_at = models.DateTimeField("utworzony", default=timezone.now)
    approved_at = models.DateTimeField("zatwierdzony", null=True, blank=True)
    approved_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="committee_approvals"
    )

    class Meta:
        verbose_name = "członek komitetu"
        verbose_name_plural = "członkowie komitetu"
        ordering = ("created_at", "id")

    def __str__(self) -> str:
        return f"{self.user.email} ({self.status})"

    @property
    def is_active_reviewer(self) -> bool:
        return self.status == CommitteeStatus.ACTIVE


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
