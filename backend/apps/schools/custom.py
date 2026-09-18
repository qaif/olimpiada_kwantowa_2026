"""Słownik placówek **jednego organizatora**: model, import CSV i wyszukiwarka.

Osobna tabela, a nie wiersze w ``School`` – i to jest cała treść decyzji D2 etapu 1 przepisana na
etap 2 (§ 1.3.3). ``School`` jest rejestrem publicznym wspólnym dla instalacji: ten sam wykaz SIO
dla każdego konkursu, wgrywany komendą, z upsertem po numerze RSPO. Lista organizatora jest czymś
innym – uczelnie partnerskie, ośrodki, kluby, szkoły zagraniczne z jego programu – i należy do
niego. Wspólna tabela z kolumną właściciela znaczyłaby, że **każde** zapytanie wyszukiwarki musi
pamiętać o filtrze, a zapomniany filtr w wyszukiwarce publicznej jest wyciekiem listy kontrahentów
organizatora.

Osobny moduł, a nie kolejne trzysta linii w ``models.py``: model, parser pliku i wyszukiwarka są
jedną sprawą i czyta się je razem, a ``apps/schools/models.py`` opisuje wykaz publiczny i ma
zostać tym, czym jest. Model rejestruje się w ``models.py`` jedną linią importu – Django ładuje
moduł razem z aplikacją, więc migracje i rejestr modeli widzą go tak samo jak ``School``.

**Czego ten moduł nie robi:** nie dopisuje ani jednego wiersza do ``School`` i nie kasuje niczego.
Wiersz nieobecny w kolejnym pliku jest **wygaszany** (``is_active=False``), tak samo jak w
``seed_schools`` i z tego samego powodu: może być wskazany przez profil uczestnika sprzed roku,
a historia zgłoszeń nie ma prawa zniknąć razem z aktualizacją słownika.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from django.db import models, transaction
from django.utils import timezone
from rest_framework import status

from apps.competitions.scoping import competition_scoped_manager
from apps.core.api import DomainError
from apps.core.models import audit
from apps.core.text import fold

from .models import InstitutionType
from .normalise import DISTRICT_SEPARATOR, derived_fields

#: Ile wierszy przyjmujemy z jednego pliku (§ 1.3.3). Dziesięć razy więcej niż przy imporcie
#: uczniów (``apps.accounts.bulk_registration.MAX_ROWS``), bo to jest inny zbiór: tam plik opisuje
#: **jedną klasę albo szkołę**, a tu cały wykaz placówek konkursu – lista uczelni w Polsce ma
#: kilkaset pozycji, a wykaz ośrodków programu międzynarodowego bywa dłuższy.
MAX_ROWS = 5000

#: Górna granica rozmiaru pliku – ta sama, co przy imporcie uczniów. Pięć tysięcy wierszy CSV waży
#: kilkaset kilobajtów, więc dwa megabajty zostawiają zapas i zatrzymują wgranie czegoś zupełnie
#: innego, zanim parser zacznie to czytać.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024

#: Najkrótsza sensowna nazwa placówki – tyle samo, co w rejestracji
#: (``apps.accounts.services.MIN_SCHOOL_NAME_LENGTH``). Dwuliterowy wiersz w słowniku byłby
#: pozycją, której uczestnik nie rozpozna, a wyszukiwarka i tak nie zawęzi.
MIN_NAME_LENGTH = 3

#: Sufit liczby podpowiedzi i próg długości zapytania – **przepisane** z ``apps.schools.api``
#: (``MAX_RESULTS``, ``MIN_QUERY_LENGTH``). Wartości są tam stałymi zadania T25 i ten moduł ich
#: nie zmienia; powtórzenie jest tu po to, żeby wyszukiwarka słownika własnego nie mogła oddać
#: dłuższej listy niż wykaz publiczny, gdyby ktoś zawołał ją z pominięciem T25.
MAX_RESULTS = 20
MIN_QUERY_LENGTH = 2

#: Zdarzenie audytowe importu. W ``diff`` idą **wyłącznie liczniki** – nazwy placówek są listą
#: kontrahentów organizatora i nie mają czego robić w tabeli, którą czyta operator platformy.
AUDIT_IMPORTED = "custom_directory.imported"


class CustomInstitution(models.Model):
    """Placówka z wykazu wgranego przez organizatora – **osobna tabela**, nie wiersz w ``School``.

    Osobna, bo ``School`` jest rejestrem publicznym wspólnym dla instalacji (etap 1 § 8, decyzja
    D2), a to jest lista jednego organizatora. Uzasadnienie rozdziału stoi w docstringu modułu.

    **k-anonimowość (decyzja D13).** Organizator może tu wpisać także polską szkołę, której nie ma
    w wykazie SIO – wykaz nie zna szkół założonych po jego dacie, a jego nieaktualność nie może
    zamykać drogi do olimpiady. Cena jest jedna i trzeba ją znać: wiersz stąd liczy się do
    grupowania w wynikach **dokładnie tak samo** jak wiersz z ``School``, bo rejestracja kopiuje
    ``name`` do ``Participant.school`` (``apps.accounts.services._resolve_institution``), a
    ``apps.results.services._display_name`` grupuje po ``_school_key(school)``, czyli po
    znormalizowanej **nazwie**, i dopiero grupa co najmniej ``MIN_SCHOOL_GROUP`` uczestników
    dostaje podpis „J.K., XIV LO”. Wynika z tego, że dwie pisownie tej samej szkoły – jedna
    z wykazu, druga z pliku organizatora – są dla progu **dwiema** szkołami i żadna z nich może
    nie zebrać grupy. To jest ta sama zasada, co przy wolnym tekście, i tak samo ma być zapisana
    w podręczniku organizatora. Ten moduł nie zmienia ani jednej linii w ``apps.results``.

    Kolumny wyliczane (``search_text``, ``city_search``) liczy **ta sama** funkcja, co dla
    ``School`` (``apps.schools.normalise.derived_fields``) – dwie reguły normalizacji znaczyłyby
    dwa zachowania wyszukiwarki na jednym ekranie formularza.
    """

    #: ``CASCADE``, tak jak przy ``Region`` i ``ConsentDefinition``, a nie ``PROTECT`` jak przy
    #: ``Participant``: to jest słownik **samego konkursu**, a nie cudze dane. Uczestnicy nie
    #: znikną po cichu razem z nim – ``Participant.custom_institution_ref`` jest ``PROTECT``, więc
    #: konkurs z profilami zatrzyma się tam, gdzie stoją dane ludzi.
    competition = models.ForeignKey(
        "tenancy.Competition",
        verbose_name="konkurs",
        on_delete=models.CASCADE,
        related_name="custom_institutions",
    )
    #: Identyfikator nadany przez organizatora – klucz upsertu przy kolejnym wgraniu pliku, tak
    #: jak ``rspo`` w wykazie publicznym. Pusty jest dopuszczalny (nie każdy prowadzi swój wykaz
    #: w arkuszu z identyfikatorami) i wtedy wiersz rozpoznaje się po parze nazwa + miejscowość.
    external_id = models.CharField("identyfikator organizatora", max_length=64, blank=True)
    name = models.CharField("nazwa", max_length=255)
    institution_type = models.CharField(
        "rodzaj placówki",
        max_length=16,
        choices=InstitutionType.choices,
        default=InstitutionType.OTHER,
        db_index=True,
    )
    #: ISO 3166-1 alpha-2. **Pusty znaczy Polska**, a nie „nie podano” – ta sama reguła i to samo
    #: jedno miejsce odczytu (``country or "PL"``), co przy ``Participant.country`` (§ 1.3.2).
    country = models.CharField("kraj", max_length=2, blank=True)
    #: Region z podziału terytorialnego konkursu (§ 1.4.2) – **kod**, a nie klucz obcy, i tak
    #: stanowi § 1.3.3. Trzy powody, wszystkie praktyczne:
    #:
    #: - plik organizatora bywa opisany kodem regionu, którego w podziale konkursu **jeszcze** nie
    #:   ma; klucz obcy kazałby wtedy wiersz odrzucić albo zgubić tę informację,
    #: - import grupowy (T26) i eksporty czytają kod bez złączenia – dokładnie tak, jak czytają
    #:   ``Participant.district`` obok ``Participant.region``,
    #: - klucz obcy do ``accounts.Region`` znaczyłby zależność migracji ``schools`` od ``accounts``
    #:   i kaskadę ``SET_NULL`` przy kasowaniu regionów. Przewijanie migracji kont (test
    #:   ``accounts.test_consent_definitions_migration``) kasuje regiony w stanie, w którym tabeli
    #:   słownika jeszcze nie ma – i właśnie na tym taka kaskada się wywraca.
    #:
    #: Import sprowadza wartość do kodu regionu konkursu, gdy taki region istnieje (po kodzie albo
    #: po nazwie), a w przeciwnym razie zostawia napis z pliku.
    region_code = models.SlugField("kod regionu", max_length=40, blank=True)
    city = models.CharField("miejscowość", max_length=120, blank=True)
    postal_code = models.CharField("kod pocztowy", max_length=12, blank=True)
    address = models.CharField("adres", max_length=255, blank=True)
    search_text = models.CharField(
        "tekst wyszukiwania", max_length=400, db_index=True, editable=False, default=""
    )
    city_search = models.CharField(
        "miejscowość (postać porównawcza)", max_length=120, db_index=True, editable=False, default=""
    )
    #: Wygaszenie zamiast skasowania – patrz docstring modułu.
    is_active = models.BooleanField("aktywna", default=True)
    #: Nazwa pliku i data wgrania, czyli **skąd** ten wiersz się wziął. Napis dla człowieka, a nie
    #: klucz obcy do tabeli importów: historia wgrań jest w audycie (``custom_directory.imported``),
    #: a przy wierszu wystarczy jedno zdanie, które koordynator rozpozna na liście.
    source_label = models.CharField("źródło", max_length=120, blank=True)
    created_at = models.DateTimeField("dodana", default=timezone.now)

    #: Własna kolumna konkursu – domyślna ścieżka queryseta.
    objects = competition_scoped_manager()

    class Meta:
        verbose_name = "placówka organizatora"
        verbose_name_plural = "placówki organizatora"
        ordering = ("name", "id")
        constraints = [
            # Identyfikator jest unikalny **w konkursie**, a nie na platformie: dwaj organizatorzy
            # mogą numerować swoje wykazy od jedynki i nie jest to ten sam wiersz. Warunek
            # ``~Q(external_id="")`` jest tu konieczny, bo pusty identyfikator znaczy „nie mam” –
            # bez niego drugi wiersz bez identyfikatora nie dałby się wgrać.
            models.UniqueConstraint(
                fields=["competition", "external_id"],
                condition=~models.Q(external_id=""),
                name="schools_custominstitution_unique_external_id",
            )
        ]
        indexes = [
            models.Index(fields=("competition", "search_text"), name="schools_custom_search_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name}, {self.city}" if self.city else self.name

    def save(self, *args, **kwargs):
        """Kolumny wyliczane są liczone, nie wpisywane – tak samo jak w ``School.save()``.

        ``derived_fields`` oddaje trzy kolumny; ``city_parent`` jest własnością wykazu SIO
        (dzielnice pięciu największych miast) i tej tabeli nie dotyczy, więc bierzemy z niej
        dwie. Reguła zostaje **jedna** – o to chodziło w § 1.3.3.
        """
        computed = derived_fields(self.name, self.city, "", self.postal_code)
        self.search_text = computed["search_text"]
        self.city_search = computed["city_search"]
        super().save(*args, **kwargs)


# --- kolumny pliku --------------------------------------------------------------------------
#
# Format i reguły przepisane z importu uczniów (``apps.accounts.bulk_registration``), bo to jest
# ten sam problem i ten sam człowiek go wykonuje: arkusz prowadzony ręcznie, nagłówki pisane raz
# z ogonkami, raz bez, raz wielkimi literami.


@dataclass(frozen=True)
class Column:
    """Jedna kolumna pliku: klucz w kodzie, nagłówek dla człowieka i warianty jego zapisu."""

    key: str
    label: str
    aliases: tuple[str, ...]
    required: bool = False


def header_key(text: str) -> str:
    """Postać porównawcza nagłówka: małe litery bez diakrytyków, bez spacji i interpunkcji.

    Składanie znaków robi ``apps.core.text.fold`` – ta sama funkcja, co przy ``search_text``
    i przy liście województw. Własna kopia reguły znaczyłaby, że „Miejscowość” i „miejscowosc”
    trafiają w ten sam klucz w jednym imporcie, a w drugim nie.
    """
    return "".join(char for char in fold(text or "").strip() if char.isalnum())


COLUMNS: tuple[Column, ...] = (
    Column("name", "nazwa", ("nazwa", "nazwaplacowki", "nazwainstytucji", "nazwaszkoly"), required=True),
    Column("external_id", "identyfikator", ("identyfikator", "identyfikatorwlasny", "idplacowki", "id")),
    Column("institution_type", "rodzaj", ("rodzaj", "rodzajplacowki", "typ", "typplacowki")),
    Column("country", "kraj", ("kraj", "panstwo")),
    Column("region", "region", ("region", "kodregionu", "wojewodztwo")),
    Column("city", "miejscowosc", ("miejscowosc", "miasto")),
    Column("postal_code", "kod_pocztowy", ("kodpocztowy", "kod")),
    Column("address", "adres", ("adres", "ulica")),
)

_COLUMN_BY_ALIAS: dict[str, str] = {alias: column.key for column in COLUMNS for alias in column.aliases}

#: Rodzaj placówki wolno zapisać w pliku kodem (``UNIVERSITY``) albo etykietą („uczelnia wyższa”).
#: Lista jest **zamknięta** i pochodzi wprost z ``InstitutionType`` – zgadywanie („wyższa szkoła
#: czegoś to pewnie uczelnia”) wpisywałoby do bazy rodzaj, którego organizator nie podał.
_TYPE_BY_TEXT: dict[str, str] = {
    **{fold(value): value for value in InstitutionType.values},
    **{fold(str(label)): value for value, label in InstitutionType.choices},
}


class RowError(ValueError):
    """Wiersz, którego nie da się zapisać. Niesie zdanie dla człowieka, bez numeru linii.

    Numer dokłada :func:`import_custom_institutions`, bo to ono wie, który wiersz właśnie czyta –
    dzięki temu tę samą funkcję normalizującą da się zawołać z testu i z podglądu, bez udawania
    numeracji pliku.
    """


@dataclass
class ImportIssue:
    """Błąd jednego wiersza: numer linii w pliku (nagłówek to linia 1) i zdanie dla człowieka."""

    line: int
    message: str

    def __str__(self) -> str:
        return f"wiersz {self.line}: {self.message}"


@dataclass
class ImportReport:
    """Wynik importu – **same liczniki i błędy**, bez treści wgranych wierszy.

    Taki kształt ma jeden powód: raport idzie do ekranu koordynatora (T23) i do audytu, a lista
    placówek organizatora jest jego listą kontrahentów. Numer linii wystarczy, żeby poprawić plik;
    nazwa w logu nie dokłada nic poza kopią danych w drugim miejscu.
    """

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deactivated: int = 0
    errors: list[ImportIssue] = field(default_factory=list)
    dry_run: bool = False

    @property
    def rows(self) -> int:
        """Ile wierszy pliku dało się zapisać (albo dałoby się przy ``dry_run``)."""
        return self.created + self.updated + self.unchanged

    @property
    def skipped(self) -> int:
        """Ile wierszy odpadło na błędzie – tyle samo, ile pozycji ma ``errors``."""
        return len(self.errors)

    def counts(self) -> dict:
        """Liczniki do audytu i do ekranu. Bez nazw, bez identyfikatorów, bez miejscowości."""
        return {
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "deactivated": self.deactivated,
            "skipped": self.skipped,
        }


# --- czytanie pliku -------------------------------------------------------------------------


def _decode_csv(data: bytes) -> str:
    """Tekst pliku CSV. Dwa kodowania, bo tyle wychodzi z Excela na komputerze koordynatora.

    ``utf-8-sig`` zdejmuje BOM, który Excel dokleja przy „CSV UTF-8”; gdy to nie jest UTF-8,
    zostaje ``cp1250`` – domyślne kodowanie polskiego Excela przy zwykłym „CSV (rozdzielany
    przecinkami)”. Reguła jest przepisana z ``apps.accounts.bulk_registration._decode_csv``
    świadomie: odmowa „zapisz jako UTF-8” przerzuca na człowieka problem, którego nazwy nie ma
    obowiązku znać.
    """
    for encoding in ("utf-8-sig", "cp1250"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DomainError(
        "Nie udało się odczytać pliku – zapisz go jako CSV w kodowaniu UTF-8.",
        "IMPORT_ENCODING",
        status.HTTP_400_BAD_REQUEST,
    )


def read_table(file) -> list[list[str]]:
    """Surowa tabela z wgranego pliku CSV. Rozmiar sprawdzamy **przed** czytaniem.

    Przyjmujemy i plik z formularza (``UploadedFile``), i same bajty – ekran koordynatora wgrywa
    to pierwsze, a test i komenda operują na drugim. Separator rozpoznajemy sami, bo polski Excel
    zapisuje średnikami, a arkusz z Google Docs przecinkami; plik jednokolumnowy nie ma czego
    rozpoznać i wtedy średnik jest wariantem częstszym.
    """
    data = file if isinstance(file, bytes | bytearray) else None
    if data is None:
        size = getattr(file, "size", None)
        if size is not None and size > MAX_UPLOAD_BYTES:
            raise DomainError(
                f"Plik jest za duży (maks. {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).",
                "IMPORT_TOO_LARGE",
                status.HTTP_400_BAD_REQUEST,
            )
        data = file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise DomainError(
            f"Plik jest za duży (maks. {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).",
            "IMPORT_TOO_LARGE",
            status.HTTP_400_BAD_REQUEST,
        )
    text = _decode_csv(bytes(data))
    try:
        delimiter = csv.Sniffer().sniff(text[:4096], delimiters=";,\t").delimiter
    except csv.Error:
        delimiter = ";"
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    return [[(cell or "").strip() for cell in row] for row in reader]


def map_header(row: list[str]) -> dict[int, str]:
    """Nagłówek pliku → mapa „numer kolumny w pliku → klucz w kodzie”.

    Kolumna nierozpoznana jest **pomijana**, a nie odrzucana: arkusz koordynatora bywa jego
    roboczym arkuszem i ma prawo mieć kolumnę „uwagi”. Brak kolumny obowiązkowej jest natomiast
    błędem całego pliku, a nie każdego wiersza z osobna – komunikat wymienia wtedy nagłówki,
    których ten import oczekuje.
    """
    mapping = {
        index: _COLUMN_BY_ALIAS[key]
        for index, cell in enumerate(row)
        if (key := header_key(cell)) in _COLUMN_BY_ALIAS
    }
    missing = [column.label for column in COLUMNS if column.required and column.key not in mapping.values()]
    if missing:
        expected = ", ".join(column.label for column in COLUMNS)
        raise DomainError(
            f"W pliku brakuje kolumny: {', '.join(missing)}. Nagłówek może zawierać: {expected}.",
            "IMPORT_HEADER",
            status.HTTP_400_BAD_REQUEST,
        )
    return mapping


def normalise_row(values: dict) -> dict:
    """Jeden wiersz pliku → pola ``CustomInstitution``. ``RowError`` zamiast cichej poprawki.

    Normalizacja jest **zachowawcza**: przycinamy białe znaki, składamy kraj do dwóch wielkich
    liter, rodzaj placówki rozpoznajemy z zamkniętej listy (kod albo etykieta). Czego nie robimy:
    nie zgadujemy rodzaju z nazwy, nie dopisujemy kraju „PL” i nie skracamy nazwy – wiersz, który
    nie mieści się w kolumnie, jest błędem do poprawienia w pliku, a nie do obcięcia po cichu.
    """
    name = (values.get("name") or "").strip()
    if len(name) < MIN_NAME_LENGTH:
        raise RowError("brak nazwy placówki")
    if len(name) > 255:
        raise RowError("nazwa jest dłuższa niż 255 znaków")
    institution_type = _institution_type(values.get("institution_type"))
    country = (values.get("country") or "").strip().upper()
    if country and (len(country) != 2 or not country.isascii() or not country.isalpha()):
        raise RowError("kraj podaj dwuliterowym kodem (ISO 3166-1), na przykład „DE”")
    city = (values.get("city") or "").strip()
    if len(city) > 120:
        raise RowError("miejscowość jest dłuższa niż 120 znaków")
    external_id = (values.get("external_id") or "").strip()
    if len(external_id) > 64:
        raise RowError("identyfikator jest dłuższy niż 64 znaki")
    return {
        "external_id": external_id,
        "name": name,
        "institution_type": institution_type,
        "country": country,
        "region_code": (values.get("region") or "").strip()[:40],
        "city": city,
        "postal_code": (values.get("postal_code") or "").strip()[:12],
        "address": (values.get("address") or "").strip()[:255],
    }


def _institution_type(text) -> str:
    """Rodzaj placówki z pliku. Pusty znaczy „inna placówka”, nieznany jest błędem wiersza."""
    value = (text or "").strip()
    if not value:
        return InstitutionType.OTHER
    found = _TYPE_BY_TEXT.get(fold(value))
    if found is None:
        allowed = ", ".join(InstitutionType.values)
        raise RowError(f"nieznany rodzaj placówki „{value}” – dopuszczalne: {allowed}")
    return found


# --- import ---------------------------------------------------------------------------------


def _match_key(row: dict) -> tuple:
    """Klucz rozpoznania wiersza: identyfikator organizatora, a bez niego nazwa i miejscowość.

    Dwa klucze, bo są dwa sposoby prowadzenia wykazu. Organizator z własną numeracją dostaje
    upsert odporny na zmianę nazwy (tak jak ``seed_schools`` po ``rspo``); organizator z arkuszem
    bez numerów – dopasowanie po tym, co w arkuszu jest. Nazwę i miejscowość składamy do postaci
    porównawczej, więc „Uniwersytet Jagielloński, Kraków” i „UNIWERSYTET JAGIELLOŃSKI, KRAKÓW”
    to jeden wiersz, a nie dwa.
    """
    if row["external_id"]:
        return ("id", row["external_id"])
    return ("name", fold(row["name"]), fold(row["city"]))


#: Pola przepisywane z pliku do wiersza przy upsercie. ``is_active`` jest osobno (wiersz obecny
#: w pliku **wraca** do aktywnych), a ``source_label`` bierze się z importu, nie z pliku.
_UPSERT_FIELDS = (
    "name",
    "institution_type",
    "country",
    "region_code",
    "city",
    "postal_code",
    "address",
)


def _region_codes(competition, values: set[str]) -> dict[str, str]:
    """Mapa „napis z pliku → kod regionu konkursu” – **jedno** zapytanie na cały import.

    Rozpoznajemy i po kodzie, i po nazwie („mazowieckie” oraz „Mazowieckie”), bo w arkuszu stoi
    to, co organizator ma w głowie, a nie to, co ma w bazie. Wynik jest kodem, a nie obiektem:
    kolumna jest napisem (patrz ``CustomInstitution.region_code``), a sprowadzenie wariantów do
    jednego kodu jest właśnie tym, po co ta funkcja istnieje.

    Pytamy tylko wtedy, gdy plik w ogóle ma niepustą wartość regionu: konkurs bez podziału
    terytorialnego nie ma powodu płacić za to zapytanie.
    """
    if not values:
        return {}
    from apps.accounts.models import Region

    found: dict[str, str] = {}
    for region in Region.objects.for_competition(competition):
        found.setdefault(fold(region.code), region.code)
        found.setdefault(fold(region.name), region.code)
    return found


def import_custom_institutions(
    competition,
    file,
    *,
    actor=None,
    request=None,
    dry_run: bool = False,
    deactivate_missing: bool = False,
    source_label: str = "",
) -> ImportReport:
    """Wgrywa wykaz placówek organizatora z pliku CSV. Zwraca :class:`ImportReport`.

    Przebieg jest ten sam, co przy imporcie uczniów, i celowo: odczyt → normalizacja wiersz po
    wierszu → rozstrzygnięcie („założę”, „poprawię”, „bez zmian”) → zapis w jednej transakcji.

    - **``dry_run=True``** liczy wszystko i **nie zapisuje niczego** – to jest podgląd przed
      zatwierdzeniem (§ 1.3.3). Raport z podglądu i raport z zapisu mają ten sam kształt, więc
      ekran pokazuje jedno i to samo zestawienie przed i po,
    - **upsert po ``external_id``**, a bez niego po parze nazwa + miejscowość (:func:`_match_key`);
      wiersz odnaleziony wraca do aktywnych, bo obecność w nowym pliku jest oświadczeniem
      organizatora, że placówka nadal jest w programie,
    - **``deactivate_missing=True``** wygasza wiersze nieobecne w pliku. Domyślnie **nie**, bo plik
      bywa uzupełnieniem, a nie całym wykazem – i dlatego to jest kratka na ekranie, a nie
      zachowanie domyślne. ``DELETE`` nie pada nigdy: wiersz może być wskazany przez profil
      uczestnika sprzed roku,
    - **błąd wiersza nie przerywa importu.** Plik z jedną literówką ma wejść w pozostałych
      wierszach, a koordynator ma dostać listę linii do poprawienia – odrzucenie całości znaczyłoby
      poprawianie pięciu tysięcy wierszy w kółko.

    Audyt (``custom_directory.imported``) niesie **wyłącznie liczniki**; celem wpisu jest konkurs,
    a nie żadna z placówek – zdarzeniem jest wgranie wykazu, nie zmiana pojedynczego wiersza.
    """
    table = read_table(file)
    if not table:
        raise DomainError("Plik jest pusty – nie ma w nim nawet nagłówka.", "IMPORT_EMPTY", 400)
    columns = map_header(table[0])
    report = ImportReport(dry_run=dry_run)
    rows: list[dict] = []
    seen_keys: set[tuple] = set()
    for line, raw in enumerate(table[1:], start=2):
        if not any((cell or "").strip() for cell in raw):
            continue
        if len(rows) >= MAX_ROWS:
            raise DomainError(
                f"Plik ma więcej niż {MAX_ROWS} wierszy – podziel go na części.",
                "IMPORT_TOO_MANY_ROWS",
                status.HTTP_400_BAD_REQUEST,
            )
        values = {key: (raw[index] if index < len(raw) else "") for index, key in columns.items()}
        try:
            cleaned = normalise_row(values)
        except RowError as exc:
            report.errors.append(ImportIssue(line, str(exc)))
            continue
        key = _match_key(cleaned)
        if key in seen_keys:
            report.errors.append(ImportIssue(line, "ta placówka jest w pliku drugi raz"))
            continue
        seen_keys.add(key)
        rows.append(cleaned)
    _apply(
        competition,
        rows,
        report=report,
        dry_run=dry_run,
        deactivate_missing=deactivate_missing,
        source_label=source_label[:120],
    )
    if not dry_run:
        audit(actor, AUDIT_IMPORTED, competition, report.counts(), request=request)
    return report


def _apply(competition, rows, *, report, dry_run, deactivate_missing, source_label) -> None:
    """Rozstrzyga i zapisuje – w jednej transakcji, żeby nie zostawić wykazu w połowie."""
    existing = list(CustomInstitution.objects.for_competition(competition))
    by_key: dict[tuple, CustomInstitution] = {}
    for row in existing:
        by_key.setdefault(("name", fold(row.name), fold(row.city)), row)
    for row in existing:
        if row.external_id:
            by_key[("id", row.external_id)] = row
    codes = _region_codes(competition, {row["region_code"] for row in rows if row["region_code"]})
    touched: set[int] = set()
    to_create: list[CustomInstitution] = []
    to_update: list[CustomInstitution] = []
    for values in rows:
        if values["region_code"]:
            # Kod nierozpoznany zostaje taki, jaki jest – patrz ``CustomInstitution.region_code``.
            values["region_code"] = codes.get(fold(values["region_code"]), values["region_code"])
        found = by_key.get(_match_key(values))
        if found is None:
            report.created += 1
            to_create.append(CustomInstitution(competition=competition, source_label=source_label, **values))
            continue
        touched.add(found.pk)
        changed = [name for name in _UPSERT_FIELDS if getattr(found, name) != values[name]]
        if not found.is_active:
            changed.append("is_active")
        if not changed:
            report.unchanged += 1
            continue
        report.updated += 1
        for name in _UPSERT_FIELDS:
            setattr(found, name, values[name])
        found.is_active = True
        found.source_label = source_label
        to_update.append(found)
    missing = [row for row in existing if row.pk not in touched and row.is_active]
    if deactivate_missing:
        report.deactivated = len(missing)
    if dry_run:
        return
    with transaction.atomic():
        for row in to_create:
            # ``save()`` po kolei, a nie ``bulk_create``: kolumny wyliczane liczy ``save()``, a
            # ominięcie go zostawiłoby wiersz, którego wyszukiwarka nigdy nie znajdzie. Pięć
            # tysięcy wierszy wgrywa się raz na sezon, więc koszt jest po właściwej stronie.
            row.save()
        for row in to_update:
            row.save()
        if deactivate_missing and missing:
            CustomInstitution.objects.filter(pk__in=[row.pk for row in missing]).update(is_active=False)


# --- wyszukiwarka ---------------------------------------------------------------------------


def search_custom_institutions(
    competition,
    query: str,
    *,
    institution_types: tuple[str, ...] | None = None,
    city: str = "",
    limit: int = MAX_RESULTS,
) -> list[CustomInstitution]:
    """Placówki organizatora pasujące do zapytania – druga połowa wyszukiwarki z § 1.3.3.

    Normalizacja jest **ta sama**, co w ``apps.schools.api.search_schools``: zapytanie składamy
    ``fold``-em, dzielimy na wyrazy i każdy z nich musi trafić w ``search_text`` (koniunkcja
    tokenów), a miejscowość zawęża po ``city_search`` razem z dzielnicami. Dwie reguły znaczyłyby,
    że ta sama fraza wpisana w to samo okno znajduje placówkę z jednego wykazu, a z drugiego nie.

    **Bez flagi nie pada ani jedno zapytanie.** Warunek sprawdzamy tutaj, a nie tylko u wołającego
    (T25), bo to jest zdanie z § 5.6 i ma być prawdziwe niezależnie od tego, kto tę funkcję
    zawoła: ``custom_directory_enabled`` przy wyłączonej fladze odpowiada bez dotykania bazy.

    Wynikiem jest **lista**, a nie ``QuerySet``: wołający (wyszukiwarka dwóch słowników) skleja ją
    z wynikiem wykazu publicznego, a queryset kusiłby ``.count()``, czyli drugim przejściem po tym
    samym zbiorze.
    """
    from apps.accounts.services import custom_directory_enabled

    if not custom_directory_enabled(competition):
        return []
    tokens = fold(query or "").split()
    city_key = fold(city or "").strip()
    if not city_key and (not tokens or len(fold(query or "").strip()) < MIN_QUERY_LENGTH):
        return []
    queryset = CustomInstitution.objects.for_competition(competition).filter(is_active=True)
    if city_key:
        queryset = queryset.filter(
            models.Q(city_search=city_key)
            | models.Q(city_search__startswith=f"{city_key}{DISTRICT_SEPARATOR}")
        )
    if institution_types:
        queryset = queryset.filter(institution_type__in=tuple(institution_types))
    for token in tokens:
        # ``contains``, nie ``icontains`` – obie strony porównania są już złożone, tak jak przy
        # ``School`` (``apps.schools.api.search_schools``).
        queryset = queryset.filter(search_text__contains=token)
    return list(queryset.order_by("name", "id")[: max(1, min(limit, MAX_RESULTS))])
