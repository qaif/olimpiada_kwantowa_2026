"""Zaświadczenie o statusie ucznia: skan podstemplowanego wzoru i decyzja koordynatora.

Prośba organizatora z 24.09.2026. Uczestnik pobiera z panelu **imienny** wzór zaświadczenia
(``apps.student_status.pdf``), szkoła wpisuje klasę, przystawia pieczątkę i podpisuje, a uczestnik
wgrywa zdjęcie albo skan tej kartki. Koordynator ogląda skan i akceptuje go albo odrzuca z powodem.
Status **nie blokuje** oddawania prac – jest wyłącznie filtrem przy pobieraniu paczek ZIP („tylko
uczniowie z potwierdzonym statusem”) i przypomnieniem w panelu.

**Status jest per uczestnik i per edycja, a nie per uczestnik.** Zaświadczenie poświadcza, że ktoś
jest uczniem **w danym roku szkolnym** – a edycja olimpiady jest właśnie rokiem szkolnym
(„I edycja 2026/2027”). Uczeń trzeciej klasy wraca w kolejnej edycji jako uczeń czwartej i jego
zeszłoroczne zaświadczenie niczego o tym roku nie mówi; absolwent, który zgłosi się ponownie, nie
może przejść na starym papierze. Klucz „tylko uczestnik” dawałby status wieczny, klucz „etap” –
cztery kartki z pieczątką na jeden rok. Profil uczestnika (``accounts.Participant``) jest już
profilem **jednego konkursu** (§ 3.3), więc para (uczestnik, edycja) jest zarazem parą w obrębie
jednego organizatora: edycja i profil muszą należeć do tego samego konkursu, pilnuje tego serwis.

**Wersje zamiast nadpisywania.** Każde wgranie to nowy wiersz z kolejnym numerem wersji; dokładnie
jeden wiersz na parę (uczestnik, edycja) ma ``is_current=True`` i to on jest „statusem”. Poprzedni
zostaje jako historia (kto, kiedy, jaki był powód odrzucenia), ale jego **plik** jest usuwany ze
storage zaraz po zastąpieniu – skan z datą urodzenia i podpisem dyrektora nie ma po zastąpieniu
żadnego celu, a historia decyzji go nie potrzebuje (art. 5 ust. 1 lit. c RODO). Brak wiersza
bieżącego znaczy „brak zaświadczenia”.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.competitions.scoping import competition_scoped_manager

#: Przełącznik konkursu, za którym stoi cała funkcja. Nazwa jest jedna i jest tutaj, żeby literówka
#: wywracała się w jednym miejscu (``Competition.has_feature`` podnosi ``KeyError`` na nieznanej).
FLAG = "student_status_certificate"


def enabled(competition) -> bool:
    """Czy ten konkurs zbiera zaświadczenia o statusie ucznia. **Jedyne** wejście do flagi.

    Brak konkursu to „nie wiadomo, czyj to panel” – i odpowiedź jest ta sama, co przy wyłączonej
    fladze: funkcji nie ma. Odczyt nie dotyka bazy (flaga jest polem wiersza konkursu, który
    ``CompetitionMiddleware`` ma już w pamięci), więc wołanie tego w każdym widoku jest darmowe.
    """
    return competition is not None and competition.has_feature(FLAG)


class CertificateStatus(models.TextChoices):
    """Stan jednego wgranego pliku. Stan „brak” nie jest wartością – to brak wiersza bieżącego."""

    PENDING = "PENDING", "oczekuje na weryfikację"
    ACCEPTED = "ACCEPTED", "zaakceptowane"
    REJECTED = "REJECTED", "odrzucone"
    #: Plik zastąpiony nowszym **zanim** ktokolwiek go rozpatrzył. Osobna wartość, bo historia
    #: z wierszem „oczekuje na weryfikację” pod wierszem bieżącym mówiłaby, że czekają dwa pliki.
    SUPERSEDED = "SUPERSEDED", "zastąpione nowszym plikiem"


#: Stan w panelu i na liście koordynatora, gdy nie ma ani jednego wgranego pliku.
STATE_MISSING = "MISSING"

#: Etykiety stanów widoku – czterech z zamówienia organizatora („brak / oczekuje na weryfikację /
#: zaakceptowane / odrzucone”). ``SUPERSEDED`` nie jest stanem widoku, tylko wpisem historii.
STATE_LABELS: dict[str, str] = {
    STATE_MISSING: "brak",
    CertificateStatus.PENDING: CertificateStatus.PENDING.label,
    CertificateStatus.ACCEPTED: CertificateStatus.ACCEPTED.label,
    CertificateStatus.REJECTED: CertificateStatus.REJECTED.label,
}


class ScanStatus(models.TextChoices):
    """Wynik skanu antywirusowego pliku – te same wartości, co ``submissions.AvStatus``.

    Kopia, a nie import: tamta klasa opisuje plik **rozwiązania**, a ten moduł nie ma powodu
    zależeć od modeli prac. Zgodność wartości jest wygodą (ten sam klient clamd, te same słowa
    w logu), a nie więzem.
    """

    PENDING = "PENDING", "oczekuje na skan"
    CLEAN = "CLEAN", "czysty"
    INFECTED = "INFECTED", "zainfekowany"
    ERROR = "ERROR", "błąd skanu"


class StudentStatusCertificate(models.Model):
    """Jedna wersja zaświadczenia jednego uczestnika w jednej edycji.

    Konkurs **nie** jest osobną kolumną: dochodzimy do niego przez edycję (``edition__competition``),
    tak jak robią to pozostałe modele domeny zawodów bez własnej denormalizacji. Tabela jest mała
    (jeden, czasem dwa wiersze na uczestnika w roku), a zapytania idą po edycji, więc złączenie
    kosztuje tyle co nic.
    """

    #: ``CASCADE`` z profilu: profil znika wyłącznie przy skasowaniu konta bez śladu w zawodach
    #: (``accounts.profile._erase_account``), a wtedy skan nie ma czyj być. Plik w storage sprząta
    #: ``services.erase_for_user`` **przed** kaskadą – kaskada bazy nie wie o buckecie.
    participant = models.ForeignKey(
        "accounts.Participant",
        on_delete=models.CASCADE,
        related_name="student_status_certificates",
        verbose_name="uczestnik",
    )
    edition = models.ForeignKey(
        "competitions.Edition",
        on_delete=models.CASCADE,
        related_name="student_status_certificates",
        verbose_name="edycja",
    )
    version = models.PositiveSmallIntegerField("wersja")
    is_current = models.BooleanField("bieżąca", default=True)
    status = models.CharField(
        "stan", max_length=16, choices=CertificateStatus.choices, default=CertificateStatus.PENDING
    )
    #: Identyfikator do klucza obiektu w storage. Klucz powstaje wyłącznie z identyfikatorów
    #: technicznych i skrótu treści – nazwa pliku od uczestnika nie bierze w nim udziału
    #: (ta sama reguła, co ``submissions.storage.build_object_key``).
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    #: Pusty napis znaczy „pliku już nie ma w storage” (zastąpiony, zainfekowany, po retencji,
    #: po anonimizacji) – wiersz zostaje jako historia decyzji.
    object_key = models.CharField("klucz obiektu", max_length=300, blank=True)
    sha256 = models.CharField("sha256", max_length=64)
    #: Typ wyliczony przez serwer z treści pliku (``validators.validate_scan``), nigdy z nagłówka
    #: ``Content-Type`` przesłanego przez przeglądarkę.
    mime = models.CharField("typ", max_length=40)
    size_bytes = models.PositiveIntegerField("rozmiar (B)")
    scan_status = models.CharField(
        "skan antywirusowy", max_length=16, choices=ScanStatus.choices, default=ScanStatus.PENDING
    )
    scanned_at = models.DateTimeField("przeskanowany", null=True, blank=True)
    uploaded_at = models.DateTimeField("przesłane", default=timezone.now)
    decided_at = models.DateTimeField("rozpatrzone", null=True, blank=True)
    #: ``SET_NULL``: decyzja zostaje w historii także po odejściu koordynatora z zespołu, a kto ją
    #: podjął, mówi dodatkowo wpis audytowy (``student_status.accepted`` / ``.rejected``).
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="rozpatrzył(a)",
    )
    #: Powód odrzucenia – napisany **do uczestnika**, bo to on go czyta w panelu i w liście.
    rejection_reason = models.TextField("powód odrzucenia", blank=True)
    file_removed_at = models.DateTimeField("plik usunięty", null=True, blank=True)

    objects = competition_scoped_manager("edition__competition")

    class Meta:
        verbose_name = "zaświadczenie o statusie ucznia"
        verbose_name_plural = "zaświadczenia o statusie ucznia"
        ordering = ("participant_id", "edition_id", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=["participant", "edition", "version"],
                name="student_status_version_per_edition",
            ),
            # Jeden stan na parę (uczestnik, edycja). Dwa wiersze bieżące znaczyłyby dwa różne
            # odpowiedzi na pytanie „czy ten uczeń ma potwierdzony status” – zależnie od kolejności
            # wierszy w zapytaniu, czyli od przypadku.
            models.UniqueConstraint(
                fields=["participant", "edition"],
                condition=Q(is_current=True),
                name="student_status_single_current",
            ),
        ]
        indexes = [
            # Filtr paczek ZIP: „zaakceptowani w tej edycji” – jedno zapytanie na pobranie.
            models.Index(fields=["edition", "is_current", "status"], name="student_status_filter_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.participant_id} / {self.edition_id} v{self.version} ({self.status})"

    @property
    def has_file(self) -> bool:
        """Czy plik jest jeszcze w storage (nie został usunięty po zastąpieniu ani po retencji)."""
        return bool(self.object_key)

    @property
    def is_clean(self) -> bool:
        """Czy plik wolno pokazać komukolwiek poza jego autorem – tylko po czystym skanie."""
        return self.has_file and self.scan_status == ScanStatus.CLEAN

    @property
    def extension(self) -> str:
        """Rozszerzenie z typu wyliczonego **przez serwer** – nigdy z nazwy pliku od uczestnika."""
        from .validators import EXTENSION_BY_MIME

        return EXTENSION_BY_MIME.get(self.mime, "dat")
