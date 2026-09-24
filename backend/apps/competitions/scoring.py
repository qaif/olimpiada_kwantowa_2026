"""Reguła oceny zadania: co wolno wpisać w pole „punkty” – jedna odpowiedź dla całego systemu.

Od wydania 0.35.0 etap ocenia się na jeden z dwóch sposobów (``ScoringScale.free_values``):

- **tylko wartości ze skali** – dotychczasowe zachowanie i stan każdego etapu, który organizator
  nie przełączył: ocena musi być jedną z wartości skali (0/2/5/6),
- **dowolna wartość od minimum do maksimum, co 0,01** – prośba organizatora z 2026-09-24: ocena
  4,25 jest poprawna, o ile mieści się między najniższą a najwyższą wartością skali. Wartości
  skali i ich etykiety zostają wtedy **podpowiedzią** dla recenzenta, a nie listą zamkniętą.

Granice biorą się z zadania, jeśli ma własny zakres, a w przeciwnym razie z etapu – ta sama reguła
pierwszeństwa, którą wcześniej niósł ``grading.services.allowed_scores``. Zadanie ma własny zakres,
gdy ma własną skalę (``Problem.scoring_values``) albo – wyłącznie w trybie dowolnym – samo maksimum
(``Problem.max_points`` bez listy wartości; „zadania mogą mieć różną ilość punktów”). Tryb jest
zawsze trybem **etapu**: zadanie z własną skalą w etapie „tylko ze skali” przyjmuje wyłącznie swoje
wartości, a w etapie dowolnym – dowolną liczbę między swoim minimum i maksimum.

**Postać przechowywana.** Wszystko, co ta reguła porównuje, jest w postaci, w jakiej leży w kolumnie
``score`` – czyli przesunięte o ``ScoringScale.offset`` (etap 2 § 1.2.6 b). Zadanie z własnym
zakresem przesunięcia nie ma i mieć nie może, więc jego granice wracają dosłownie; zadanie z samym
maksimum ma zakres ``0 … max`` także w etapie z punktami ujemnymi (decyzja przy wydaniu 0.35.0:
własny zakres zadania nie jest wariantem skali etapu, tak jak nie była nim własna skala). Ekrany
przeliczają postać do pokazania przez ``to_display``/``to_stored``; przy ``offset = 0`` – czyli
w każdym konkursie bez punktów ujemnych – obie postaci są tą samą liczbą.

Każda droga zapisu oceny (recenzja, poprawka, korekta koordynatora, rozstrzygnięcie rozjazdu,
reklamacja, rozmowa kwalifikacyjna, API) woła ``ScoreRule.clean`` – dwie kopie tej reguły
rozjechałyby się przy pierwszej zmianie, a skutek wyszedłby dopiero w ogłoszonej tabeli.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from rest_framework import status

from apps.core.api import DomainError
from apps.core.points import POINTS_QUANTUM, PointsError, format_points, parse_points, to_points

#: Kod błędu dla oceny spoza skali albo spoza zakresu. Jeden kod na oba tryby, bo dla klienta API
#: to jest ta sama sytuacja („ta liczba nie jest tu dopuszczalną oceną”); treść komunikatu mówi,
#: czy chodziło o listę wartości, czy o zakres.
SCORE_NOT_IN_SCALE = "SCORE_NOT_IN_SCALE"


def _conflict(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, status.HTTP_409_CONFLICT)


def _scale_items(raw) -> tuple[dict, ...]:
    """Pozycje skali ``{value, label}`` w postaci do pokazania – bez pozycji, które nie są liczbą."""
    return tuple(
        {"value": item["value"], "label": item.get("label", "")}
        for item in raw or []
        if isinstance(item, dict)
        and isinstance(item.get("value"), int)
        and not isinstance(item.get("value"), bool)
    )


@dataclass(frozen=True)
class ScoreRule:
    """Dopuszczalne oceny jednego zadania – w postaci przechowywanej – i sposób ich sprawdzenia."""

    #: Tryb etapu: ``True`` = dowolna wartość z zakresu co 0,01, ``False`` = tylko wartości skali.
    free: bool
    #: Wartości skali (postać przechowywana). Puste dla zadania z samym maksimum.
    values: frozenset = field(default_factory=frozenset)
    #: Najniższa i najwyższa dopuszczalna ocena (postać przechowywana).
    minimum: Decimal = Decimal(0)
    maximum: Decimal = Decimal(0)
    #: Przesunięcie skali, którym ta ocena podlega: offset etapu albo zero dla własnego zakresu.
    offset: int = 0
    #: Pozycje skali do pokazania (``{value, label}``) – lista wyboru albo podpowiedź przy polu.
    items: tuple = ()

    # --- przeliczenia postaci -----------------------------------------------------------------

    def to_stored(self, display) -> Decimal:
        """Ocena wpisana przez człowieka (z minusem, gdy skala go ma) → postać do kolumny ``score``."""
        return to_points(display) + self.offset

    def to_display(self, stored) -> Decimal | None:
        """Ocena z kolumny ``score`` → liczba, którą wystawił recenzent. ``None`` przechodzi bez zmian."""
        number = to_points(stored)
        return None if number is None else number - self.offset

    @property
    def display_minimum(self) -> Decimal:
        return self.minimum - self.offset

    @property
    def display_maximum(self) -> Decimal:
        return self.maximum - self.offset

    # --- sprawdzenie ----------------------------------------------------------------------------

    def accepts(self, stored) -> bool:
        """Czy liczba (postać przechowywana) jest tu dopuszczalną oceną. Nie rzuca wyjątków."""
        number = to_points(stored)
        if number is None:
            return False
        if self.free:
            return self.minimum <= number <= self.maximum and number == number.quantize(POINTS_QUANTUM)
        # ``Decimal("5.00") in {5}`` jest prawdą: Python gwarantuje równe skróty dla równych liczb
        # różnych typów, więc ocena z kolumny dziesiętnej porównuje się ze skalą całkowitą wprost.
        return number in self.values

    def describe(self) -> str:
        """Czym jest dopuszczalna ocena – zdanie do komunikatu błędu (postać przechowywana)."""
        if self.free:
            return (
                f"zakres {format_points(self.minimum)}–{format_points(self.maximum)} "
                "(co 0,01, najwyżej dwa miejsca po przecinku)"
            )
        return f"skala {sorted(self.values)}"

    def clean(self, raw) -> Decimal:
        """Ocena przysłana przez wołającego (postać przechowywana) → ``Decimal`` albo ``DomainError`` 400.

        Przyjmuje liczbę albo tekst z przecinkiem („4,25”) – normalizację robi
        ``apps.core.points.parse_points``. Odmowy:

        - ``SCORE_INVALID`` – to nie jest liczba albo ma więcej niż dwa miejsca po przecinku,
        - ``SCORE_NOT_IN_SCALE`` – liczba spoza skali (tryb „tylko ze skali”) albo spoza zakresu
          (tryb dowolny). Brzmienie dla trybu skali jest to samo, co przed wydaniem 0.35.0.
        """
        try:
            number = parse_points(raw)
        except PointsError as exc:
            raise DomainError(str(exc), exc.code, status.HTTP_400_BAD_REQUEST) from exc
        if self.accepts(number):
            return number
        if self.free:
            detail = f"Ocena {format_points(number)} wykracza poza {self.describe()}."
        else:
            detail = f"Ocena {format_points(number)} nie należy do skali {sorted(self.values)}."
        raise DomainError(detail, SCORE_NOT_IN_SCALE, status.HTTP_400_BAD_REQUEST)


def uses_own_range(problem, *, free: bool) -> bool:
    """Czy ocena tego zadania rządzi się **jego** zakresem, a nie skalą etapu.

    Własna skala – zawsze. Samo maksimum – tylko w etapie dowolnym; w etapie „tylko ze skali” takie
    zadanie nie ma prawa istnieć (pilnuje tego ``Problem.clean`` i odmowa przełączenia etapu z
    powrotem, ``services.set_scoring_scale``), a gdyby zostało wpisane z pominięciem obu, ocenia je
    skala etapu – bezpieczniej niż zakres, którego etap nie uznaje.

    Tego samego pytania używają przesunięcia sumy (``services.stage_scoring``) i zbiór ocen rządzonych
    skalą etapu (``services.scores_in_use``), więc odpowiedź jest jedna.
    """
    return problem.has_own_scale or (free and problem.has_own_max)


def stage_free_values(stage) -> bool:
    """Czy etap ocenia dowolnymi wartościami. Etap bez skali – nie (i tak nie da się w nim oceniać)."""
    scale = getattr(stage, "scoring_scale", None)
    return bool(scale is not None and scale.free_values)


def score_rule(stage, problem=None, *, scale=None) -> ScoreRule:
    """Reguła oceny zadania ``problem`` w etapie ``stage`` – albo samego etapu, gdy zadania brak.

    ``problem`` jest opcjonalny wyłącznie dla wywołań, które pytają o etap jako całość (rozmowa
    kwalifikacyjna, ekran przydziałów bez zadania). Każde miejsce, które zna zgłoszenie, **musi**
    podać jego zadanie – inaczej ocena przechodząca walidację nie musiałaby należeć do zakresu,
    według którego praca jest oceniana.

    Etap bez skali (albo ze skalą pustą) to ``409 SCORING_SCALE_MISSING`` – ten sam kod i to samo
    brzmienie, co przed wydaniem 0.35.0. Zadanie z własną skalą ocenia się mimo braku skali etapu,
    ale wtedy wyłącznie w trybie „tylko ze skali”: o trybie rozstrzyga etap, a etap go nie ma.

    ``scale`` pozwala zapytać o skalę **jeszcze niezapisaną** (``services.set_scoring_scale`` sprawdza,
    czy wystawione oceny zmieszczą się w skali po zmianie) bez podmieniania jej w obiekcie etapu –
    odrzucona zmiana nie może zostawić wołającemu w pamięci skali, której w bazie nie ma.
    """
    if scale is None:
        scale = getattr(stage, "scoring_scale", None)
    free = bool(scale is not None and scale.free_values)
    if problem is not None and problem.has_own_scale:
        values = frozenset(problem.allowed_values())
        if values:
            return ScoreRule(
                free=free,
                values=values,
                minimum=Decimal(min(values)),
                maximum=Decimal(max(values)),
                offset=0,
                items=_scale_items(problem.scoring_values),
            )
    if problem is not None and free and problem.has_own_max:
        return ScoreRule(
            free=True,
            values=frozenset(),
            minimum=Decimal(0),
            maximum=to_points(problem.max_points),
            offset=0,
            items=(),
        )
    if scale is None:
        raise _conflict("Etap nie ma skali punktacji.", "SCORING_SCALE_MISSING")
    values = frozenset(scale.stored_allowed_values())
    if not values:
        raise _conflict("Skala punktacji etapu jest pusta.", "SCORING_SCALE_MISSING")
    return ScoreRule(
        free=free,
        values=values,
        minimum=Decimal(min(values)),
        maximum=Decimal(max(values)),
        offset=scale.offset or 0,
        items=_scale_items(scale.values),
    )


def safe_score_rule(stage, problem=None) -> ScoreRule | None:
    """``score_rule`` dla ekranów: brak skali to ``None``, a nie wyjątek.

    Ekran ma stanąć także dla etapu, którego skalę ktoś skasował – znika z niego wtedy sam formularz
    oceny, a nie cała strona (ta sama zasada, co w ``grading.services.scale_items``).
    """
    try:
        return score_rule(stage, problem)
    except DomainError:
        return None


def problem_maximum(stage, problem) -> Decimal | None:
    """Maksimum zadania **w postaci do pokazania** („Zad. 3 (max 12,5)”) albo ``None`` bez skali."""
    rule = safe_score_rule(stage, problem)
    return None if rule is None else rule.display_maximum


def problem_maxima(stage, problems) -> dict[int, Decimal]:
    """``{Problem.pk: maksimum do pokazania}`` dla listy zadań etapu – bez zapytania na zadanie.

    Skala etapu jest czytana raz (``stage.scoring_scale``), a zadania przychodzą od wołającego;
    zadanie, dla którego nie da się ustalić maksimum (etap bez skali), nie ma klucza.
    """
    maxima: dict[int, Decimal] = {}
    for problem in problems:
        maximum = problem_maximum(stage, problem)
        if maximum is not None:
            maxima[problem.pk] = maximum
    return maxima


def stage_maximum_total(stage, problems) -> Decimal | None:
    """Najwyższa możliwa suma etapu: suma maksimów zadań (z wagami, gdy konkurs je ma).

    Liczona **tą samą** funkcją, co suma uczestnika (``services.StageScoring.total``), z maksimów
    w postaci przechowywanej – więc wagi, przesunięcia i zaokrąglenie są dokładnie te same, a „38,75
    z 40” nie może pokazać maksimum policzonego inaczej niż wynik. ``None``, gdy etap nie ma zadań
    albo któremuś zadaniu brak skali – a także dla etapu w formie testu i etapu z komponentami
    (``StageComponent``): tam suma etapu nie jest sumą ocen zadań, więc suma ich maksimów nie byłaby
    jej maksimum, tylko liczbą z innego rachunku.
    """
    from .services import stage_scoring

    problems = list(problems)
    if not problems or stage.is_quiz or stage.components.exists():
        return None
    stored: dict[int, Decimal] = {}
    for problem in problems:
        rule = safe_score_rule(stage, problem)
        if rule is None:
            return None
        stored[problem.pk] = rule.maximum
    return stage_scoring(stage, problems=problems).total(stored)


def coordinator_score_widget(rule) -> dict | None:
    """Pole punktów na ekranach koordynatora – w postaci **przechowywanej**, jak cały ten ekran.

    Ekrany koordynatora (przydziały, moderacja) od zawsze pokazują i przyjmują ocenę tak, jak leży
    w kolumnie ``score`` (lista wyboru z ``allowed_scores``); przy ``offset = 0`` – czyli wszędzie
    poza etapem z punktami ujemnymi – to ta sama liczba, którą wystawił recenzent. Wydanie 0.35.0
    dokłada drugi kształt: w etapie z dowolnymi wartościami pole liczbowe ``step=0.01`` z granicami
    zakresu zamiast listy. ``None`` = zadanie bez skali (ekran pokazuje odnośnik do skali etapu).
    """
    if rule is None:
        return None
    return {
        "free": rule.free,
        "values": sorted(rule.values),
        "minimum": rule.minimum,
        "maximum": rule.maximum,
    }


def problem_maxima_by_number(stage, problems=None) -> dict[str, Decimal]:
    """``{"3": Decimal("12.5")}`` – maksima zadań po **numerze**, do nagłówków tabel wyników.

    Klucz jest numerem zadania jako tekst, bo tak kluczuje punkty snapshot wyników i wiersz
    ``compute_stage_results`` – nagłówek „Zad. 3 (max 12,5)” stoi nad kolumną ``points["3"]``.
    Maksimum czytamy **na żywo** z reguły oceny, także nad tabelą już ogłoszoną: to opis skali,
    a nie wynik, i nie zmienia ani jednej liczby zamrożonej w snapshocie. Dwa zapytania (zadania
    i skala etapu), niezależnie od liczby wierszy tabeli.
    """
    problems = list(stage.problems.all()) if problems is None else list(problems)
    maxima: dict[str, Decimal] = {}
    for problem in problems:
        maximum = problem_maximum(stage, problem)
        if maximum is not None:
            maxima[str(problem.number)] = maximum
    return maxima
