"""Reguła automatycznych zaświadczeń z warsztatów i rejestr dokumentów, które z niej wyszły (WS-CERT-02).

Dlaczego dwa małe modele w osobnej aplikacji, a nie pola w ``results.Certificate`` i w edycji.
Dokument jest i zostaje dokumentem z ``apps.results`` – tym samym, który wystawia ręczne „Wystaw
(ZIP)”, z tym samym numerem i kodem. Ta aplikacja dokłada wyłącznie **decyzję organizatora**
(od ilu warsztatów, jakim szablonem, czy pisać do uczestnika) i **ślad**, że dokument wystawiła
reguła, a nie człowiek. Kolumna „automatycznie” w rejestrze dyplomów dotyczyłaby jednego rodzaju
z dziewięciu, a zmiana modelu wspólnego dla wszystkich dyplomów kosztowałaby migrację tabeli, którą
czyta strona weryfikacji.
"""

from __future__ import annotations

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from apps.competitions.scoping import competition_scoped_manager

#: Górna granica progu. Harmonogram jednej edycji ma kilkanaście zajęć; liczba spoza tego rzędu jest
#: literówką („30” zamiast „3”), a nie regułą – formularz ma ją zatrzymać, zanim nikt nic nie dostanie.
MAX_THRESHOLD = 100


class AutoIssueTrigger(models.TextChoices):
    """Co uruchomiło przebieg reguły – do rejestru i audytu, nie do decyzji."""

    ATTENDANCE = "attendance", "zapis obecności"
    RECALCULATE = "recalculate", "przelicz teraz"


class AutoIssuePolicy(models.Model):
    """Ustawienia automatycznych zaświadczeń z warsztatów **jednej edycji**.

    Edycja, a nie konkurs: dokument należy do edycji (jeden na osobę i edycję), szablony dopasowuje
    się po (rodzaj, edycja), a ekrany zaświadczeń pracują na edycji bieżącej. Reguła z zeszłego roku
    nie może po cichu obowiązywać w nowym – inny harmonogram, często inny próg. Brak wiersza znaczy
    „wyłączone” i jest stanem domyślnym każdej edycji.
    """

    edition = models.OneToOneField(
        "competitions.Edition",
        on_delete=models.CASCADE,
        related_name="workshop_certificate_policy",
        verbose_name="edycja",
    )
    enabled = models.BooleanField("wystawiaj zaświadczenie automatycznie", default=False)
    min_workshops = models.PositiveSmallIntegerField(
        "próg: co najmniej tyle warsztatów",
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(MAX_THRESHOLD)],
        help_text="Liczą się różne warsztaty z harmonogramu na stronie „Warsztaty”.",
    )
    # ``SET_NULL``: usunięcie szablonu nie może zabrać reguły – dokumenty wracają wtedy do zwykłego
    # dopasowania szablonu (rodzaj, edycja), czyli do stanu sprzed wyboru.
    template = models.ForeignKey(
        "results.CertificateTemplate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="szablon graficzny",
        help_text="Puste = szablon dopasowany jak dla każdego dokumentu (rodzaj „warsztaty”, edycja).",
    )
    notify_participants = models.BooleanField("wyślij list „Twoje zaświadczenie jest gotowe”", default=False)
    updated_at = models.DateTimeField("zmieniono", default=timezone.now)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="zmienił",
    )

    objects = competition_scoped_manager("edition__competition")

    class Meta:
        verbose_name = "reguła zaświadczeń z warsztatów"
        verbose_name_plural = "reguły zaświadczeń z warsztatów"

    def __str__(self) -> str:
        return f"Zaświadczenia z warsztatów: {self.edition_id}"


class AutoIssuedCertificate(models.Model):
    """Ślad, że ten dokument wystawiła reguła – licznik na ekranie i jeden list na dokument.

    Klucz główny to sam dokument: wystawienie automatyczne jest cechą dokumentu, a nie zdarzeniem,
    które mogłoby się powtórzyć. ``notified_at`` pilnuje, żeby list „gotowe” wyszedł raz, także gdy
    zadanie dostarczono dwa razy.
    """

    certificate = models.OneToOneField(
        "results.Certificate",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="workshop_auto_issue",
        verbose_name="dokument",
    )
    # Kopia klucza z dokumentu, żeby licznik na ekranie był jednym zapytaniem po indeksie edycji.
    edition = models.ForeignKey(
        "competitions.Edition",
        on_delete=models.CASCADE,
        related_name="+",
        verbose_name="edycja",
    )
    trigger = models.CharField("wyzwalacz", max_length=16, choices=AutoIssueTrigger.choices)
    threshold = models.PositiveSmallIntegerField("próg w chwili wystawienia")
    issued_at = models.DateTimeField("wystawiono", default=timezone.now)
    notified_at = models.DateTimeField("list wysłany", null=True, blank=True)

    objects = competition_scoped_manager("edition__competition")

    class Meta:
        verbose_name = "zaświadczenie z warsztatów wystawione automatycznie"
        verbose_name_plural = "zaświadczenia z warsztatów wystawione automatycznie"
        ordering = ("-issued_at",)

    def __str__(self) -> str:
        return f"auto: {self.certificate_id}"
