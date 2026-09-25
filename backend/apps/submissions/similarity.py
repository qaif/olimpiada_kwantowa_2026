"""Wykrywanie podobieństw między rozwiązaniami oddanymi jako kod (``py``, ``ipynb``).

Po co osobny mechanizm dla kodu. Przy dowodzie w PDF-ie plagiat widać gołym okiem – recenzent
czyta dwa razy to samo zdanie i wie. Przy programie jest odwrotnie: wystarczy zmienić nazwy
zmiennych, przestawić kolejność funkcji i dopisać komentarze, żeby dwa pliki przestały być
podobne dla człowieka, a pozostały tym samym rozwiązaniem co do struktury. Porównanie maszynowe
robi przy tym rzecz, której żaden recenzent nie zrobi: zestawia **każdą parę** prac w zadaniu,
także prace dwóch osób, których nigdy nie czytał ten sam człowiek (ocenianie jest ślepe
i rozproszone, więc bez tego ekranu nikt nie ma widoku na całe zadanie naraz).

Czego wynik **nie** znaczy. Wysokie podobieństwo jest przesłanką, nie dowodem: przy zadaniu
z narzuconym szkieletem albo z jednym oczywistym algorytmem dwie uczciwe prace potrafią wyjść
na 0,9. Dlatego moduł nie ma pojęcia „plagiat”, niczego nie dyskwalifikuje i nie powiadamia
uczestnika. Kończy się na zapisaniu liczby i udostępnieniu porównania – decyzja należy do
komitetu i wraca do systemu osobną drogą (dyskwalifikacja albo korekta oceny z uzasadnieniem).

Jak liczymy podobieństwo:

1. **Normalizacja.** Z pliku zostaje ciąg tokenów: komentarze znikają, literały tekstowe
   i liczbowe sprowadzają się do ``STR``/``NUM``, a każdy identyfikator spoza słów kluczowych
   języka staje się ``ID``. To jest cały sens ćwiczenia – porównujemy **kształt rozwiązania**,
   a nie nazwy, które łatwo podmienić. Notatnik przed normalizacją sklejamy z samych komórek
   kodu (komórki tekstowe i wyniki wykonania nie są rozwiązaniem),
2. **Dwie miary i maksimum z nich.** Jaccard na k-gramach tokenów widzi podobieństwo także po
   przestawieniu bloków (kolejność nie ma znaczenia), a ``difflib`` – dopisanie albo usunięcie
   fragmentu przy zachowanej kolejności. Każda z nich ma ślepą plamkę dokładnie tam, gdzie druga
   widzi dobrze, więc bierzemy większą: szukamy przesłanek, a nie oszczędzamy na fałszywych
   alarmach (te odsiewa człowiek, patrząc na porównanie obok siebie).

Pamięć i czas są ograniczone z założenia. Plik każdego rozwiązania czytamy **raz**, strumieniem,
z górnym limitem bajtów, i natychmiast zamieniamy na tokeny – w pamięci nigdy nie ma kompletu
plików zadania, tylko ich odciski. Liczba par jest ograniczona (``MAX_PAIRS_PER_PROBLEM``);
po przekroczeniu limitu przeliczenie przerywa się dla tego zadania i **mówi o tym wprost**,
zamiast po cichu oddać niekompletną tabelę.
"""

from __future__ import annotations

import difflib
import json
import keyword
import logging
import re
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser

from django.db import transaction
from django.utils import timezone

from apps.competitions.models import Problem, Stage
from apps.core.models import audit

from .models import (
    SIMILARITY_STORE_THRESHOLD,
    AvStatus,
    Submission,
    SubmissionSimilarity,
    SubmissionStatus,
)
from .storage import get_submission_storage

logger = logging.getLogger(__name__)

#: Formaty, dla których porównanie w ogóle ma sens. PDF i zdjęcie kartki odpadają: normalizacja
#: do tokenów nie ma tam czego znormalizować, a porównywanie bajtów PDF-a mówiłoby wyłącznie
#: o tym, w jakim edytorze powstał.
COMPARABLE_FORMATS = frozenset({"ipynb", "py"})

#: Ile bajtów pliku czytamy. Rozwiązanie zadania olimpijskiego to kilkadziesiąt kilobajtów kodu;
#: plik większy niż to jest prawie zawsze notatnikiem z wklejonymi danymi, a ogon takiego pliku
#: nie zmienia podobieństwa **kodu**. Limit jest jednocześnie bezpiecznikiem pamięci.
MAX_SOURCE_BYTES = 2 * 1024 * 1024

#: Górny limit tokenów branych do porównania. ``difflib`` ma złożoność iloczynową, więc bez
#: obcięcia jedna para wyjątkowo długich prac potrafiłaby zająć worker na minuty.
MAX_TOKENS = 4000

#: Długość k-gramu przy mierze Jaccarda. Pięć tokenów to mniej więcej jedno wyrażenie
#: (``ID = ID ( ID )``): krótsze k-gramy powtarzają się w każdym programie i zawyżają podobieństwo,
#: dłuższe przestają rozpoznawać kod przepisany z drobnymi zmianami.
SHINGLE_SIZE = 5

#: Minimalna liczba tokenów, przy której wynik cokolwiek znaczy. Dwa dwulinijkowe rozwiązania
#: „importuj i wypisz” są identyczne z natury zadania, a nie z przepisania.
MIN_TOKENS = 30

#: Limit par na jedno zadanie. Przy komplecie prac finału liczba par rośnie kwadratowo, a ekran
#: i tak jest narzędziem do obejrzenia kilkunastu najwyższych wyników, nie do audytu wszystkiego.
MAX_PAIRS_PER_PROBLEM = 5000

#: Domyślny próg **pokazywania** pary na ekranie. Wyższy niż próg zapisu: w bazie chcemy mieć
#: zapas na obniżenie progu bez przeliczania etapu, a na ekranie – listę do obejrzenia.
DEFAULT_REPORT_THRESHOLD = 0.8

#: Komentarze, literały i liczby w postaci, którą trzeba usunąć **przed** tokenizacją.
#: Kolejność alternatyw ma znaczenie: najpierw napisy potrójne, potem zwykłe, na końcu komentarz –
#: inaczej ``#`` w środku napisu ucinałby resztę linii.
_STRIP_RE = re.compile(
    r"'''.*?'''|\"\"\".*?\"\"\"|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|#[^\n]*",
    re.DOTALL,
)
#: Token: identyfikator/słowo kluczowe, liczba albo pojedynczy znak operatora. Wszystko inne
#: (białe znaki) wypada samo, bo nie pasuje do żadnej alternatywy.
_TOKEN_RE = re.compile(r"[A-Za-z_]\w*|\d+\.?\d*|[^\sA-Za-z_\d]")

#: Słowa, które zostają sobą. Słowa kluczowe języka są szkieletem rozwiązania i podmiana ich nazw
#: nie jest możliwa; wszystko inne staje się ``ID``.
_KEYWORDS = frozenset(keyword.kwlist) | frozenset(keyword.softkwlist)

_PLACEHOLDER_STRING = "STR"
_PLACEHOLDER_NUMBER = "NUM"
_PLACEHOLDER_IDENT = "ID"


@dataclass(frozen=True)
class Fingerprint:
    """Odcisk jednego rozwiązania: tokeny i ich k-gramy. Pliku już tu nie ma."""

    submission_id: int
    tokens: tuple[str, ...]
    shingles: frozenset[tuple[str, ...]]

    @property
    def is_usable(self) -> bool:
        """Czy odcisk nadaje się do porównania – patrz ``MIN_TOKENS``."""
        return len(self.tokens) >= MIN_TOKENS


def notebook_source(raw: bytes) -> str:
    """Kod z notatnika: sklejone komórki ``code``, bez komórek tekstowych i bez wyników.

    Obsługujemy oba układy, które spotykamy w oddanych plikach: nbformat 4 (``cells``) i nbformat 3
    (``worksheets[].cells[]`` z kodem w ``input``). Notatnik nieczytelny jako JSON nie jest tu
    błędem – uczestnik mógł oddać plik uszkodzony, a przeliczenie podobieństw nie jest miejscem,
    w którym się to rozstrzyga. Zwracamy wtedy pusty tekst i praca po prostu nie ma odcisku.
    """
    try:
        document = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError, UnicodeDecodeError:
        return ""
    if not isinstance(document, dict):
        return ""
    cells = document.get("cells")
    if not isinstance(cells, list):
        cells = [
            cell
            for sheet in document.get("worksheets") or []
            if isinstance(sheet, dict)
            for cell in sheet.get("cells") or []
        ]
    parts: list[str] = []
    for cell in cells:
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        source = cell.get("source", cell.get("input", ""))
        if isinstance(source, list):
            source = "".join(item for item in source if isinstance(item, str))
        if isinstance(source, str):
            parts.append(source)
    return "\n".join(parts)


def _strip_literals(text: str) -> str:
    """Zamienia komentarze na nic, a literały tekstowe na jeden znacznik.

    Napis zostaje **jednym tokenem**, a nie znika: program, który wypisuje trzy komunikaty, ma
    inny kształt niż taki, który nie wypisuje żadnego, a treść komunikatu jest akurat tym, co
    przepisujący zmienia najchętniej.
    """

    def replace(match: re.Match) -> str:
        return "" if match.group(0).startswith("#") else f" {_PLACEHOLDER_STRING} "

    return _STRIP_RE.sub(replace, text)


def tokenize(text: str) -> tuple[str, ...]:
    """Ciąg tokenów porównawczych: słowa kluczowe dosłownie, reszta jako ``ID``/``NUM``/``STR``.

    To jest jedyne miejsce, w którym decyduje się, co znaczy „takie samo rozwiązanie”. Sprowadzenie
    identyfikatorów do jednego symbolu jest świadome i kosztowne: dwa rozwiązania różniące się
    wyłącznie nazwami zmiennych wyjdą identyczne. O to chodzi – właśnie tak wygląda przepisana
    praca, a rozróżnianie ich po nazwach zamieniłoby ekran w wykrywacz kopiowania przez schowek.
    """
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(_strip_literals(text)):
        if raw == _PLACEHOLDER_STRING:
            tokens.append(_PLACEHOLDER_STRING)
        elif raw[0].isdigit():
            tokens.append(_PLACEHOLDER_NUMBER)
        elif raw[0].isalpha() or raw[0] == "_":
            tokens.append(raw if raw in _KEYWORDS else _PLACEHOLDER_IDENT)
        else:
            tokens.append(raw)
        if len(tokens) >= MAX_TOKENS:
            break
    return tuple(tokens)


def _shingles(tokens: tuple[str, ...]) -> frozenset[tuple[str, ...]]:
    """Zbiór k-gramów. Zbiór, nie lista: Jaccard pyta o **obecność** fragmentu, nie o liczbę."""
    if len(tokens) < SHINGLE_SIZE:
        return frozenset()
    return frozenset(tokens[index : index + SHINGLE_SIZE] for index in range(len(tokens) - SHINGLE_SIZE + 1))


def _jaccard(left: frozenset, right: frozenset) -> float:
    """Udział wspólnych k-gramów. Pusty zbiór po którejkolwiek stronie to brak podobieństwa."""
    if not left or not right:
        return 0.0
    common = len(left & right)
    if not common:
        return 0.0
    return common / len(left | right)


def score_pair(left: Fingerprint, right: Fingerprint) -> float:
    """Podobieństwo dwóch odcisków: większa z dwóch miar (patrz nagłówek modułu)."""
    ratio = difflib.SequenceMatcher(None, left.tokens, right.tokens, autojunk=False).ratio()
    return max(_jaccard(left.shingles, right.shingles), ratio)


def _read_source(submission: Submission) -> str:
    """Treść rozwiązania jako tekst – strumieniem ze storage, z twardym limitem bajtów.

    Plik otwieramy i zamykamy w obrębie jednego wywołania, więc w pamięci nigdy nie ma więcej
    niż jednego rozwiązania naraz. Brak pliku, plik niedoczyszczony przez antywirusa albo błąd
    storage dają pusty tekst: przeliczenie podobieństw ma pominąć taką pracę, a nie wywrócić
    całe zadanie z powodu jednego brakującego obiektu.
    """
    submission_file = submission.latest_file
    if submission_file is None or submission_file.av_status != AvStatus.CLEAN:
        return ""
    storage = get_submission_storage()
    try:
        stream = storage.open(submission_file.object_key)
    except Exception:  # noqa: BLE001 - dowolny błąd storage znaczy „tej pracy nie porównamy”
        logger.warning("Nie udało się otworzyć pliku pracy %s do porównania.", submission.pk)
        return ""
    try:
        raw = stream.read(MAX_SOURCE_BYTES)
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    if submission_file.object_key.lower().endswith(".ipynb"):
        return notebook_source(raw)
    return raw.decode("utf-8", errors="replace")


def fingerprint_from_text(submission_id: int, text: str) -> Fingerprint:
    """Odcisk z gotowego tekstu. Wydzielone od ``fingerprint``, bo to dwie różne odpowiedzialności:

    tutaj jest **cała miara podobieństwa** (normalizacja i k-gramy), a tam – dostęp do storage.
    Rozdział pozwala też sprawdzić samą miarę bez bazy i bez plików, czyli w teście, który mówi
    wyłącznie o tym, co uznajemy za „tę samą pracę”.
    """
    tokens = tokenize(text)
    return Fingerprint(submission_id=submission_id, tokens=tokens, shingles=_shingles(tokens))


def fingerprint(submission: Submission) -> Fingerprint:
    """Odcisk jednej pracy. Plik jest czytany tu i tylko tu – dalej pracujemy na tokenach."""
    return fingerprint_from_text(submission.pk, _read_source(submission))


def comparable_problems(stage: Stage) -> list[Problem]:
    """Zadania etapu, w których rozwiązanie bywa kodem – reszta nie ma czego porównywać."""
    return [
        problem
        for problem in stage.problems.order_by("number", "id")
        if COMPARABLE_FORMATS & set(problem.allowed_formats or [])
    ]


def latest_clean_submissions(problem: Problem) -> list[Submission]:
    """Najnowsza, przeskanowana wersja pracy każdego uczestnika w tym zadaniu.

    Jedna praca na wpis do etapu: porównywanie wersji tego samego uczestnika ze sobą dałoby
    same wysokie wyniki i zasypałoby tabelę. Wersje odrzucone przez antywirusa odpadają –
    nigdy nie weszły do oceniania.
    """
    rows = (
        Submission.objects.filter(problem=problem)
        .exclude(status=SubmissionStatus.REJECTED_INFECTED)
        .select_related("entry", "entry__participant")
        .prefetch_related("files")
        .order_by("entry_id", "-version", "-id")
    )
    latest: dict[int, Submission] = {}
    for submission in rows:
        latest.setdefault(submission.entry_id, submission)
    return sorted(latest.values(), key=lambda item: item.pk)


@dataclass(frozen=True)
class ProblemSummary:
    """Wynik przeliczenia jednego zadania – także wtedy, gdy limit par przerwał pracę."""

    problem_id: int
    compared: int
    stored: int
    truncated: bool


def _store_pairs(problem: Problem, pairs: list[tuple[int, int, float]]) -> int:
    """Zapisuje wyniki jednego zadania, zastępując poprzednie przeliczenie.

    Kasujemy i wstawiamy od nowa, zamiast aktualizować: para, która przy poprzednim przebiegu
    przekraczała próg, a teraz nie (uczestnik oddał nową wersję), musi z tabeli **zniknąć** –
    inaczej ekran pokazywałby zarzut wobec pracy, której już nie ma. Znacznik „zgłoszone do
    komitetu” przenosimy, bo to decyzja człowieka, a nie wynik obliczenia.
    """
    reported = {
        (row["submission_a_id"], row["submission_b_id"]): row["reported_at"]
        for row in SubmissionSimilarity.objects.filter(problem=problem, reported_at__isnull=False).values(
            "submission_a_id", "submission_b_id", "reported_at"
        )
    }
    SubmissionSimilarity.objects.filter(problem=problem).delete()
    now = timezone.now()
    SubmissionSimilarity.objects.bulk_create(
        [
            SubmissionSimilarity(
                stage_id=problem.stage_id,
                problem=problem,
                submission_a_id=left,
                submission_b_id=right,
                score=score,
                computed_at=now,
                reported_at=reported.get((left, right)),
            )
            for left, right, score in pairs
        ]
    )
    return len(pairs)


def recompute_problem(problem: Problem) -> ProblemSummary:
    """Przelicza podobieństwa w jednym zadaniu i zapisuje pary powyżej progu przechowywania.

    Odciski budujemy po kolei, po jednej pracy, i dopiero potem porównujemy je między sobą:
    inaczej ta sama praca byłaby ściągana ze storage tyle razy, w ilu parach występuje (czyli
    przy stu pracach – dziewięćdziesiąt dziewięć razy każda).
    """
    submissions = latest_clean_submissions(problem)
    prints = [item for item in (fingerprint(submission) for submission in submissions) if item.is_usable]
    pairs: list[tuple[int, int, float]] = []
    compared = 0
    truncated = False
    for index, left in enumerate(prints):
        for right in prints[index + 1 :]:
            if compared >= MAX_PAIRS_PER_PROBLEM:
                truncated = True
                break
            compared += 1
            score = score_pair(left, right)
            if score >= SIMILARITY_STORE_THRESHOLD:
                # Kolejność pary jest wymuszona constraintem: mniejszy identyfikator pierwszy.
                low, high = sorted((left.submission_id, right.submission_id))
                pairs.append((low, high, score))
        if truncated:
            break
    stored = _store_pairs(problem, pairs)
    return ProblemSummary(problem_id=problem.pk, compared=compared, stored=stored, truncated=truncated)


@transaction.atomic
def recompute_stage(stage: Stage, *, actor=None, request=None) -> dict:
    """Przelicza podobieństwa we wszystkich „kodowych” zadaniach etapu. Zwraca podsumowanie.

    Transakcja obejmuje cały etap, bo podmiana wyników jednego zadania jest operacją „skasuj
    i wstaw” – przerwana w połowie zostawiłaby zadanie bez ani jednego wiersza, co wygląda na
    ekranie identycznie jak „nic nie znaleziono”.

    Do audytu idą wyłącznie liczniki. Identyfikatory par są w bazie; wpis audytowy czytają też
    osoby bez prawa do wiedzy o tym, czyja praca jest do czyjej podobna.
    """
    problems = comparable_problems(stage)
    summaries = [recompute_problem(problem) for problem in problems]
    result = {
        "stage_id": stage.pk,
        "problems": len(summaries),
        "compared": sum(item.compared for item in summaries),
        "stored": sum(item.stored for item in summaries),
        "truncated": [item.problem_id for item in summaries if item.truncated],
    }
    audit(actor, "similarity.recomputed", stage, result, request=request)
    logger.info(
        "Etap %s: porównano %s par w %s zadaniach, zapisano %s wyników (limit przerwał %s zadań).",
        stage.pk,
        result["compared"],
        result["problems"],
        result["stored"],
        len(result["truncated"]),
    )
    return result


@transaction.atomic
def toggle_report(pair: SubmissionSimilarity, *, actor=None, request=None) -> bool:
    """Przełącza znacznik „zgłoszone do komitetu”. Zwraca stan **po** zmianie.

    Zgłoszenie niczego nie rozstrzyga i nikogo nie powiadamia: jest zakładką koordynatora na
    parze, którą komitet ma obejrzeć na posiedzeniu. Dlatego da się je cofnąć – para obejrzana
    i wyjaśniona wraca do zwykłej listy, a ślad obu kliknięć zostaje w audycie.
    """
    locked = SubmissionSimilarity.objects.select_for_update().get(pk=pair.pk)
    reported = locked.reported_at is None
    locked.reported_at = timezone.now() if reported else None
    locked.reported_by = actor if (reported and getattr(actor, "is_authenticated", False)) else None
    locked.save(update_fields=["reported_at", "reported_by"])
    audit(
        actor,
        "similarity.reported" if reported else "similarity.report_withdrawn",
        locked,
        {"problem_id": locked.problem_id, "score": round(locked.score, 3)},
        request=request,
    )
    return reported


#: Znaczniki, które wolno zostawić w tabeli porównania. ``difflib.HtmlDiff`` produkuje wyłącznie
#: te (plus kotwice nawigacyjne), a treść rozwiązań escapuje sam – biała lista jest tu drugą
#: barierą, nie pierwszą: kod uczestnika jest danymi z zewnątrz i nie ma prawa wnieść na stronę
#: koordynatora ani jednego znacznika, choćby biblioteka kiedyś zmieniła zachowanie.
ALLOWED_DIFF_TAGS = frozenset({"table", "thead", "tbody", "tr", "td", "th", "span", "a", "colgroup", "col"})
#: Atrybuty przepuszczane razem ze znacznikiem. ``href`` wyłącznie wewnątrzstronicowy (``#…``) –
#: kotwice „następna zmiana” są jedynymi odnośnikami, jakie ta tabela ma prawo zawierać.
ALLOWED_DIFF_ATTRS = frozenset({"class", "id", "href", "nowrap"})

#: Ile linii źródła bierzemy do porównania. Tabela dla pliku dłuższego niż to i tak nie jest
#: czytana w całości, a ``HtmlDiff`` rośnie z iloczynu długości obu stron.
MAX_DIFF_LINES = 600


class _DiffSanitiser(HTMLParser):
    """Przepuszcza wyłącznie znaczniki i atrybuty z białej listy, resztę zamieniając na tekst.

    Dlaczego własny parser, a nie wyrażenie regularne: usuwanie znaczników regexem jest klasycznym
    sposobem na przepuszczenie tego jednego, którego wzorzec nie przewidział. Parser rozstrzyga
    o każdym znaczniku z osobna, a wszystko, co odpadnie, znika bez śladu – tekst wewnątrz zostaje.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []

    def _attributes(self, attrs: list[tuple[str, str | None]]) -> str:
        kept = []
        for name, value in attrs:
            if name not in ALLOWED_DIFF_ATTRS or value is None:
                continue
            if name == "href" and not value.startswith("#"):
                continue
            kept.append(f' {name}="{escape(value, quote=True)}"')
        return "".join(kept)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ALLOWED_DIFF_TAGS:
            self.parts.append(f"<{tag}{self._attributes(attrs)}>")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag in ALLOWED_DIFF_TAGS:
            self.parts.append(f"<{tag}{self._attributes(attrs)}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in ALLOWED_DIFF_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        # ``HtmlDiff`` wstawia ``&nbsp;`` dla wcięć – encja jest bezpieczna i potrzebna,
        # bo bez niej wcięcia kodu (czyli w Pythonie: struktura) zniknęłyby z porównania.
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def result(self) -> str:
        return "".join(self.parts)


def sanitise_html(raw: str) -> str:
    """Tabela porównania po przejściu przez białą listę znaczników."""
    parser = _DiffSanitiser()
    parser.feed(raw)
    parser.close()
    return parser.result()


def submission_source(submission: Submission) -> str:
    """Treść rozwiązania do pokazania na ekranie porównania – bez normalizacji do tokenów.

    Porównanie ogląda **człowiek**, więc pokazujemy kod taki, jaki oddał uczestnik: to na nazwach
    zmiennych i komentarzach najczęściej widać, że jedna praca powstała z drugiej. Normalizacja
    (``tokenize``) służy wyłącznie liczbie, która skierowała tu koordynatora.
    """
    return _read_source(submission)


def diff_table(pair: SubmissionSimilarity, *, left_label: str, right_label: str) -> str:
    """Porównanie obok siebie jako oczyszczony fragment HTML.

    ``HtmlDiff`` sam escapuje treść porównywanych linii, a mimo to wynik przechodzi jeszcze przez
    ``sanitise_html``: między nami a kodem uczestnika mają stać dwie niezależne bariery, bo to
    jedyne miejsce w serwisie, w którym treść pliku od użytkownika trafia na stronę jako HTML,
    a nie jako tekst.

    Styl tabeli jest w arkuszu (``static/css/coordinator-tools.css``), nie w atrybutach: polityka
    bezpieczeństwa nie dopuszcza stylów inline, więc ``make_file`` (który wkleja własny ``<style>``)
    nie wchodzi w grę – używamy wyłącznie ``make_table``.
    """
    left = submission_source(pair.submission_a).splitlines()[:MAX_DIFF_LINES]
    right = submission_source(pair.submission_b).splitlines()[:MAX_DIFF_LINES]
    table = difflib.HtmlDiff(wrapcolumn=80).make_table(
        left, right, fromdesc=left_label, todesc=right_label, context=False
    )
    return sanitise_html(table)


def pairs_for_stage(
    stage: Stage, *, threshold: float = DEFAULT_REPORT_THRESHOLD
) -> list[SubmissionSimilarity]:
    """Pary etapu powyżej progu pokazywania, z doczytanymi danymi obu prac.

    ``select_related`` sięga aż do uczestnika, bo tabela stoi na kodzie publicznym przy każdej
    stronie pary – bez tego strona robiłaby cztery zapytania na wiersz.
    """
    return list(
        SubmissionSimilarity.objects.filter(stage=stage, score__gte=threshold)
        .select_related(
            "problem",
            "submission_a__entry__participant__user",
            "submission_b__entry__participant__user",
        )
        .order_by("-score", "id")
    )
