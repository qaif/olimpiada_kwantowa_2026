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
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import SchoolSupervisor
from apps.competitions.models import Edition, Stage, StageEntry
from apps.competitions.scoping import competition_scoped_manager
from apps.competitions.storage import private_media_storage

from .certificate_layout import default_certificate_layout


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

    #: Przez etap. Tabela wyników jest publiczna, ale publiczna **w swoim konkursie**: adres
    #: ``/results/<id>/`` nie wymaga logowania, więc bez zakresu byłby najtańszą drogą do cudzych
    #: wyników – wystarczyłoby przejechać identyfikatory etapów.
    objects = competition_scoped_manager("stage__edition__competition")

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
    """Rodzaj dokumentu. Pięć, bo tyle jest różnych faktów do poświadczenia.

    Podział laureat / finalista / uczestnik jest podziałem **regulaminowym**, a nie wynikiem
    obliczenia: o tym, kto jest laureatem, rozstrzyga komitet i to on wybiera rodzaj przy
    wystawianiu. System nie zgaduje tego z punktów, bo próg laureata bywa ustalany na posiedzeniu
    i nie musi pokrywać się z progiem kwalifikacji.

    ``WARSZTATY`` poświadcza obecność na warsztatach online, czyli fakt spoza toru zawodów:
    warsztat nie jest etapem, nie ma wpisu, punktów ani progu, a mimo to nauczyciel bywa proszony
    o zaświadczenie dla ucznia, a uczeń dokłada je do wniosku stypendialnego. Rodzaj jest osobny,
    a nie „uczestnik” z dopiskiem, bo zdanie na papierze jest tu zupełnie inne – wylicza tematy
    i terminy zajęć, na których uczeń był.
    """

    LAUREAT = "LAUREAT", "laureat"
    FINALISTA = "FINALISTA", "finalista"
    UCZESTNIK = "UCZESTNIK", "uczestnik"
    OPIEKUN = "OPIEKUN", "opiekun"
    WARSZTATY = "WARSZTATY", "uczestnik warsztatów"


#: Prefiks numeru dokumentu: ``OK/<rok>/<kolejny>``. „OK” od Olimpiady Kwantowej – numer trafia
#: na papier i bywa przepisywany do dziennika szkolnego, więc musi być krótki i jednoznaczny.
#:
#: Od wydania D prefiks jest własnością konkursu (``tenancy.Competition.certificate_prefix``,
#: § 3.3) i to on rozstrzyga o numerze. Ta stała zostaje jako **odwrót** dla przebiegu, któremu
#: konkursu nie da się wskazać (podgląd szablonu w instalacji bez konkursów), i jako wartość
#: domyślna nowego konkursu. Konkurs #1 dostał w migracji dokładnie ``"OK"``, więc żaden
#: dotychczasowy numer się nie zmienia.
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
    # --- pieczęć elektroniczna -------------------------------------------------------------
    # Trzy pola opisujące **ostatni udany** podpis pliku, a nie sam fakt konfiguracji podpisu.
    # PDF powstaje przy każdym pobraniu, więc to, czy dokument wyszedł z serwera opieczętowany,
    # jest własnością chwili składu: certyfikat mógł stracić ważność, plik .p12 mógł zniknąć
    # z wolumenu, a dokument i tak ma wyjść – tyle że bez pieczęci. Bez zapisania tego faktu
    # strona weryfikacji musiałaby zgadywać, co odbiorca trzyma w ręku.
    signed = models.BooleanField("podpisany elektronicznie", default=False)
    signed_at = models.DateTimeField("data podpisu", null=True, blank=True)
    # Nazwa z podmiotu certyfikatu (CN albo nazwa organizacji). Trzymamy ją przy dokumencie,
    # bo strona weryfikacji ma powiedzieć **kto** pieczętował, a nie „podpisano cyfrowo”:
    # czytelnik sprawdza dokument właśnie po to, żeby wiedzieć, czyja to pieczęć.
    signer_name = models.CharField("podpisujący", max_length=200, blank=True)

    #: Przez edycję – klucz obcy do niej dokument ma od początku (§ 3.4).
    #:
    #: ``number`` i ``code`` zostają unikalne **globalnie** i to nie jest przeoczenie: numer niesie
    #: prefiks konkursu, a strona weryfikacji jest publiczna i ma działać bez wskazania konkursu –
    #: czytelnik trzymający dyplom w ręku nie wie, pod którą domeną go sprawdzić.
    objects = competition_scoped_manager("edition__competition")

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


#: Rozszerzenia tła dokumentu. PDF jest tu obok obrazów świadomie: drukarnie oddają projekt
#: dyplomu jako PDF w CMYK-u i przerobienie go na PNG kosztuje jakość, której na papierze
#: nie da się odzyskać. Skład wkleja wtedy tekst **na pierwszą stronę** tego pliku.
CERTIFICATE_BACKGROUND_EXTENSIONS = ("png", "jpg", "jpeg", "pdf")
#: Logo i podpisy są rysowane na stronie, więc muszą być obrazem – PDF-u reportlab nie umie
#: wstawić jako grafiki bez dodatkowej biblioteki, a nie ma po co jej dokładać dla winiety.
CERTIFICATE_IMAGE_EXTENSIONS = ("png", "jpg", "jpeg")


class CertificateTemplate(models.Model):
    """Szablon graficzny dokumentu: tło, logo, podpisy i położenie napisów.

    Po co, skoro dyplom już się składa. Bo dotąd jego wygląd był **kodem**: zmiana winiety przed
    galą znaczyła poprawkę w ``apps.results.certificates`` i wdrożenie, a organizator, który
    dostał z drukarni gotowy projekt karty, nie miał gdzie go wgrać. Szablon przenosi tę decyzję
    tam, gdzie zapada – do panelu – i zostawia w kodzie wyłącznie skład.

    Dopasowanie do dokumentu idzie od najbardziej szczegółowego do najogólniejszego:
    (rodzaj, edycja) → (rodzaj, wszystkie edycje) → (wszystkie rodzaje, edycja) → (wszystkie,
    wszystkie) → wbudowany układ. Pusty ``kind`` znaczy „każdy rodzaj”, puste ``edition`` –
    „każda edycja”; jubileuszowa winieta jednej edycji nie wymaga więc kopiowania szablonu pięć
    razy, a dyplom laureata może mieć własną kartę przy wspólnym tle reszty dokumentów.

    ``kind`` jest **pustym napisem**, a nie ``NULL``-em: dwie wartości znaczące „brak” w jednej
    kolumnie tekstowej to klasyczne źródło zapytań, które gubią wiersze (``= ''`` nie łapie
    ``NULL``), a tutaj kolumna jest częścią wyszukiwania szablonu przy każdym pobraniu dokumentu.

    Pliki idą do **prywatnego** storage (ten sam alias, co treści zadań). Tło dyplomu nie jest
    tajemnicą, ale publiczny bucket to adres, który da się zgadnąć i podlinkować – a wtedy czysta
    karta dyplomu olimpiady krąży po sieci jako gotowy plik do podrobienia. Dokument z panelu
    wychodzi zawsze jako złożony PDF, więc nikt nie potrzebuje URL-a do samego tła.
    """

    # Właściciel szablonu. ``PROTECT`` i bez ``null``: „szablon dla wszystkich edycji”
    # (``edition IS NULL``) jest od wydania D szablonem wszystkich edycji **jednego konkursu**,
    # a nie wspólną półką instalacji. Bez tej kolumny winieta organizatora A byłaby tłem dyplomu
    # organizatora B – i to bez żadnego kliknięcia, samym trafieniem w odwrót „na wszystko”.
    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.PROTECT,
        related_name="certificate_templates",
        verbose_name="konkurs",
    )
    name = models.CharField("nazwa", max_length=120)
    kind = models.CharField(
        "rodzaj dokumentu",
        max_length=16,
        choices=CertificateKind.choices,
        blank=True,
        help_text="Puste = szablon dla wszystkich rodzajów dokumentów.",
    )
    edition = models.ForeignKey(
        Edition,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="certificate_templates",
        verbose_name="edycja",
        help_text="Puste = szablon dla wszystkich edycji.",
    )
    background = models.FileField(
        "tło (PNG, JPG albo PDF)",
        upload_to="certificates/backgrounds/",
        blank=True,
        storage=private_media_storage,
        validators=[FileExtensionValidator(list(CERTIFICATE_BACKGROUND_EXTENSIONS))],
    )
    logo = models.ImageField(
        "logo",
        upload_to="certificates/logos/",
        blank=True,
        storage=private_media_storage,
        validators=[FileExtensionValidator(list(CERTIFICATE_IMAGE_EXTENSIONS))],
    )
    # Trzy podpisy, każdy jako trójka pól, zamiast osobnej tabeli. Liczba jest ograniczona
    # z premedytacją: na dokumencie mieszczą się trzy bloki podpisu obok siebie i tyle podpisów
    # ma dyplom olimpiady (komitet, patron, partner). Tabela podrzędna dołożyłaby formularz
    # zagnieżdżony i kolejność do utrzymania, a nie dołożyłaby ani jednej możliwości.
    signature_1_image = models.ImageField(
        "podpis 1 – grafika",
        upload_to="certificates/signatures/",
        blank=True,
        storage=private_media_storage,
        validators=[FileExtensionValidator(list(CERTIFICATE_IMAGE_EXTENSIONS))],
    )
    signature_1_name = models.CharField("podpis 1 – imię i nazwisko", max_length=120, blank=True)
    signature_1_title = models.CharField("podpis 1 – funkcja", max_length=160, blank=True)
    signature_2_image = models.ImageField(
        "podpis 2 – grafika",
        upload_to="certificates/signatures/",
        blank=True,
        storage=private_media_storage,
        validators=[FileExtensionValidator(list(CERTIFICATE_IMAGE_EXTENSIONS))],
    )
    signature_2_name = models.CharField("podpis 2 – imię i nazwisko", max_length=120, blank=True)
    signature_2_title = models.CharField("podpis 2 – funkcja", max_length=160, blank=True)
    signature_3_image = models.ImageField(
        "podpis 3 – grafika",
        upload_to="certificates/signatures/",
        blank=True,
        storage=private_media_storage,
        validators=[FileExtensionValidator(list(CERTIFICATE_IMAGE_EXTENSIONS))],
    )
    signature_3_name = models.CharField("podpis 3 – imię i nazwisko", max_length=120, blank=True)
    signature_3_title = models.CharField("podpis 3 – funkcja", max_length=160, blank=True)
    layout = models.JSONField("układ", default=default_certificate_layout, blank=True)
    # Wyłączony szablon zostaje w bazie razem z plikami: „ten dyplom wygląda nie tak, wróćmy do
    # poprzedniego” musi być jednym kliknięciem, a nie ponownym wgrywaniem tła z czyjegoś dysku.
    is_active = models.BooleanField("aktywny", default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="certificate_templates",
        verbose_name="utworzył",
    )
    created_at = models.DateTimeField("utworzony", default=timezone.now)

    #: Własna kolumna, a **nie** droga przez edycję – i to jest cała zmiana wydania D w tym
    #: modelu. Dopasowanie szablonu (``apps.results.certificates.resolve_template``) idzie od
    #: szczegółu do ogółu i kończy na wierszu bez edycji; dopóki zakres szedł przez ``edition``,
    #: taki wiersz nie należał do nikogo, więc „szablon na wszystko” był wspólną półką całej
    #: instalacji. Teraz jest półką jednego konkursu.
    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = "szablon dokumentu"
        verbose_name_plural = "szablony dokumentów"
        # Od najnowszego: przy dwóch szablonach pasujących tak samo dobrze wygrywa ten, który
        # wgrano później. Tak działa oczekiwanie („wgrałem nowy, to on obowiązuje”), a stary
        # zostaje pod ręką jako wpis do włączenia z powrotem.
        ordering = ("-created_at", "-id")

    def __str__(self) -> str:
        return self.name

    @property
    def kind_label(self) -> str:
        """Etykieta rodzaju dla panelu. Pusty rodzaj to zdanie, a nie pusta komórka w tabeli."""
        return self.get_kind_display() if self.kind else "wszystkie rodzaje"

    @property
    def edition_label(self) -> str:
        return self.edition.year_label if self.edition_id else "wszystkie edycje"

    def signatures(self) -> list[dict]:
        """Wypełnione bloki podpisu, po kolei. Blok pusty w całości nie trafia na dokument."""
        blocks = []
        for index in (1, 2, 3):
            image = getattr(self, f"signature_{index}_image")
            name = getattr(self, f"signature_{index}_name")
            title = getattr(self, f"signature_{index}_title")
            if image or name or title:
                blocks.append({"image": image or None, "name": name, "title": title})
        return blocks
