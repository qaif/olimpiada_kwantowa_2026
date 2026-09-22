"""Słownik szkół ponadpodstawowych – dane referencyjne z rejestru SIO/RSPO.

Po co osobna aplikacja, skoro uczestnik ma pole ``school``:

- **wolny tekst nie grupuje.** „II LO w Krakowie”, „2 LO Kraków” i „Liceum Ogólnokształcące nr 2”
  to dla bazy trzy różne szkoły, więc żadna statystyka po szkołach (ani próg k-anonimowości
  w publikacji wyników) nie ma czego zliczyć,
- **rejestr ma stabilny identyfikator.** ``RSPO`` jest kluczem nadanym przez Ministerstwo
  Edukacji; zmiana nazwy szkoły albo jej adresu nie tworzy nowego wiersza, tylko aktualizuje ten
  sam. Dlatego upsert w ``seed_schools`` idzie po ``rspo``, a nie po nazwie.

Czego ten model **nie** zastępuje: pola ``Participant.school``. Tam nadal ląduje tekst do
pokazania (nazwa wybrana z listy albo wpisana ręcznie), bo rejestr nie jest kompletny – szkoły
zagraniczne, świeżo założone i placówki spoza wykazu istnieją i nie mogą blokować rejestracji.
``Participant.school_ref`` jest więc opcjonalnym dowiązaniem, a nie jedyną drogą.
"""

from django.contrib.postgres.indexes import GinIndex
from django.db import models

from apps.accounts.models import Voivodeship

from .normalise import derived_fields

#: Rok szkolny wykazu, z którego pochodzi wgrany słownik. Trzymany przy wierszu, a nie tylko
#: w README: po odświeżeniu fixture'u widać w bazie, które wpisy pochodzą ze starego wykazu.
DEFAULT_SOURCE_YEAR = "2025/2026"


class SchoolKind(models.TextChoices):
    """Typ szkoły – uproszczenie „Typu podmiotu” z wykazu SIO do garści etykiet dla ucznia.

    Wykaz rozróżnia kilkadziesiąt typów podmiotów, w tym takie, których uczeń nigdy o sobie nie
    powie („Ogólnokształcąca szkoła muzyczna II stopnia”). Do wyboru w podpowiedzi wystarczy
    rozróżnienie, które pomaga odsiać trafienia o podobnych nazwach w jednym mieście.
    """

    LO = "LO", "liceum ogólnokształcące"
    TECHNIKUM = "TECHNIKUM", "technikum"
    BRANZOWA_1 = "BRANZOWA_1", "branżowa szkoła I stopnia"
    BRANZOWA_2 = "BRANZOWA_2", "branżowa szkoła II stopnia"
    ARTYSTYCZNA = "ARTYSTYCZNA", "szkoła artystyczna"
    SPECJALNA = "SPECJALNA", "szkoła specjalna przysposabiająca do pracy"
    INNA = "INNA", "inna"


#: Typy szkół wypisywane na początku listy, w tej kolejności; reszta idzie po nich, alfabetycznie.
#: Nie jest to ocena szkół, tylko odwzorowanie tego, kto startuje w olimpiadzie z fizyki kwantowej:
#: uczniowie liceów ogólnokształcących i techników. Zgłoszenie organizatora brzmiało „okno wyboru
#: szkoły po wpisaniu «wrocław» nie pokazuje liceów ogólnokształcących” – przy porządku wyłącznie
#: alfabetycznym pierwsze dwadzieścia trafień dla dużego miasta potrafi nie zawierać ani jednego
#: liceum, bo nazwy branżowych i techników zaczynają się od wcześniejszych liter.
KIND_ORDER: tuple[str, ...] = (SchoolKind.LO, SchoolKind.TECHNIKUM)


class InstitutionType(models.TextChoices):
    """Rodzaj placówki – **szersza oś** niż ``SchoolKind``: tamten rozróżnia liceum od technikum
    wewnątrz szkół ponadpodstawowych, ten rozróżnia szkołę podstawową od uczelni i od jej braku.

    Dwie osie, a nie jedna rozszerzona lista, bo pytania są różne. ``SchoolKind`` steruje
    **porządkiem podpowiedzi** wewnątrz jednego miasta (``KIND_ORDER``) i pochodzi z „Typu
    podmiotu” w wykazie; ``InstitutionType`` mówi, **z którego wykazu** wiersz w ogóle pochodzi
    i czy dany konkurs go dopuszcza. Zlanie ich w jedną kolumnę znaczyłoby, że dołożenie uczelni
    przestawia kolejność liceów – czyli zmianę widoczną w Konkursie #1 bez żadnego powodu.

    Trzy ostatnie wartości **nie mają wierszy w** ``School``: placówka poza Polską, brak szkoły
    i „inna placówka” to sytuacje uczestnika, a nie pozycje rejestru – tam nazwę wpisuje się
    wolnym tekstem (§ 1.3.2 planu etapu 2).
    """

    PRIMARY = "PRIMARY", "szkoła podstawowa"
    SECONDARY = "SECONDARY", "szkoła ponadpodstawowa"
    UNIVERSITY = "UNIVERSITY", "uczelnia wyższa"
    FOREIGN = "FOREIGN", "placówka poza Polską"
    NONE = "NONE", "bez szkoły"
    OTHER = "OTHER", "inna placówka"


class School(models.Model):
    """Szkoła z wykazu SIO. Dane referencyjne – redakcja ich nie tworzy, wgrywa je ``seed_schools``."""

    rspo = models.PositiveIntegerField("numer RSPO", unique=True)
    name = models.CharField("nazwa", max_length=255)
    kind = models.CharField("typ", max_length=16, choices=SchoolKind.choices, default=SchoolKind.INNA)
    # Rodzaj placówki – kolumna, a nie osobna tabela, bo słownik zostaje **jeden i wspólny dla
    # instalacji** (etap 1 § 8, decyzja D2): rejestr ministerialny jest tym samym rejestrem dla
    # każdego konkursu, więc rozbicie go po organizatorach znaczyłoby kilka kopii tych samych
    # ośmiu tysięcy wierszy. Domyślna wartość jest ``SECONDARY``, bo tyle i wyłącznie tyle zawiera
    # dzisiejszy wykaz; wiersz bez tej informacji (fixture sprzed etapu 2) jest szkołą
    # ponadpodstawową i nikt nie musi tego wpisywać ręcznie.
    institution_type = models.CharField(
        "rodzaj placówki",
        max_length=16,
        choices=InstitutionType.choices,
        default=InstitutionType.SECONDARY,
        db_index=True,
    )
    voivodeship = models.CharField("województwo", max_length=100, choices=Voivodeship.choices)
    city = models.CharField("miejscowość", max_length=120)
    # Gmina wyliczona z ``city`` (``apps.schools.normalise``). Osobna kolumna, a nie poprawiony
    # ``city``, bo oba napisy są prawdziwe i oba są potrzebne: „Wrocław-Krzyki” to adres szkoły
    # w rejestrze i tak trzeba go pokazać uczniowi, a „Wrocław” to miasto, którego ten uczeń
    # szuka w formularzu. Nadpisanie ``city`` zgubiłoby dzielnicę, a brak ``city_parent``
    # zostawiał stolicę w słowniku pod osiemnastoma nazwami i pod żadnym „Warszawa”.
    city_parent = models.CharField(
        "gmina (miejscowość nadrzędna)", max_length=120, db_index=True, editable=False, default=""
    )
    postal_code = models.CharField("kod pocztowy", max_length=12, blank=True)
    address = models.CharField("adres", max_length=255, blank=True)
    is_public = models.BooleanField("publiczna", default=True)
    # Bez ``db_index=True``: wyszukiwarka pyta tę kolumnę wyłącznie ``contains`` (koniunkcja
    # tokenów w ``search_schools``), a zwykły B-tree (razem z automatycznym indeksem
    # ``varchar_pattern_ops`` pod ``LIKE 'prefiks%'``) nie umie obsłużyć dopasowania w środku
    # napisu – stąd 864 sekwencyjnych przejść po tabeli na produkcji (22.09.2026) mimo indeksu.
    # Właściwy indeks pod ``contains`` jest w ``Meta.indexes`` niżej (GIN + ``pg_trgm``).
    search_text = models.CharField("tekst wyszukiwania", max_length=400, editable=False)
    # Postać porównawcza miejscowości: „wroclaw krzyki”, „warszawa srodmiescie”, „gdansk”.
    # Gmina stoi na początku, dzielnica jest osobnym wyrazem – reguła i uzasadnienie w
    # ``apps.schools.normalise.city_search_for``.
    city_search = models.CharField(
        "miejscowość (postać porównawcza)", max_length=120, db_index=True, editable=False, default=""
    )
    # Wiersz zniknięty z nowego wykazu (szkoła zlikwidowana albo przekształcona) jest wygaszany,
    # a nie kasowany: może być wskazany przez ``Participant.school_ref`` sprzed roku, a historia
    # zgłoszeń nie ma prawa zniknąć razem z aktualizacją słownika (stąd też ``on_delete=PROTECT``).
    is_active = models.BooleanField("aktywna", default=True)
    source_year = models.CharField("rok wykazu", max_length=9, default=DEFAULT_SOURCE_YEAR)

    class Meta:
        verbose_name = "szkoła"
        verbose_name_plural = "szkoły"
        ordering = ("name", "id")
        indexes = [
            # Podpowiedzi są filtrowane województwem wybranym w formularzu – para (województwo,
            # tekst) jest więc dokładnie tym, po czym chodzi zapytanie wyszukiwarki.
            models.Index(fields=("voivodeship", "search_text"), name="schools_voiv_search_idx"),
            # ``schools_city_kind_idx`` (city_search, kind, name) zdjęty stąd 22.09.2026: zero
            # skanów na produkcji przez 15 dni. Porządek listy liczy ``_kind_rank()`` w
            # ``apps.schools.api`` – wyrażenie ``Case``, nie kolumna ``kind`` – więc ten indeks
            # nigdy nie mógł posłużyć sortowaniu, a filtr ``city_search`` obsługuje sam siebie
            # (indeks jednokolumnowy niżej, plus GIN pod ``contains`` przy dzielnicach).
            #
            # GIN + ``pg_trgm`` pod dopasowanie **w środku napisu** (``contains``), którego zwykły
            # B-tree nie obsłuży: ``search_text__contains`` (koniunkcja tokenów w
            # ``search_schools``) i ``city_search__contains`` (dzielnica po separatorze w
            # ``search_cities``/``search_schools``). Rozszerzenie włącza migracja
            # ``0006_pg_trgm_search_indexes`` (``TrigramExtension``) – rola aplikacyjna nie
            # potrzebuje do tego uprawnień superużytkownika (``pg_trgm`` jest zaufanym
            # rozszerzeniem od PostgreSQL 13).
            GinIndex(fields=["search_text"], name="schools_search_trgm_idx", opclasses=["gin_trgm_ops"]),
            # Prefiks (``city_search__startswith``) nadal obsługuje ``varchar_pattern_ops``
            # doklejany automatycznie przez ``db_index=True`` na kolumnie – ten indeks jest tylko
            # dla wariantu ``contains`` (dzielnica).
            GinIndex(fields=["city_search"], name="schools_city_trgm_idx", opclasses=["gin_trgm_ops"]),
        ]

    def __str__(self) -> str:
        return f"{self.name}, {self.city}"

    def save(self, *args, **kwargs):
        """Kolumny wyliczane są liczone, nie wpisywane – nie mają jak rozjechać się z danymi.

        Gmina też: ``city_parent`` jest funkcją miejscowości, województwa i kodu pocztowego
        (``apps.schools.normalise.parent_city``), więc wiersz poprawiony w adminie albo założony
        w teście dostaje ją tak samo jak wiersz z wykazu.
        """
        computed = derived_fields(self.name, self.city, self.voivodeship, self.postal_code)
        for field, value in computed.items():
            setattr(self, field, value)
        super().save(*args, **kwargs)


# Słownik własny organizatora (``CustomInstitution``, § 1.3.3) mieszka w osobnym module razem ze
# swoim importem CSV i wyszukiwarką – tam czyta się je jako jedną sprawę, a ten plik zostaje
# opisem **wykazu publicznego**. Import stoi na końcu, a nie na górze, bo ``apps.schools.custom``
# potrzebuje ``InstitutionType`` zdefiniowanego wyżej; Django ładuje wtedy oba modele razem
# z aplikacją, więc ``makemigrations`` i rejestr modeli widzą ``CustomInstitution`` tak samo jak
# ``School``.
from .custom import CustomInstitution  # noqa: E402,F401  (import na końcu – patrz komentarz wyżej)
