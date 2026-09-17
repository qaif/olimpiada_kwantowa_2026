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

from django.db import models

from apps.accounts.models import Voivodeship
from apps.core.text import fold

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


def search_text_for(name: str, city: str) -> str:
    """Postać porównawcza wiersza: „nazwa miejscowość” bez diakrytyków, małymi literami.

    Wyszukiwanie idzie po tej kolumnie, a nie po ``name``/``city`` z ``icontains``, z dwóch
    powodów. Po pierwsze **diakrytyki**: uczeń wpisujący „lodz” ma znaleźć „ŁÓDŹ”, a Postgres bez
    rozszerzenia ``unaccent`` tego nie zrobi (a rozszerzenie wymaga uprawnień na bazie, których
    aplikacja w produkcji nie ma). Po drugie **jedno pole na token**: zapytanie „mickiewicza
    krakow” ma trafić w wiersz, w którym jeden wyraz jest w nazwie, a drugi w mieście – przy
    dwóch osobnych kolumnach wymagałoby to iloczynu wariantów.
    """
    return fold(f"{name} {city}".strip())


def city_search_for(city: str) -> str:
    """Postać porównawcza samej miejscowości – dla kroku „Miejscowość” w wyszukiwarce szkół.

    Osobna kolumna obok ``search_text``, bo pytania są dwa różne. ``search_text`` odpowiada na
    „czy ten wiersz pasuje do wpisanego tekstu” (zlepek nazwy i miasta, dopasowanie po fragmencie);
    ta kolumna odpowiada na „jakie **miejscowości** zaczynają się od tych liter” i na „pokaż
    wszystkie szkoły dokładnie tej miejscowości”. Z ``search_text`` żadnego z tych dwóch pytań nie
    da się zadać: „wroclaw” jako fragment zlepka trafia także w nazwę („Zespół Szkół Wrocławskich”
    w Oleśnicy), a listy odrębnych miast nie da się z niego wyjąć w ogóle.

    Kolumna jest **wyliczana**, a nie odczytywana rozszerzeniem ``unaccent`` w locie: rozszerzenie
    wymaga uprawnień na bazie, których aplikacja na produkcji nie ma, a dopasowanie prefiksowe po
    zwykłej kolumnie z indeksem jest dla planisty czytelne (``LIKE 'wroc%'`` używa indeksu, a
    ``unaccent(city) LIKE …`` już nie – chyba że postawić indeks funkcyjny, czyli znów migrację
    z rozszerzeniem).
    """
    return fold(city.strip())


class School(models.Model):
    """Szkoła z wykazu SIO. Dane referencyjne – redakcja ich nie tworzy, wgrywa je ``seed_schools``."""

    rspo = models.PositiveIntegerField("numer RSPO", unique=True)
    name = models.CharField("nazwa", max_length=255)
    kind = models.CharField("typ", max_length=16, choices=SchoolKind.choices, default=SchoolKind.INNA)
    voivodeship = models.CharField("województwo", max_length=100, choices=Voivodeship.choices)
    city = models.CharField("miejscowość", max_length=120)
    postal_code = models.CharField("kod pocztowy", max_length=12, blank=True)
    address = models.CharField("adres", max_length=255, blank=True)
    is_public = models.BooleanField("publiczna", default=True)
    search_text = models.CharField("tekst wyszukiwania", max_length=400, db_index=True, editable=False)
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
            # Krok „Miejscowość”: podpowiedź miast (prefiks po ``city_search``) i pełna lista szkół
            # wybranego miasta uporządkowana typem, a potem nazwą. Jeden indeks obsługuje oba
            # zapytania, bo oba zaczynają się od tej samej kolumny.
            models.Index(fields=("city_search", "kind", "name"), name="schools_city_kind_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name}, {self.city}"

    def save(self, *args, **kwargs):
        """Kolumny porównawcze są wyliczane, nie wpisywane – nie mają jak rozjechać się z danymi."""
        self.search_text = search_text_for(self.name, self.city)[:400]
        self.city_search = city_search_for(self.city)[:120]
        super().save(*args, **kwargs)
