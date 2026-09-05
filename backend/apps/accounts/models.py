"""Model użytkownika (e-mail jako login), profile ról i kody zaproszeń.

Zasady:
- kod zaproszenia nigdy nie jest przechowywany jawnie – w bazie jest wyłącznie sha256,
- czas zawsze przez ``django.utils.timezone.now()``,
- dostęp do danych wyłącznie przez ORM.
"""

import hashlib
import secrets

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

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


def generate_public_code() -> str:
    """Losowy, anonimowy identyfikator uczestnika w formacie ``OLM-XXXXXX``."""
    suffix = "".join(secrets.choice(PUBLIC_CODE_ALPHABET) for _ in range(PUBLIC_CODE_RANDOM_LENGTH))
    return f"{PUBLIC_CODE_PREFIX}{suffix}"


def hash_invitation_code(plain_code: str) -> str:
    """sha256 kodu zaproszenia. Jedyna postać kodu, jaka trafia do bazy."""
    return hashlib.sha256(plain_code.strip().encode("utf-8")).hexdigest()


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
    school = models.CharField("szkoła", max_length=200)
    district = models.CharField("okręg", max_length=100)
    birth_year = models.PositiveSmallIntegerField("rok urodzenia")
    gdpr_consent_at = models.DateTimeField("zgoda RODO z dnia")
    guardian_consent = models.BooleanField("zgoda opiekuna", default=False)
    publish_full_name = models.BooleanField("zgoda na publikację pełnych danych", default=False)

    class Meta:
        verbose_name = "uczestnik"
        verbose_name_plural = "uczestnicy"
        ordering = ("public_code",)

    def __str__(self) -> str:
        return self.public_code


class CommitteeStatus(models.TextChoices):
    PENDING = "PENDING", "oczekuje"
    ACTIVE = "ACTIVE", "aktywny"
    SUSPENDED = "SUSPENDED", "zawieszony"


class CommitteeMember(models.Model):
    """Profil członka komitetu (recenzent, ewentualnie komisja odwoławcza)."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="committee_member")
    district = models.CharField("okręg", max_length=100, null=True, blank=True)  # noqa: DJ001
    # Okręg samodeklarowany przy rejestracji nie jest podstawą do wykluczania konfliktu interesów.
    # ``True`` dostaje wyłącznie profil z okręgiem narzuconym przez kod zaproszenia albo potwierdzony
    # przez koordynatora (``POST /api/auth/committee/{id}/verify-district/``). Przydział na etapie
    # okręgowym pomija recenzentów niezweryfikowanych – patrz ``apps.grading.services.assign_reviewers``.
    district_verified = models.BooleanField("okręg zweryfikowany", default=False)
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
    district = models.CharField("okręg", max_length=100, null=True, blank=True)  # noqa: DJ001

    class Meta:
        verbose_name = "kod zaproszenia"
        verbose_name_plural = "kody zaproszeń"
        ordering = ("-created_at", "id")

    def __str__(self) -> str:
        return f"zaproszenie {self.code_hash[:8]}… ({self.used_count}/{self.max_uses})"

    def is_usable(self, now=None) -> bool:
        now = now or timezone.now()
        return self.expires_at > now and self.used_count < self.max_uses
