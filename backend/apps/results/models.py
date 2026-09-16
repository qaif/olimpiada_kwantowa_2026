"""Model publikacji wyników etapu (PROJEKT.md 2.2, 2.4).

Zasady:
- ``snapshot`` jest **zamrożoną** tabelą wyników. Publiczny endpoint czyta wyłącznie ten JSON,
  nigdy bazy: po publikacji zmiana ``FinalGrade`` (np. późna decyzja komisji) nie może po cichu
  przepisać ogłoszonych wyników. Nowa tabela wymaga nowej publikacji, a ta zostawia ślad w audycie,
- ``snapshot`` jest zanonimizowany zgodnie z ``anonymization`` i **nigdy** nie zawiera e-maila,
  roku urodzenia ani identyfikatora użytkownika. Imię i nazwisko wolno w nim umieścić wyłącznie
  przy ``FULL`` i wyłącznie dla uczestnika, który wyraził zgodę (``publish_full_name``),
- czas zawsze przez ``django.utils.timezone.now()``.
"""

import secrets

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import SchoolSupervisor
from apps.competitions.models import Edition, Stage, StageEntry


def default_snapshot() -> list:
    """``default`` JSONField musi być wywoływalny i zwracać nowy obiekt."""
    return []


def default_entry_totals() -> dict:
    """``default`` JSONField musi być wywoływalny i zwracać nowy obiekt."""
    return {}


class Anonymization(models.TextChoices):
    CODE = "CODE", "kod uczestnika"
    INITIALS_SCHOOL = "INITIALS_SCHOOL", "inicjały i szkoła"
    FULL = "FULL", "pełne dane – finał, za zgodą"


class ResultsPublication(models.Model):
    """Ogłoszona tabela wyników jednego etapu. Jedna na etap – ponowna publikacja ją nadpisuje."""

    # PROTECT, nie CASCADE: ogłoszona tabela wyników jest dokumentem, a nie szczegółem etapu.
    # Skasowanie etapu ma się wywrócić na ProtectedError i wymusić świadomą decyzję (najpierw
    # zdejmij publikację), zamiast po cichu zabrać jedyny ślad tego, co ogłoszono.
    stage = models.OneToOneField(Stage, on_delete=models.PROTECT, related_name="results_publication")
    published_at = models.DateTimeField("opublikowane", default=timezone.now)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="results_publications",
        verbose_name="opublikował",
    )
    anonymization = models.CharField(
        "anonimizacja", max_length=24, choices=Anonymization.choices, default=Anonymization.CODE
    )
    snapshot = models.JSONField("zamrożona tabela", default=default_snapshot, blank=True)
    # Mapa ``{str(StageEntry.pk): suma}`` z chwili publikacji. Nie jest częścią publicznej tabeli
    # (serializery wypisują pola jawnie) i nie zawiera danych osobowych – identyfikator wpisu plus
    # liczba. Służy jednemu: uczestnik w ``me/results/`` musi wiedzieć, czy jego bieżąca suma
    # rozjechała się z ogłoszoną. Ze snapshotu tego nie da się odczytać, bo wiersze są anonimowe.
    entry_totals = models.JSONField(
        "sumy wpisów w chwili publikacji", default=default_entry_totals, blank=True
    )

    class Meta:
        verbose_name = "publikacja wyników"
        verbose_name_plural = "publikacje wyników"
        ordering = ("-published_at", "-id")

    def __str__(self) -> str:
        return f"wyniki etapu {self.stage_id} ({self.anonymization})"

    @property
    def rows(self) -> list:
        """Wiersze tabeli. ``snapshot`` bywa pusty (etap bez wpisów) – kształt musi być stały."""
        return self.snapshot if isinstance(self.snapshot, list) else []


class CertificateKind(models.TextChoices):
    """Rodzaj dokumentu. Cztery, bo tyle jest różnych faktów do poświadczenia.

    Podział laureat / finalista / uczestnik jest podziałem **regulaminowym**, a nie wynikiem
    obliczenia: o tym, kto jest laureatem, rozstrzyga komitet i to on wybiera rodzaj przy
    wystawianiu. System nie zgaduje tego z punktów, bo próg laureata bywa ustalany na posiedzeniu
    i nie musi pokrywać się z progiem kwalifikacji.
    """

    LAUREAT = "LAUREAT", "laureat"
    FINALISTA = "FINALISTA", "finalista"
    UCZESTNIK = "UCZESTNIK", "uczestnik"
    OPIEKUN = "OPIEKUN", "opiekun"


#: Prefiks numeru dokumentu: ``OK/<rok>/<kolejny>``. „OK” od Olimpiady Kwantowej – numer trafia
#: na papier i bywa przepisywany do dziennika szkolnego, więc musi być krótki i jednoznaczny.
CERTIFICATE_NUMBER_PREFIX = "OK"

#: Alfabet kodu weryfikacyjnego – ten sam, co w kodach uczestników: bez znaków mylących przy
#: przepisywaniu z papieru (0/O, 1/I/L).
VERIFICATION_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
#: Dwanaście znaków z 31-znakowego alfabetu to ok. 59 bitów – kodu nie da się zgadnąć, a strona
#: weryfikacji i tak nie wydaje żadnych danych osobowych bez zgody na publikację nazwiska.
VERIFICATION_CODE_LENGTH = 12


def generate_verification_code() -> str:
    """Losowy kod weryfikacyjny dokumentu. Losowy, a nie wyliczony z numeru – numer jest jawny."""
    return "".join(secrets.choice(VERIFICATION_CODE_ALPHABET) for _ in range(VERIFICATION_CODE_LENGTH))


class Certificate(models.Model):
    """Wystawiony dyplom albo zaświadczenie – **rejestr**, a nie plik.

    W bazie nie ma PDF-a i nie będzie: dokument składa się z faktów, które i tak tu są (edycja,
    rodzaj, numer, data), więc trzymanie kopii binarnej znaczyłoby jedynie tyle, że poprawka
    w szablonie nie dotyczy dokumentów już wystawionych. PDF powstaje przy każdym pobraniu
    (``apps.results.certificates``), a tożsamość dokumentu niesie **numer i kod**.

    Dokument dotyczy albo wpisu uczestnika do etapu, albo opiekuna szkolnego – dokładnie jednego
    z nich (constraint niżej). Dwa nullowalne klucze zamiast relacji ogólnej (``GenericForeignKey``)
    z premedytacją: odbiorców są dwa rodzaje i nigdy nie będzie ich więcej niż kilka, a klucz
    obcy daje integralność, której relacja ogólna nie daje.

    ``code`` jest jedynym, co trzeba znać, żeby sprawdzić dokument na stronie publicznej – i
    dlatego jest losowy, a nie wyprowadzony z numeru. Numer stoi na papierze obok kodu i bywa
    cytowany w pismach; gdyby kod dał się z niego wyliczyć, weryfikacja przestałaby cokolwiek
    poświadczać.
    """

    edition = models.ForeignKey(Edition, on_delete=models.PROTECT, related_name="certificates")
    # PROTECT po obu stronach odbiorcy: skasowanie wpisu albo profilu opiekuna unieważniłoby
    # dokument, który ktoś trzyma w ręku. Usunięcie konta uczestnika przechodzi przez anonimizację
    # (``apps.accounts.profile``), która wpisu do etapu nie kasuje.
    entry = models.ForeignKey(
        StageEntry,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="certificates",
        verbose_name="wpis uczestnika",
    )
    supervisor = models.ForeignKey(
        SchoolSupervisor,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="certificates",
        verbose_name="opiekun",
    )
    kind = models.CharField("rodzaj", max_length=16, choices=CertificateKind.choices)
    number = models.CharField("numer", max_length=32, unique=True)
    code = models.CharField(
        "kod weryfikacyjny", max_length=32, unique=True, default=generate_verification_code
    )
    issued_at = models.DateTimeField("wystawiony", default=timezone.now)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="certificates_issued",
        verbose_name="wystawił",
    )

    class Meta:
        verbose_name = "dyplom / zaświadczenie"
        verbose_name_plural = "dyplomy i zaświadczenia"
        ordering = ("-issued_at", "-id")
        constraints = [
            # Dokładnie jeden odbiorca. Bez tego dałoby się zapisać dokument bez adresata
            # (niczego nie poświadcza) albo z dwoma naraz (nie wiadomo, czyj jest).
            models.CheckConstraint(
                condition=Q(entry__isnull=False, supervisor__isnull=True)
                | Q(entry__isnull=True, supervisor__isnull=False),
                name="results_certificate_single_recipient",
            ),
            # Jeden dokument danego rodzaju na wpis. Powtórne „Wystaw” ma oddać ten sam numer,
            # a nie wypisać drugi dyplom dla tej samej osoby w tym samym etapie.
            models.UniqueConstraint(
                fields=["entry", "kind"],
                condition=Q(entry__isnull=False),
                name="results_certificate_unique_entry_kind",
            ),
            models.UniqueConstraint(
                fields=["supervisor", "edition"],
                condition=Q(supervisor__isnull=False),
                name="results_certificate_unique_supervisor_edition",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.number} ({self.get_kind_display()})"

    @property
    def is_for_supervisor(self) -> bool:
        return self.supervisor_id is not None
