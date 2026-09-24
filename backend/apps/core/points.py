"""Punkty jako liczba: jedno wejście, jedno wyjście i jedna postać w JSON-ie (v0.35.0).

Od wydania 0.35.0 ocena nie musi być liczbą całkowitą: etap w trybie „dowolna wartość od min do
max” przyjmuje np. 4,25 (prośba organizatora z 2026-09-24). Kolumny punktów są od tej pory
``DecimalField(decimal_places=2)``, a ten moduł jest **jedynym** miejscem, które wie, jak liczbę
punktów przeczytać od człowieka, jak ją pokazać i jak zapisać w JSON-ie. Powód skupienia jest ten
sam, co przy ``apps.core.text.fold``: trzy kopie reguły „przecinek czy kropka” rozjechałyby się
przy pierwszej poprawce, a skutkiem byłaby ta sama ocena pokazana jako „4,25” na ekranie
i „4.3” w PDF-ie.

Zasady, które moduł egzekwuje:

- **żadnego ``float`` w rachunku.** Liczba wchodzi jako ``Decimal`` i jako ``Decimal`` jest
  porównywana, sumowana i zaokrąglana. ``float`` pojawia się wyłącznie na samym wyjściu do JSON-a
  (``points_json``) – bo JSON nie zna typu dziesiętnego – i wyłącznie dla wartości, która ma już
  najwyżej dwa miejsca po przecinku; ``repr`` takiej liczby jest najkrótszym zapisem, więc
  ``4.25`` zapisuje się jako ``4.25``, a odczyt przez ``str`` oddaje dokładnie ``Decimal("4.25")``,
- **dwa miejsca po przecinku to reguła wejścia, nie zaokrąglenie.** ``parse_points`` odmawia
  ``4,255`` zamiast po cichu zrobić z niego ``4,26``: recenzent, który wpisał trzy cyfry, pomylił
  się, a system nie zgaduje, w którą stronę,
- **wyświetlanie bez zbędnych zer.** Liczba całkowita pokazuje się jak dotąd („5”, nie „5,00”),
  reszta z przecinkiem po polsku i kropką po angielsku, z najwyżej dwiema cyframi („4,25”, „3,5”),
- **CSV zawsze z kropką.** Eksport jest dla arkusza i dla skryptu; przecinek dziesiętny w pliku
  rozdzielanym przecinkami (albo średnikami, zależnie od programu) jest przepisem na przesunięte
  kolumny. ``points_csv`` zwraca więc tę samą postać niezależnie od języka interfejsu.

Moduł nie importuje modeli – woła go i warstwa domeny, i szablony, i eksporty.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.utils.translation import get_language

#: Liczba miejsc po przecinku w każdej kolumnie punktów. Zmiana tej stałej wymaga migracji.
POINTS_PLACES = 2
#: Najmniejszy krok oceny w trybie dowolnym: 0,01 pkt.
POINTS_QUANTUM = Decimal("0.01")
#: Krok, do którego zaokrągla się suma etapu w trybie „tylko wartości ze skali” – pełne punkty,
#: jak przed wydaniem 0.35.0 (``competitions.services.StageScoring``).
WHOLE_POINTS = Decimal("1")
#: Pojedyncza ocena: do 99 999,99 – zapas ponad ``PositiveSmallIntegerField`` (32 767), który
#: trzymał te kolumny do wydania 0.35.0, więc żadna dotychczasowa wartość nie może się nie zmieścić.
SCORE_MAX_DIGITS = 7
#: Suma etapu i próg punktowy: do 99 999 999,99 – zapas ponad ``PositiveIntegerField`` sumy
#: w praktyce (dotychczasowe kolumny sum trzymały liczby rzędu setek).
TOTAL_MAX_DIGITS = 10
#: Największa liczba, jaką ``parse_points`` w ogóle przeczyta – pojemność kolumny sumy etapu.
MAX_PARSED_POINTS = Decimal("99999999.99")

#: Tekst liczby od człowieka: opcjonalny minus, cyfry, opcjonalnie separator dziesiętny (kropka
#: albo przecinek) i cyfry. Bez separatorów tysięcy i bez notacji wykładniczej – „1e2” nie jest
#: oceną, którą ktoś wpisał świadomie.
_NUMBER_RE = re.compile(r"^-?\d+(?:[.,]\d+)?$")


class PointsError(ValueError):
    """Wartość nie jest poprawną liczbą punktów. ``code`` idzie do API jako kod błędu."""

    def __init__(self, message: str, code: str = "SCORE_INVALID") -> None:
        super().__init__(message)
        self.code = code


def _finite(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise PointsError("Punkty muszą być liczbą.")
    return value


def parse_points(raw) -> Decimal:
    """Liczba punktów **wpisana przez człowieka albo przysłana przez klienta API** → ``Decimal``.

    Przyjmuje ``int``, ``Decimal``, ``float`` (JSON z API) i tekst z przecinkiem albo kropką
    („4,25”, „4.25”, „ 5 ”). Odmawia wartości logicznych (``True`` jest w Pythonie jedynką, ale
    nie jest oceną), tekstu, który nie jest liczbą, i liczby o więcej niż dwóch miejscach po
    przecinku – ``PointsError`` z kodem ``SCORE_INVALID``.

    Zwraca wartość sprowadzoną do dwóch miejsc (``Decimal("4.25")``, ``Decimal("5.00")``). To nie
    jest zaokrąglenie: do dwóch miejsc sprowadza się wyłącznie liczba, która już je miała
    (``4.250`` → ``4.25``). Czy ocena mieści się w skali, rozstrzyga reguła etapu
    (``apps.competitions.scoring.ScoreRule``), a nie ta funkcja.
    """
    if isinstance(raw, bool) or raw is None:
        raise PointsError("Punkty muszą być liczbą.")
    if isinstance(raw, int):
        value = Decimal(raw)
    elif isinstance(raw, Decimal):
        value = _finite(raw)
    elif isinstance(raw, float):
        # ``repr`` float-a jest najkrótszym zapisem, który czyta się z powrotem do tej samej
        # liczby – więc 4.25 z JSON-a staje się Decimal("4.25"), a nie 4.2500000000000000177…
        try:
            value = _finite(Decimal(repr(raw)))
        except InvalidOperation as exc:  # pragma: no cover - repr float-a jest zawsze liczbą
            raise PointsError("Punkty muszą być liczbą.") from exc
    elif isinstance(raw, str):
        text = raw.strip().replace(" ", "").replace(" ", "")
        if not _NUMBER_RE.match(text):
            raise PointsError(f"„{raw.strip()}” nie jest liczbą punktów (np. 4 albo 4,25).")
        value = Decimal(text.replace(",", "."))
    else:
        raise PointsError("Punkty muszą być liczbą.")
    # Granica **przed** zaokrągleniem do setnych: ``quantize`` liczby rzędu 1e30 przekracza precyzję
    # kontekstu ``decimal`` i rzuca ``InvalidOperation`` – czyli 500 zamiast odmowy. Żadna ocena ani
    # próg w tym systemie nie zbliża się do tej granicy (kolumna sumy mieści 99 999 999,99).
    if abs(value) > MAX_PARSED_POINTS:
        raise PointsError("Liczba punktów jest zbyt duża.")
    quantized = value.quantize(POINTS_QUANTUM)
    if quantized != value:
        raise PointsError("Punkty mogą mieć najwyżej dwa miejsca po przecinku (np. 4,25).")
    return quantized


def to_points(value) -> Decimal | None:
    """Liczba punktów **już zapisana** (kolumna, snapshot JSON, audyt) → ``Decimal`` albo ``None``.

    Czytnik pobłażliwy, w odróżnieniu od ``parse_points``: nie odmawia, tylko sprowadza do liczby
    wszystko, co mogło trafić do bazy przez kolejne wydania – ``int`` ze snapshotu sprzed 0.35.0,
    ``float`` ze snapshotu nowszego, ``Decimal`` z kolumny i tekst z eksportu. Wartość, której nie
    da się przeczytać jako liczby, daje ``None``: ekran pokazuje wtedy kreskę, a nie błąd 500.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        try:
            result = Decimal(repr(value))
        except InvalidOperation:  # pragma: no cover
            return None
        return result if result.is_finite() else None
    if isinstance(value, str):
        text = value.strip().replace(",", ".")
        try:
            result = Decimal(text)
        except InvalidOperation:
            return None
        return result if result.is_finite() else None
    return None


def round_points(value, quantum: Decimal = POINTS_QUANTUM) -> Decimal:
    """Zaokrąglenie **połówka w górę** do ``quantum`` (0,01 albo pełny punkt) – jedyna metoda w systemie.

    ``ROUND_HALF_UP``, a nie bankierskie ``ROUND_HALF_EVEN`` (domyślne w ``decimal``): regulamin
    i protokół komisji liczą „od połowy w górę”, a tabela wyników, w której 2,5 daje 2, a 3,5
    daje 4, wyglądałaby na błąd. Tą samą metodą sprowadza wynik testu do pełnych punktów
    ``apps.quiz.services`` i sumę ważoną ``competitions.services.StageScoring``.
    """
    number = to_points(value)
    if number is None:
        raise PointsError("Punkty muszą być liczbą.")
    return number.quantize(quantum, rounding=ROUND_HALF_UP)


def is_whole(value) -> bool:
    """Czy liczba punktów jest całkowita (``5``, ``Decimal("5.00")``)."""
    number = to_points(value)
    return number is not None and number == number.to_integral_value()


def points_json(value):
    """Liczba punktów w postaci do JSON-a: ``int``, gdy całkowita, inaczej ``float`` o ≤ 2 miejscach.

    ``int`` dla liczby całkowitej jest decyzją o **zgodności wstecz**: snapshot wyników, wpis
    audytu i odpowiedź API sprzed 0.35.0 niosły ``5``, a nie ``5.0`` ani ``"5.00"`` – klient,
    który porównuje ``points == 5``, i test, który porównuje słowniki na równość, mają dalej
    dostać dokładnie to samo. Wartość ułamkowa wychodzi jako liczba JSON (``4.25``), a nie tekst:
    typ pola się nie zmienia, zmienia się wyłącznie to, że czasem ma część ułamkową.
    """
    number = to_points(value)
    if number is None:
        return None
    number = number.quantize(POINTS_QUANTUM, rounding=ROUND_HALF_UP)
    if number == number.to_integral_value():
        return int(number)
    return float(number)


def jsonable_points(obj):
    """``obj`` z każdym ``Decimal`` zamienionym przez ``points_json`` – rekurencyjnie po słownikach i listach.

    Dla danych zapisywanych do ``JSONField`` bez własnego enkodera (audyt, snapshot wyników,
    ładunek webhooka): ``json`` z biblioteki standardowej nie umie zapisać ``Decimal`` i wywraca
    zapis wyjątkiem. Wszystko inne przechodzi bez zmian.
    """
    if isinstance(obj, Decimal):
        return points_json(obj)
    if isinstance(obj, dict):
        return {key: jsonable_points(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable_points(value) for value in obj]
    return obj


def _plain(number: Decimal) -> str:
    """Zapis z kropką, bez zbędnych zer: ``5``, ``4.25``, ``3.5``, ``-1.5``."""
    number = number.quantize(POINTS_QUANTUM, rounding=ROUND_HALF_UP)
    if number == number.to_integral_value():
        return str(int(number))
    return format(number.normalize(), "f")


def decimal_separator(language: str | None = None) -> str:
    """Separator dziesiętny języka interfejsu: przecinek po polsku, kropka po angielsku."""
    code = (language or get_language() or "pl").split("-")[0].lower()
    return "." if code == "en" else ","


def format_points(value, language: str | None = None) -> str:
    """Punkty do pokazania człowiekowi: „5”, „4,25”, „3,5” (po angielsku „4.25”). ``None`` → „”.

    Jedyna funkcja wyświetlająca punkty – woła ją filtr szablonów ``points``, PDF dyplomu i karty
    uczestnika. Dzięki temu ta sama ocena nie może wyglądać inaczej na ekranie i na wydruku.
    """
    number = to_points(value)
    if number is None:
        return ""
    text = _plain(number)
    separator = decimal_separator(language)
    return text if separator == "." else text.replace(".", separator)


def points_csv(value) -> str:
    """Punkty do CSV: zawsze kropka dziesiętna, bez zbędnych zer (``5``, ``4.25``). ``None`` → „”."""
    number = to_points(value)
    return "" if number is None else _plain(number)


def input_value(value) -> str:
    """Wartość atrybutu ``value`` pola ``<input type="number">``: kropka niezależnie od języka.

    Przeglądarka czyta ``value`` pola liczbowego wyłącznie z kropką – „4,25” w atrybucie daje
    puste pole. To inna reguła niż wyświetlanie tekstu (``format_points``), więc osobna funkcja.
    """
    return points_csv(value)
