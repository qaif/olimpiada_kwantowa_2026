"""Polityka drugiego składnika konkursu i okres przejściowy konta (SEC-01 § 2–3).

Dwie małe tabele, obie puste na instalacji z wyłączonym ``TWO_FACTOR_ENABLED``: warstwa wymuszająca
nie dochodzi wtedy do kodu, który je czyta lub zapisuje.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class PolicyMode(models.TextChoices):
    """Skąd biorą się wymagane role konkursu.

    ``auto`` jest wartością domyślną (także dla konkursu bez wiersza): wymóg rośnie sam razem
    z funkcjami, które wnoszą dane wrażliwe (delegacje, płatności, logistyka, nadzór). Konkurs bez
    nich nie wymaga niczego ponad politykę platformy – Olimpiada Kwantowa po włączeniu funkcji
    zachowuje się więc tak, jak dotąd, dopóki organizator nie zdecyduje inaczej.
    """

    AUTO = "auto", "automatycznie (personel konkursów z danymi wrażliwymi)"
    CUSTOM = "custom", "wybrane role"


class TwoFactorPolicy(models.Model):
    """Ustawienie konkursu: kto z personelu musi mieć drugi składnik. Brak wiersza = ``auto``.

    Osobny model, a nie kolumna ``Competition`` ani wpis w ``feature_flags``: to nie jest
    przełącznik funkcji, tylko ustawienie z kilkoma polami, które czyta wyłącznie ta aplikacja –
    wzorzec ``ForumSettings``/``ChatSettings``.
    """

    competition = models.OneToOneField(
        "tenancy.Competition",
        on_delete=models.CASCADE,
        related_name="two_factor_policy",
        verbose_name="konkurs",
    )
    mode = models.CharField("tryb", max_length=8, choices=PolicyMode.choices, default=PolicyMode.AUTO)
    # Klucze ról z ``apps.staff_mfa.policy.ROLE_KEYS``. Lista JSON, a nie M2M do grup: część kluczy
    # nie jest grupą Django (``logistics``, ``admin``), a lista zmienia się wyłącznie w całości.
    roles = models.JSONField("wymagane role (tryb „wybrane”)", default=list, blank=True)
    grace_days = models.PositiveSmallIntegerField(
        "okres przejściowy (dni)",
        null=True,
        blank=True,
        help_text="Puste = wartość platformy (TWO_FACTOR_GRACE_DAYS). Zero = wymóg od razu.",
    )
    allow_remember = models.BooleanField(
        "pozwól zapamiętać urządzenie",
        default=True,
        help_text="Wyłączone: kod przy każdym logowaniu, niezależnie od TWO_FACTOR_REMEMBER_DAYS.",
    )
    updated_at = models.DateTimeField("zmieniono", auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="zmienił(a)",
    )

    class Meta:
        verbose_name = "polityka 2FA konkursu"
        verbose_name_plural = "polityki 2FA konkursów"

    def __str__(self) -> str:
        return f"2FA: {self.competition_id} ({self.mode})"


class TwoFactorGrace(models.Model):
    """Chwila, w której warstwa wymuszająca pierwszy raz zobaczyła wymóg 2FA dla tego konta.

    Jeden wiersz na konto i **na zawsze** (do usunięcia konta): okres przejściowy jest jednorazowy.
    Wyłączenie 2FA, reset przez organizatora ani zmiana ról go nie odnawiają – inaczej „wyłącz
    i poczekaj” byłoby trwałym obejściem wymogu. Konto po resecie i tak konfiguruje drugi składnik
    samo, zaraz po zalogowaniu hasłem, więc odnowienie nie jest do niczego potrzebne.

    Per konto, a nie per konkurs: urządzenie jest jedno dla wszystkich konkursów, więc i termin
    na jego konfigurację jest jeden. Długość okresu bierzemy z konkursu żądania (polityka).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="two_factor_grace",
        verbose_name="konto",
    )
    required_since = models.DateTimeField("wymagane od", default=timezone.now)

    class Meta:
        verbose_name = "okres przejściowy 2FA"
        verbose_name_plural = "okresy przejściowe 2FA"

    def __str__(self) -> str:
        return f"2FA grace: {self.user_id} od {self.required_since:%Y-%m-%d}"
