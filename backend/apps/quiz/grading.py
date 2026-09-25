"""Sprawdzanie odpowiedzi – **czyste funkcje**, bez ORM-a, bez zegara i bez żądania HTTP.

Dlaczego osobny moduł, a nie metody modeli: ta warstwa odpowiada na pytanie „ile punktów należy
się za tę odpowiedź”, i jest to jedyne pytanie w całej funkcji testów, którego zła odpowiedź jest
**nieodwracalna po ogłoszeniu wyników**. Czysta funkcja da się przetestować wyczerpująco (i jest –
``apps/quiz/tests/test_grading.py``), da się ją wywołać na danych z rozmowy z organizatorem bez
zakładania bazy i da się ją przeliczyć ponownie po poprawce klucza, nie ruszając niczego innego.

Kształt danych, którymi ten moduł się posługuje:

``QuizQuestion.settings`` (klucz odpowiedzi i reguły porównania) per rodzaj pytania:

- ``SINGLE_CHOICE`` – ``{"partial_credit": …}`` bywa obecne, ale nie ma znaczenia: jednokrotny
  wybór jest z definicji „wszystko albo nic”. Klucz to ``QuizOption.is_correct``,
- ``MULTIPLE_CHOICE`` – ``{"partial_credit": "ALL_OR_NOTHING" | "PROPORTIONAL"}``,
- ``SHORT_TEXT`` – ``{"accepted": ["…", …], "fold_case": bool, "fold_whitespace": bool,
  "fold_diacritics": bool}``,
- ``NUMERIC`` – ``{"answer": liczba, "tolerance_abs": liczba, "tolerance_rel": liczba,
  "unit": "…"}``. ``unit`` jest wyłącznie podpisem pola w interfejsie; jednostki **nie**
  sprawdzamy, bo uczestnik jej nie wpisuje.

``QuizAnswer.payload`` (odpowiedź uczestnika):

- wybór – ``{"options": [id, …]}`` (jednokrotny wybór też listą, zawsze najwyżej jednoelementową:
  jeden kształt dla obu rodzajów oszczędza rozgałęzienia w autozapisie i w szablonie),
- ``SHORT_TEXT`` – ``{"text": "…"}``,
- ``NUMERIC`` – ``{"value": "…"}``. Napis, a nie liczba: to jest surowa treść pola formularza
  i dopiero ten moduł rozstrzyga, czy da się ją przeczytać jako liczbę. Zapisanie liczby
  wcześniej znaczyłoby, że „3,14x” ginie w drodze i uczestnik nie zobaczyłby własnej odpowiedzi.

Odpowiedź pusta (brak wiersza, pusty ``payload``, sam biały znak) jest **brakiem odpowiedzi**,
a nie odpowiedzią błędną: nie dostaje punktów i **nie** dostaje punktów ujemnych. Inaczej punkty
ujemne karałyby za pominięcie pytania, czyli dokładnie za to zachowanie, do którego mają
zachęcać, kiedy uczestnik nie zna odpowiedzi.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from apps.core.points import round_points
from apps.core.text import fold

#: Wartości ``settings["partial_credit"]``. Powtórzone tu jako napisy, a nie wzięte z ``models``:
#: ten moduł ma nie importować Django, żeby dało się go wołać (i testować) bez bazy.
ALL_OR_NOTHING = "ALL_OR_NOTHING"
PROPORTIONAL = "PROPORTIONAL"

#: Wartości ``Quiz.negative_floor`` – z tego samego powodu co wyżej.
FLOOR_QUESTION = "QUESTION"
FLOOR_QUIZ = "QUIZ"

#: Wartości ``QuizQuestion.kind``. Napisy muszą zgadzać się co do znaku z ``models.QuestionKind``
#: i pilnuje tego test (``test_grading.py``), bo jedna literówka znaczyłaby tu „nieznany rodzaj
#: pytania” w środku przeliczania wyników.
SINGLE_CHOICE = "SINGLE_CHOICE"
MULTIPLE_CHOICE = "MULTIPLE_CHOICE"
SHORT_TEXT = "SHORT_TEXT"
NUMERIC = "NUMERIC"

#: Domyślne flagi normalizacji krótkiej odpowiedzi tekstowej.
#:
#: Wszystkie trzy są domyślnie **włączone** i to jest świadomy wybór na korzyść uczestnika.
#: Pytanie „jak nazywa się zjawisko” sprawdza wiedzę, a nie to, czy ktoś napisał „Splątanie”
#: wielką literą i czy jego klawiatura miała polskie znaki (szkolna pracownia z układem US
#: bywa bez nich). Koordynator, dla którego wielkość liter jest istotna – np. symbol pierwiastka,
#: gdzie „Co” to kobalt, a „CO” tlenek węgla – wyłącza ``fold_case`` przy tym jednym pytaniu.
DEFAULT_TEXT_FLAGS = {"fold_case": True, "fold_whitespace": True, "fold_diacritics": True}

#: Znaki, które w polu liczbowym znaczą „separator tysięcy” i nie zmieniają wartości. Spacja
#: nierozdzielająca jest tu, bo tak liczby formatuje arkusz kalkulacyjny, z którego uczestnik
#: potrafi wartość skopiować.
_THOUSANDS_SEPARATORS = (" ", " ", " ", "_")

ZERO = Decimal("0")


@dataclass(frozen=True)
class QuestionScore:
    """Wynik sprawdzenia jednej odpowiedzi.

    ``is_correct`` jest trójstanowe i każdy stan znaczy co innego: ``True`` – w pełni poprawna,
    ``False`` – udzielona i błędna (to jest jedyny stan, w którym odejmują się punkty ujemne),
    ``None`` – brak odpowiedzi. Rozdzielenie „błędnej” od „braku” jest potrzebne nie tylko do
    punktów ujemnych: statystyka trudności pytania (``apps.quiz.services``) inaczej traktuje
    pytanie, którego nikt nie umiał, a inaczej takie, do którego nikt nie zdążył dojść.

    ``ratio`` to część maksimum pytania uznana za trafioną (1 przy pełnej odpowiedzi). Jest
    osobno od punktów, bo statystyka porównuje pytania warte różnie.
    """

    points: Decimal
    is_correct: bool | None
    ratio: Decimal = ZERO

    @property
    def answered(self) -> bool:
        return self.is_correct is not None


def normalise_text(
    value: str,
    *,
    fold_case: bool = True,
    fold_whitespace: bool = True,
    fold_diacritics: bool = True,
) -> str:
    """Postać porównawcza krótkiej odpowiedzi tekstowej.

    Kolejność kroków nie jest dowolna. Najpierw ujednolicamy zapis Unicode (``NFKC``), bo „ą”
    przychodzi z przeglądarki raz jako jeden znak, a raz jako „a” z dokładanym ogonkiem i bez tego
    kroku byłyby to dwa różne napisy **przed** jakimkolwiek składaniem. Potem białe znaki, bo
    zwijanie spacji musi widzieć już ujednolicony tekst. Diakrytyki i wielkość liter na końcu,
    przez ``apps.core.text.fold`` – tę samą funkcję, którą składa nazwy szkół wyszukiwarka; jedna
    definicja „bez ogonków, małymi literami” w całym serwisie jest warta więcej niż lokalna kopia
    dopasowana do tego jednego pola.

    ``fold`` składa wielkość liter razem z diakrytykami, więc samo ``fold_diacritics`` bez
    ``fold_case`` wymaga osobnego przejścia – i dlatego ten warunek jest tu rozpisany jawnie,
    zamiast jednego wywołania.
    """
    text = unicodedata.normalize("NFKC", value or "")
    if fold_whitespace:
        text = " ".join(text.split())
    else:
        text = text.strip()
    if fold_diacritics and fold_case:
        return fold(text)
    if fold_diacritics:
        # Same diakrytyki, z zachowaną wielkością liter: rozkładamy i wyrzucamy znaki łączące.
        # „ł” nie rozkłada się przez NFKD (jest osobną literą), stąd jawne przepisanie obu form.
        decomposed = unicodedata.normalize("NFKD", text.replace("ł", "l").replace("Ł", "L"))
        return "".join(char for char in decomposed if not unicodedata.combining(char))
    if fold_case:
        return text.casefold()
    return text


def parse_number(value) -> Decimal | None:
    """Liczba z tego, co uczestnik wpisał – albo ``None``, gdy to nie jest liczba.

    Przyjmujemy przecinek dziesiętny, bo w polskiej szkole tak właśnie zapisuje się ułamki, a pole
    tekstowe nie ma powodu tego odrzucać. Przyjmujemy też separatory tysięcy i zapis wykładniczy
    („1,5e-3”), bo to są postacie, w których liczba przychodzi z kalkulatora i z arkusza.

    ``Decimal``, nie ``float``: wartość jest potem porównywana z tolerancją podaną przez
    koordynatora, a ``0.1 + 0.2`` we ``float`` nie jest równe ``0.3``. Przy tolerancji rzędu
    1e-9 – a takie się zdarzają w fizyce – różnica jest widoczna w wyniku.
    """
    if value is None:
        return None
    text = str(value).strip()
    for separator in _THOUSANDS_SEPARATORS:
        text = text.replace(separator, "")
    text = text.replace(",", ".")
    if not text:
        return None
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    # ``Decimal("nan")`` i ``Decimal("inf")`` konstruują się bez błędu, a potem przewracają
    # porównanie z tolerancją (NaN nie jest równe niczemu, także sobie). To nie są liczby,
    # których ktokolwiek oczekuje w odpowiedzi – odrzucamy je tak samo jak „trzy”.
    if not number.is_finite():
        return None
    return number


def selected_option_ids(payload) -> list[int]:
    """Identyfikatory wariantów z ``payload``, odporne na to, co przyjdzie z sieci.

    Dane pochodzą od uczestnika (autozapis wysyła JSON-a), więc mogą być czymkolwiek. Zamiast
    ufać kształtowi, przepuszczamy przez ten filtr wszystko, co idzie do sprawdzania: wartości
    nieliczbowe znikają, zamiast wywracać ocenianie całego podejścia. **Nie** sprawdzamy tu, czy
    wariant należy do pytania – to robi warstwa zapisu (``apps.quiz.services.save_answers``),
    która jako jedyna ma dostęp do bazy.
    """
    if not isinstance(payload, dict):
        return []
    raw = payload.get("options")
    if not isinstance(raw, (list, tuple)):
        return []
    result: list[int] = []
    for item in raw:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value not in result:
            result.append(value)
    return result


def grade_single_choice(payload, correct_ids: set[int]) -> QuestionScore:
    """Jednokrotny wybór: trafiony wariant albo nic. Dwa zaznaczenia to odpowiedź błędna.

    Dlaczego dwa zaznaczenia nie są „brakiem odpowiedzi”: interfejs daje przyciski radiowe, więc
    drugiego zaznaczenia nie da się zrobić przez pomyłkę – da się je natomiast wysłać ręcznie
    spreparowanym żądaniem. Uznanie takiej odpowiedzi za niebyłą znaczyłoby, że przy punktach
    ujemnych opłaca się zaznaczyć wszystko naraz zamiast nie odpowiadać.
    """
    selected = selected_option_ids(payload)
    if not selected:
        return QuestionScore(ZERO, None)
    if len(selected) == 1 and selected[0] in correct_ids:
        return QuestionScore(ZERO, True, Decimal("1"))
    return QuestionScore(ZERO, False)


def grade_multiple_choice(payload, correct_ids: set[int], all_ids: set[int], rule: str) -> QuestionScore:
    """Wielokrotny wybór – „wszystko albo nic” albo ocena proporcjonalna.

    Reguła proporcjonalna liczy **trafienia minus pomyłki**, podzielone przez liczbę wariantów
    poprawnych, i przycina wynik do zera od dołu. Bez odejmowania pomyłek zaznaczenie wszystkich
    wariantów dawałoby komplet punktów za brak jakiejkolwiek wiedzy – i to jest najczęstszy błąd
    w testach z oceną częściową, a nie kwestia gustu.

    Odpowiedź „wszystkie warianty zaznaczone” przy regule proporcjonalnej wychodzi więc na zero
    punktów, ale jest odpowiedzią **błędną** (a nie brakiem), więc punkty ujemne za nią odejdą –
    tak samo jak przy „wszystko albo nic”.
    """
    selected = set(selected_option_ids(payload)) & all_ids
    if not selected:
        return QuestionScore(ZERO, None)
    if selected == correct_ids:
        return QuestionScore(ZERO, True, Decimal("1"))
    if rule != PROPORTIONAL or not correct_ids:
        return QuestionScore(ZERO, False)
    hits = len(selected & correct_ids)
    misses = len(selected - correct_ids)
    ratio = (Decimal(hits - misses) / Decimal(len(correct_ids))).max(ZERO)
    return QuestionScore(ZERO, False, ratio)


def grade_short_text(payload, settings: dict) -> QuestionScore:
    """Krótka odpowiedź tekstowa: zgodność z którymkolwiek z wariantów uznawanych.

    Lista uznawanych odpowiedzi jest **listą**, a nie jednym wzorcem, bo prawie każde pytanie
    otwarte ma więcej niż jedną poprawną postać zapisu („splątanie”, „splatanie kwantowe”,
    „entanglement”). Wyrażeń regularnych tu nie ma i nie będzie: klucz odpowiedzi pisze
    koordynator, a nie programista, a błąd w wyrażeniu regularnym objawia się dopiero w wynikach.
    """
    if not isinstance(payload, dict):
        return QuestionScore(ZERO, None)
    flags = {key: bool(settings.get(key, default)) for key, default in DEFAULT_TEXT_FLAGS.items()}
    given = normalise_text(str(payload.get("text") or ""), **flags)
    if not given:
        return QuestionScore(ZERO, None)
    accepted = settings.get("accepted")
    if not isinstance(accepted, (list, tuple)):
        accepted = []
    for candidate in accepted:
        if given == normalise_text(str(candidate), **flags):
            return QuestionScore(ZERO, True, Decimal("1"))
    return QuestionScore(ZERO, False)


def grade_numeric(payload, settings: dict) -> QuestionScore:
    """Odpowiedź liczbowa z tolerancją bezwzględną i względną.

    Obie tolerancje działają **naraz i alternatywnie**: odpowiedź jest poprawna, gdy mieści się
    w którejkolwiek z nich. Tak jest, bo opisują dwie różne rzeczy. Tolerancja bezwzględna
    odpowiada na „zaokrąglij do dwóch miejsc” (0,005), względna – na „dopuszczamy 1% na stałe
    fizyczne i zaokrąglenia po drodze”. Przy odpowiedzi 0 tolerancja względna daje zero, więc bez
    tej alternatywy pytanie z odpowiedzią „0” nie miałoby żadnego marginesu.

    Tolerancja domyślna jest **zerowa**, czyli wymagana jest równość co do wpisanej cyfry. Wybór
    jest zamierzony: margines, o którym koordynator nie wie, jest gorszy od braku marginesu –
    przy zerze błąd widać od razu w wynikach pierwszego podejścia.
    """
    if not isinstance(payload, dict):
        return QuestionScore(ZERO, None)
    given = parse_number(payload.get("value"))
    if given is None:
        # Także wtedy, gdy uczestnik wpisał „mniej więcej cztery”: nie ma czego porównać, więc nie
        # ma odpowiedzi. Interfejs mówi o tym przy polu, zanim podejście się skończy.
        return QuestionScore(ZERO, None)
    expected = parse_number(settings.get("answer"))
    if expected is None:  # pragma: no cover - broni edytor testu (``validate_question_settings``)
        return QuestionScore(ZERO, False)
    tolerance_abs = parse_number(settings.get("tolerance_abs")) or ZERO
    tolerance_rel = parse_number(settings.get("tolerance_rel")) or ZERO
    distance = abs(given - expected)
    allowed = max(abs(tolerance_abs), abs(expected) * abs(tolerance_rel))
    if distance <= allowed:
        return QuestionScore(ZERO, True, Decimal("1"))
    return QuestionScore(ZERO, False)


def score_question(
    *, kind: str, settings: dict, payload, correct_ids: set[int], all_ids: set[int]
) -> QuestionScore:
    """Sprawdzenie odpowiedzi bez przyznawania punktów – sam werdykt i część trafiona.

    Rozdział na „ile trafione” i „ile punktów” jest tu po to, żeby reguła punktowa (waga pytania,
    punkty ujemne, podłoga) była **jedna** dla wszystkich rodzajów pytań i nie musiała być
    powtórzona w każdej z czterech funkcji wyżej. Punkty dokłada ``award_points``.
    """
    settings = settings if isinstance(settings, dict) else {}
    if kind == SINGLE_CHOICE:
        return grade_single_choice(payload, correct_ids)
    if kind == MULTIPLE_CHOICE:
        rule = settings.get("partial_credit") or ALL_OR_NOTHING
        return grade_multiple_choice(payload, correct_ids, all_ids, rule)
    if kind == SHORT_TEXT:
        return grade_short_text(payload, settings)
    if kind == NUMERIC:
        return grade_numeric(payload, settings)
    raise ValueError(f"Nieznany rodzaj pytania: {kind!r}.")  # pragma: no cover - błąd programisty


def award_points(
    score: QuestionScore, *, points: Decimal, negative_points: Decimal, floor: str
) -> QuestionScore:
    """Punkty za sprawdzoną odpowiedź: waga pytania, kara za błąd i podłoga „za pytanie”.

    Trzy przypadki i każdy jest osobną decyzją regulaminową:

    - brak odpowiedzi → zero. Nigdy kara: patrz nagłówek modułu,
    - odpowiedź poprawna (także częściowo) → ``ratio × points``. Kwota jest zaokrąglana do dwóch
      miejsc, bo tyle mieści kolumna w bazie, a ``1/3`` maksimum musi dać tę samą liczbę przy
      każdym przeliczeniu – inaczej „Przelicz punkty” zmieniałoby wyniki bez zmiany klucza.
      Metoda: **połówka w górę** (``ROUND_HALF_UP``, ``apps.core.points.round_points``) – ta sama,
      którą liczy się suma etapu i każde inne zaokrąglenie punktów w systemie. Do wydania 0.35.0
      stało tu domyślne ``quantize`` (bankierskie ``ROUND_HALF_EVEN``): pytanie za 0,5 pkt
      z jednym z czterech poprawnych wariantów trafionym dawało 0,12, a nie 0,13, choć regulamin
      liczy „od połowy w górę”. Różnica dotyczy wyłącznie kwot kończących się dokładnie na pół
      setnej i podejść ocenionych od nowa (``grade_attempt``/``regrade_quiz``) – punkty już
      zapisane w bazie nie zmieniają się same,
    - odpowiedź błędna → ``−negative_points``, a przy podłodze ``QUESTION`` przycięte do zera.
      Przy podłodze ``QUIZ`` wartość zostaje ujemna i przycięcie robi dopiero ``total_score``.

    Odpowiedź częściowo trafiona **nie** dostaje kary: ``ratio > 0`` znaczy, że uczestnik coś
    wiedział, a odjęcie mu za to punktów byłoby karą za wiedzę niepełną. Kara zostaje dla
    odpowiedzi, w której nie trafił nic.
    """
    if not score.answered:
        return QuestionScore(ZERO, None)
    if score.ratio > ZERO:
        awarded = round_points(points * score.ratio)
        return QuestionScore(awarded, score.is_correct, score.ratio)
    penalty = -abs(negative_points)
    if floor == FLOOR_QUESTION:
        penalty = max(penalty, ZERO)
    return QuestionScore(round_points(penalty), False, ZERO)


def total_score(points: list[Decimal], *, floor: str) -> Decimal:
    """Suma punktów podejścia z podłogą „za cały test”.

    Wydzielone z sumowania w serwisie, bo to jest **druga połowa** reguły ``negative_floor``
    i musi stać obok pierwszej (``award_points``). Rozdzielone między dwa moduły dałyby się
    rozjechać: ktoś dołożyłby trzeci tryb podłogi w jednym z nich i test przestałby ją stosować
    dokładnie w połowie przypadków.

    Składniki mają już po dwa miejsca po przecinku (``award_points``), więc ``round_points`` na
    końcu niczego nie zaokrągla – sprowadza tylko postać do ``0.01`` (``ROUND_HALF_UP``, jak wyżej).
    """
    total = sum(points, ZERO)
    if floor == FLOOR_QUIZ:
        total = max(total, ZERO)
    return round_points(Decimal(total))


def validate_question_settings(kind: str, settings: dict) -> dict:
    """Ustawienia sprawdzania obcięte do tego, co dany rodzaj pytania rozumie – albo wyjątek.

    Funkcja jest **bramą edytora**: to jedyne miejsce, przez które ``settings`` trafia do bazy,
    i dlatego zwraca nowy słownik zamiast poprawiać ten podany. Pytanie, które zmieniło rodzaj
    z liczbowego na tekstowe, nie może zachować klucza „answer” – zostałby w bazie niewidoczny
    w formularzu i wróciłby do gry przy kolejnej zmianie rodzaju.

    Rzuca ``ValueError`` z komunikatem po polsku; łapie go warstwa formularza i stawia pod polem.
    """
    settings = settings if isinstance(settings, dict) else {}
    if kind == MULTIPLE_CHOICE:
        rule = settings.get("partial_credit") or ALL_OR_NOTHING
        if rule not in (ALL_OR_NOTHING, PROPORTIONAL):
            raise ValueError("Nieznana reguła oceny częściowej.")
        return {"partial_credit": rule}
    if kind == SINGLE_CHOICE:
        return {}
    if kind == SHORT_TEXT:
        accepted = [str(item).strip() for item in settings.get("accepted") or [] if str(item).strip()]
        if not accepted:
            raise ValueError("Podaj co najmniej jedną uznawaną odpowiedź.")
        flags = {key: bool(settings.get(key, default)) for key, default in DEFAULT_TEXT_FLAGS.items()}
        return {"accepted": accepted, **flags}
    if kind == NUMERIC:
        answer = parse_number(settings.get("answer"))
        if answer is None:
            raise ValueError("Podaj poprawną odpowiedź liczbową.")
        tolerance_abs = parse_number(settings.get("tolerance_abs")) or ZERO
        tolerance_rel = parse_number(settings.get("tolerance_rel")) or ZERO
        if tolerance_abs < ZERO or tolerance_rel < ZERO:
            raise ValueError("Tolerancja nie może być ujemna.")
        return {
            # Napisy, a nie ``Decimal``: JSONField serializuje ``Decimal`` przez ``float`` i
            # tolerancja 0.005 wracałaby z bazy jako 0.005000000000000000104…, czyli jako liczba,
            # której koordynator nigdy nie wpisał. Napis wraca dokładnie taki, jaki poszedł.
            "answer": str(answer),
            "tolerance_abs": str(tolerance_abs),
            "tolerance_rel": str(tolerance_rel),
            "unit": str(settings.get("unit") or "").strip()[:20],
        }
    raise ValueError(f"Nieznany rodzaj pytania: {kind!r}.")
