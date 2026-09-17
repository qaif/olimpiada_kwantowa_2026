"""Układ dyplomu opisany danymi: gdzie i jak dużym pismem stoi każdy element.

Dlaczego osobny moduł, a nie stała w ``certificates.py``. Ten słownik czytają trzy strony naraz:
model (``CertificateTemplate.layout`` ma go za wartość domyślną – i to **migracja** zapisuje
referencję do funkcji), skład PDF-a i panel koordynatora, który pokazuje go w polu tekstowym.
Gdyby mieszkał w module składu, model ciągnąłby przy imporcie cały generator dokumentów, a ten
z kolei importuje model – czyli cykl. Moduł jest więc **liściem**: nie importuje niczego z aplikacji.

Współrzędne liczymy od **górnej** krawędzi strony, a nie od dolnej jak reportlab. Powód jest
prozaiczny: układ wpisuje człowiek, patrząc na dokument, a dokument czyta się od góry. „Nazwisko
260 punktów od góry” da się sprawdzić linijką na wydruku; „335 od dołu” wymaga odjęcia wysokości
strony w pamięci i jest pierwszym miejscem, w którym redaktor się pomyli. Przeliczenie na układ
reportlaba jest jedną linijką w składzie (``height - y``).

Strona jest A4 poziomo (842 × 595 pkt) – dyplom jest jedną kartą z nazwiskiem pośrodku,
a nie formularzem.
"""

from __future__ import annotations

import copy

#: Wymiary strony w punktach (A4 poziomo). Powtórzone tutaj jako liczby, a nie liczone
#: z ``reportlab.lib.pagesizes``, bo moduł ma nie importować biblioteki składu – patrz nagłówek.
PAGE_WIDTH = 842
PAGE_HEIGHT = 595

#: Domyślny układ: dokładnie ten, którym olimpiada składa dyplomy od pierwszej edycji. Szablon
#: graficzny zaczyna się od kopii tych wartości, więc redaktor przesuwa napisy względem czegoś,
#: co już działa, zamiast układać stronę od pustej kartki.
#:
#: Klucze bloków tekstowych: ``y`` (od góry), ``size`` (stopień pisma), ``bold``, ``x``
#: (pominięte = wyśrodkowanie na stronie), ``leading`` (odstęp wierszy w blokach wielowierszowych).
#: ``show: false`` wyłącza blok – tak gasi się np. nagłówek, gdy nazwa olimpiady jest już na tle.
DEFAULT_LAYOUT: dict[str, dict] = {
    # Nagłówek i edycja. ``text`` jest tu polem układu, a nie treści: to jedyny napis dyplomu,
    # który nie wynika z żadnego faktu w bazie, więc szablon może go zmienić bez zmiany kodu.
    "organiser": {"y": 90, "size": 22, "bold": True, "text": "OLIMPIADA KWANTOWA"},
    "edition": {"y": 120, "size": 13},
    "title": {"y": 190, "size": 28, "bold": True},
    "recipient": {"y": 260, "size": 24, "bold": True},
    "school": {"y": 288, "size": 13},
    "statement": {"y": 330, "size": 15},
    # Lista warsztatów – blok wielowierszowy, obecny wyłącznie na zaświadczeniu z warsztatów.
    # Na pozostałych dokumentach nie ma czego wypisać i blok po prostu nic nie rysuje.
    "workshops": {"y": 372, "size": 11, "leading": 15},
    # Linia podpisu odręcznego. ``width`` to długość kreski, a nie szerokość napisu pod nią.
    "signatures": {"y": 445, "size": 10, "width": 240},
    # Numer, kod i data – wszystko, po czym rozpoznaje się dokument, w jednym pasie u dołu.
    # ``margin`` odsuwa obie kolumny od krawędzi kartki (lewa: numer i kod, prawa: data).
    "footer": {"y": 525, "size": 9, "margin": 60},
    # Kod QR z adresem weryfikacji. Stoi nad stopką po prawej, czyli tam, gdzie i tak szuka się
    # numeru dokumentu – a nie pośrodku, gdzie zasłaniałby podpis.
    "qr": {"x": 712, "y": 455, "size": 74},
    # Logo szablonu (lewy górny róg). Bez szablonu nie ma pliku i blok nic nie rysuje.
    "logo": {"x": 60, "y": 40, "width": 140, "height": 60},
}


def default_certificate_layout() -> dict:
    """Wartość domyślna ``CertificateTemplate.layout``.

    ``default`` pola JSON musi być wywoływalne i oddawać **nowy** obiekt – wspólny słownik
    współdzieliłby jedną tablicę między wszystkimi wierszami tabeli, a pierwsze przesunięcie
    napisu w jednym szablonie przesunęłoby go we wszystkich.
    """
    return copy.deepcopy(DEFAULT_LAYOUT)


def block(layout: dict | None, name: str) -> dict:
    """Ustawienia jednego bloku: wartości z szablonu na wierzchu wartości domyślnych.

    Scalanie jest tu istotą rzeczy. Redaktor, który chce przesunąć samo nazwisko, zapisuje
    ``{"recipient": {"y": 300}}`` i nie musi przepisywać całej reszty – a szablon zapisany przy
    starszej wersji układu nie gubi bloku, który doszedł później (dostaje jego wartości domyślne).
    """
    merged = dict(DEFAULT_LAYOUT.get(name, {}))
    override = (layout or {}).get(name)
    if isinstance(override, dict):
        merged.update(override)
    return merged


def is_visible(settings: dict) -> bool:
    """Czy blok ma się narysować. Brak klucza ``show`` znaczy „tak” – bloki są domyślnie widoczne."""
    return bool(settings.get("show", True))
