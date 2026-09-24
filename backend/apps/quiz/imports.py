"""Wczytanie pytań z pliku – Markdown albo CSV. Parser czysty, bez ORM-a i bez zapisu.

Po co to w ogóle jest: pytania do testu powstają w Wordzie albo w arkuszu, na długo przed tym,
zanim ktokolwiek zaloguje się do panelu. Przepisywanie czterdziestu pytań po jednym przez
formularz z czterema wariantami każde jest godziną pracy i czterdziestoma okazjami do pomyłki
w zaznaczeniu „poprawny”. Import przenosi tę robotę tam, gdzie treść i tak powstaje.

Parser zwraca **struktury**, a nie obiekty bazy, i niczego nie zapisuje. Zapis (razem z walidacją
klucza odpowiedzi, którą i tak wykonuje ``services.save_question``) jest osobnym krokiem po
stronie widoku, dzięki czemu ekran importu może pokazać podgląd „co się wczyta” **przed**
dotknięciem bazy – a przy pliku z błędem w wierszu 27 nie zostawia po sobie 26 pytań.

Obsługiwane formaty (oba opisane na ekranie importu, ``templates/web/coordinator/quiz_import.html``):

**Markdown.** Blok zaczyna się wierszem ``## …``; wszystko do następnego ``##`` należy do pytania::

    ## [pula: kinematyka] [pkt: 2] [ujemne: 0.5]
    Ciało spada swobodnie z wysokości 20 m. Ile trwa spadek?
    - [ ] 1 s
    - [x] 2 s
    - [ ] 4 s

    ## [typ: liczba] [pkt: 3]
    Podaj przyspieszenie ziemskie w m/s².
    = 9.81 [tol: 0.02] [jednostka: m/s²]

    ## [typ: tekst]
    Jak nazywa się zjawisko opisane wyżej?
    = splątanie | splątanie kwantowe | entanglement

Rodzaj pytania **wynika z treści** i nie trzeba go podawać: wiersze ``- [x]``/``- [ ]`` to wybór
(jeden zaznaczony wariant → jednokrotny, więcej → wielokrotny), wiersz ``=`` to odpowiedź krótka,
a ``[typ: liczba]`` przełącza ją na liczbową. Domyślanie się rodzaju jest tu warte swojej ceny:
w pliku pisanym ręcznie każde pole, które **można** pominąć, jest polem, w którym nie da się
zrobić literówki.

**CSV** (średnik albo przecinek, pierwszy wiersz to nagłówek, kodowanie UTF-8)::

    rodzaj;pula;punkty;ujemne;tresc;odpowiedzi
    wybor;kinematyka;2;0.5;Ile trwa spadek?;1 s|*2 s|4 s
    liczba;;3;0;Przyspieszenie ziemskie?;9.81|tol:0.02|jednostka:m/s²
    tekst;;1;0;Nazwa zjawiska?;splątanie|entanglement

Gwiazdka przed wariantem znaczy „poprawny”. Kolumna ``odpowiedzi`` jest rozdzielana pionową
kreską, bo przecinek i średnik są już zajęte przez sam CSV, a treść wariantu bywa zdaniem.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal

from apps.core.points import PointsError, parse_points

from .grading import MULTIPLE_CHOICE, NUMERIC, SHORT_TEXT, SINGLE_CHOICE, parse_number

#: Nazwy rodzajów pytań **po polsku** – tak, jak wpisze je człowiek w kolumnie ``rodzaj``.
#: Angielskie kody (``SINGLE_CHOICE`` itd.) też przechodzą, bo eksport z innego systemu potrafi
#: je oddać w tej postaci, a odrzucenie pliku za nazwę zrozumiałą dla maszyny byłoby uporem.
KIND_ALIASES = {
    "wybor": SINGLE_CHOICE,
    "wybór": SINGLE_CHOICE,
    "jednokrotny": SINGLE_CHOICE,
    "wielokrotny": MULTIPLE_CHOICE,
    "tekst": SHORT_TEXT,
    "liczba": NUMERIC,
    "single_choice": SINGLE_CHOICE,
    "multiple_choice": MULTIPLE_CHOICE,
    "short_text": SHORT_TEXT,
    "numeric": NUMERIC,
}

#: Atrybuty w nawiasach kwadratowych nagłówka Markdown: ``[pula: …] [pkt: 2] [ujemne: 0.5]``.
_ATTRIBUTE = re.compile(r"\[\s*([\wÀ-ſ]+)\s*:\s*([^\]]*)\]")
#: Wiersz wariantu: ``- [x] treść`` albo ``* [ ] treść``.
_OPTION = re.compile(r"^[-*]\s*\[\s*([xX ]?)\s*\]\s*(.+)$")


class ImportError_(ValueError):
    """Błąd w pliku z pytaniami. Niesie numer wiersza, bo bez niego komunikat jest bezużyteczny."""


@dataclass
class ParsedQuestion:
    """Jedno pytanie wczytane z pliku – w kształcie, jakiego oczekuje ``services.save_question``."""

    kind: str
    text: str
    pool: str = ""
    points: Decimal = Decimal("1")
    negative_points: Decimal = Decimal("0")
    options: list[dict] = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    #: Wiersz pliku, w którym pytanie się zaczyna – do komunikatów o błędach i do podglądu.
    line: int = 0


def parse(text: str, *, fmt: str = "markdown") -> list[ParsedQuestion]:
    """Pytania z treści pliku. ``fmt`` to ``"markdown"`` albo ``"csv"``."""
    if fmt == "csv":
        return parse_csv(text)
    return parse_markdown(text)


def _attributes(line: str) -> tuple[dict[str, str], str]:
    """Atrybuty ``[klucz: wartość]`` z nagłówka i reszta wiersza (zwykle pusta albo treść pytania)."""
    found = {match.group(1).lower(): match.group(2).strip() for match in _ATTRIBUTE.finditer(line)}
    remainder = _ATTRIBUTE.sub("", line).strip()
    return found, remainder


def _points(raw: str | None, *, default: Decimal, line: int, label: str) -> Decimal:
    """Punkty pytania z pliku – tym samym czytnikiem, co pole edytora (``apps.core.points.parse_points``).

    Przecinek albo kropka („0,5”, „0.5”), najwyżej dwa miejsca po przecinku. Do ułamków w teście
    (po wydaniu 0.35.0) stał tu czytnik odpowiedzi liczbowych, który przyjmował też „0,125”
    – a trzecią cyfrę po cichu zaokrąglała dopiero kolumna ``numeric(6, 2)`` przy zapisie.
    """
    if raw in (None, ""):
        return default
    try:
        return parse_points(raw)
    except PointsError as exc:
        raise ImportError_(f"Wiersz {line}: „{raw}” nie jest liczbą punktów ({label}): {exc}") from exc


def parse_markdown(text: str) -> list[ParsedQuestion]:
    """Bloki ``## …`` na listę pytań. Wiersze puste i komentarze poza blokiem są pomijane."""
    questions: list[ParsedQuestion] = []
    current: ParsedQuestion | None = None
    body: list[str] = []
    answers: list[str] = []
    answer_attributes: dict[str, str] = {}
    # Czy w bloku pojawiły się wiersze w składni wariantu (``- [ ]`` / ``- [x]``). Sama ta składnia
    # przesądza, że pytanie jest pytaniem **wyboru** – także wtedy, gdy autor zapomniał zaznaczyć
    # poprawny wariant. Bez tej flagi taki blok wyglądałby jak lista uznawanych odpowiedzi
    # tekstowych i wczytałby się po cichu jako zupełnie inne pytanie, zamiast zgłosić brak klucza.
    saw_options = False

    def close() -> None:
        """Domknięcie bieżącego bloku – w jednym miejscu, bo domyka go i nowy nagłówek, i koniec pliku."""
        nonlocal current, body, answers, answer_attributes, saw_options
        if current is None:
            return
        statement = "\n".join(line for line in body).strip()
        if not statement:
            raise ImportError_(f"Wiersz {current.line}: pytanie bez treści.")
        current.text = statement
        _finish(current, answers, answer_attributes, from_options=saw_options)
        questions.append(current)
        current, body, answers, answer_attributes = None, [], [], {}
        saw_options = False

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if line.startswith("##"):
            close()
            attributes, remainder = _attributes(line[2:])
            current = ParsedQuestion(
                kind=KIND_ALIASES.get((attributes.get("typ") or "").lower(), ""),
                text="",
                pool=attributes.get("pula", ""),
                points=_points(attributes.get("pkt"), default=Decimal("1"), line=number, label="pkt"),
                negative_points=_points(
                    attributes.get("ujemne"), default=Decimal("0"), line=number, label="ujemne"
                ),
                line=number,
            )
            # Nagłówek z treścią w tym samym wierszu („## Ile trwa spadek?”) jest wygodny i działa;
            # treść pod nagłówkiem też. Obie drogi schodzą się tutaj.
            if remainder:
                body.append(remainder)
            continue
        if current is None:
            if line.strip():
                raise ImportError_(f"Wiersz {number}: treść przed pierwszym nagłówkiem „##”.")
            continue
        option = _OPTION.match(line.strip())
        if option is not None:
            saw_options = True
            answers.append(("*" if option.group(1).lower() == "x" else "") + option.group(2).strip())
            continue
        if line.strip().startswith("="):
            attributes, remainder = _attributes(line.strip()[1:])
            answer_attributes.update(attributes)
            # Wiersz „=” jest kluczem odpowiedzi otwartej. Rodzaju nie rozstrzygamy tutaj, tylko
            # w ``_finish``: ``[typ: liczba]`` mogło stać w nagłówku, a mogło i w tym wierszu.
            answers.extend(part.strip() for part in remainder.split("|") if part.strip())
            if not current.kind:
                current.kind = KIND_ALIASES.get((attributes.get("typ") or "").lower(), "")
            continue
        body.append(line)

    close()
    if not questions:
        raise ImportError_("Plik nie zawiera ani jednego pytania (bloku zaczynającego się od „##”).")
    return questions


def _finish(
    question: ParsedQuestion,
    answers: list[str],
    attributes: dict[str, str],
    *,
    from_options: bool = False,
) -> None:
    """Domyślenie rodzaju i złożenie klucza odpowiedzi – wspólne dla obu formatów.

    Rodzaj podany wprost wygrywa z domyślaniem; brak rodzaju rozstrzyga kształt odpowiedzi.
    ``from_options`` mówi, że odpowiedzi przyszły w składni wariantów (``- [ ]`` w Markdownie),
    więc pytanie jest pytaniem wyboru **niezależnie** od tego, czy autor zaznaczył poprawny –
    i brak zaznaczenia jest wtedy błędem pliku, a nie powodem, żeby wziąć te wiersze za listę
    uznawanych odpowiedzi tekstowych.
    """
    marked = [item for item in answers if item.startswith("*")]
    if question.kind in (SINGLE_CHOICE, MULTIPLE_CHOICE) or (not question.kind and (marked or from_options)):
        if not marked:
            raise ImportError_(
                f"Wiersz {question.line}: pytanie wyboru bez zaznaczonego poprawnego wariantu "
                "(użyj „- [x] …” albo gwiazdki przed treścią wariantu)."
            )
        question.options = [
            {"text": item.lstrip("*").strip(), "is_correct": item.startswith("*")} for item in answers
        ]
        if len(question.options) < 2:
            raise ImportError_(f"Wiersz {question.line}: pytanie wyboru wymaga co najmniej dwóch wariantów.")
        if not question.kind:
            question.kind = SINGLE_CHOICE if len(marked) == 1 else MULTIPLE_CHOICE
        if question.kind == MULTIPLE_CHOICE:
            question.settings = {"partial_credit": attributes.get("ocena", "ALL_OR_NOTHING").upper()}
        return

    if not answers:
        raise ImportError_(f"Wiersz {question.line}: pytanie bez odpowiedzi.")
    if question.kind == NUMERIC or (not question.kind and _looks_numeric(answers, attributes)):
        question.kind = NUMERIC
        question.settings = {
            "answer": answers[0],
            "tolerance_abs": attributes.get("tol", attributes.get("tolerancja", "0")),
            "tolerance_rel": attributes.get("tol_wzgl", "0"),
            "unit": attributes.get("jednostka", ""),
        }
        return
    question.kind = SHORT_TEXT
    question.settings = {"accepted": answers}


def _looks_numeric(answers: list[str], attributes: dict[str, str]) -> bool:
    """Czy klucz odpowiedzi wygląda na liczbowy: jedna odpowiedź i daje się przeczytać jako liczba.

    Obecność tolerancji przesądza sprawę, bo tolerancji nie podaje się do tekstu. Odpowiedzi
    wielowariantowej („splątanie | entanglement”) nie uznajemy za liczbę nawet wtedy, gdy obie
    dałyby się przeczytać jako liczby: wtedy chodziło o listę uznawanych zapisów, a nie o jedną
    wartość z marginesem.
    """
    if "tol" in attributes or "tolerancja" in attributes or "jednostka" in attributes:
        return True
    return len(answers) == 1 and parse_number(answers[0]) is not None


#: Nagłówki kolumn CSV w kolejności, w jakiej opisuje je ekran importu. ``pula``, ``ujemne``
#: i ``rodzaj`` wolno pominąć – pozostałe są wymagane.
CSV_COLUMNS = ("rodzaj", "pula", "punkty", "ujemne", "tresc", "odpowiedzi")


def parse_csv(text: str) -> list[ParsedQuestion]:
    """Wiersze CSV na listę pytań. Separator (``;`` albo ``,``) wykrywany z pierwszego wiersza."""
    sample = text.splitlines()[0] if text.strip() else ""
    delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise ImportError_("Plik CSV jest pusty.")
    columns = {(name or "").strip().lower(): name for name in reader.fieldnames}
    for required in ("tresc", "odpowiedzi"):
        if required not in columns:
            raise ImportError_(f"Brakuje kolumny „{required}”. Wymagane kolumny: {', '.join(CSV_COLUMNS)}.")

    questions: list[ParsedQuestion] = []
    for number, row in enumerate(reader, start=2):
        statement = (row.get(columns["tresc"]) or "").strip()
        if not statement:
            continue
        raw_kind = (row.get(columns.get("rodzaj", "")) or "").strip().lower()
        kind = KIND_ALIASES.get(raw_kind, "")
        if raw_kind and not kind:
            raise ImportError_(f"Wiersz {number}: nieznany rodzaj pytania „{raw_kind}”.")
        question = ParsedQuestion(
            kind=kind,
            text=statement,
            pool=(row.get(columns.get("pula", "")) or "").strip(),
            points=_points(
                (row.get(columns.get("punkty", "")) or "").strip() or None,
                default=Decimal("1"),
                line=number,
                label="punkty",
            ),
            negative_points=_points(
                (row.get(columns.get("ujemne", "")) or "").strip() or None,
                default=Decimal("0"),
                line=number,
                label="ujemne",
            ),
            line=number,
        )
        parts = [part.strip() for part in (row.get(columns["odpowiedzi"]) or "").split("|") if part.strip()]
        attributes = {}
        answers = []
        for part in parts:
            # „tol:0.02” i „jednostka:m/s²” są atrybutami klucza, a nie kolejnymi odpowiedziami.
            key, separator, value = part.partition(":")
            if separator and key.strip().lower() in ("tol", "tolerancja", "tol_wzgl", "jednostka", "ocena"):
                attributes[key.strip().lower()] = value.strip()
            else:
                answers.append(part)
        if not answers:
            raise ImportError_(f"Wiersz {number}: brak odpowiedzi w kolumnie „odpowiedzi”.")
        _finish(question, answers, attributes)
        questions.append(question)

    if not questions:
        raise ImportError_("Plik CSV nie zawiera ani jednego pytania.")
    return questions
