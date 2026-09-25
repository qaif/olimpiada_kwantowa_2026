"""Rubryka oceniania: kryteria zadania i punkty cząstkowe w recenzji.

Po co rubryka istnieje (prośba organizatora): rozjazd dwóch niezależnych ocen bierze się zwykle
nie z różnego czytania rozwiązania, tylko z różnego dzielenia punktów. Koordynator zapisuje więc
podział **raz, przy zadaniu** (``grading.RubricCriterion``), a recenzent wypełnia go kryterium po
kryterium; suma jest skutkiem jawnych decyzji, a nie wrażenia.

Dwie zasady tego modułu:

- **suma jest liczona po stronie serwera** i to ona ląduje w ``Review.score``. Punkty z przeglądarki
  są danymi wejściowymi, a nie wynikiem – recenzent nie ma jak wysłać rubryki „za 4 punkty”
  i oceny „6 punktów”,
- **suma musi należeć do skali** zadania (albo etapu, gdy zadanie własnej nie ma). Zaokrąglania tu
  nie ma świadomie: cicha korekta oceny w górę albo w dół jest zmianą decyzji recenzenta, a nie
  poprawą literówki. Zamiast tego leci błąd z listą dopuszczalnych wartości i to recenzent
  rozstrzyga, które kryterium ocenił za wysoko.

Moduł nie importuje ``apps.grading.services`` (to serwisy wołają rubrykę, nie odwrotnie), więc
skalę dostaje z zewnątrz – patrz ``assert_total_in_scale``.

**Ułamki w kryteriach (po wydaniu 0.35.0).** W etapie z dowolnymi wartościami ocen
(``ScoringScale.free_values``) punkty za kryterium i samo maksimum kryterium mogą być ułamkami
(„2,5 z 3”) – co 0,01, z przecinkiem albo kropką. Wydanie 0.35.0 dopuściło ułamek w ocenie
zadania, ale rubryka dalej liczyła w pełnych punktach, więc recenzent oceniający według kryteriów
nie miał jak wystawić 4,25, którą bez rubryki wpisałby jednym polem. Punkty kryterium sprawdza
``apps.competitions.scoring.criterion_rule`` – ta sama klasa ``ScoreRule``, która sprawdza ocenę
zadania, więc „2,5” znaczy w obu miejscach to samo. W etapie „tylko ze skali” wszystko zostaje po
staremu: kryteria w pełnych punktach, suma z listy wartości skali.

W ``Review.rubric`` punkty leżą jako liczba JSON: ``int``, gdy całkowita (tak jak przed tą
zmianą), a ``float`` o najwyżej dwóch miejscach, gdy ułamkowa (``apps.core.points.points_json``).
"""

from __future__ import annotations

from decimal import Decimal

from rest_framework import status as http

from apps.competitions.scoring import criterion_rule, stage_free_values
from apps.core.api import DomainError
from apps.core.models import audit
from apps.core.points import PointsError, format_points, parse_points, points_json

from .models import RubricCriterion

#: Limit komentarza przy jednym kryterium. Komentarz uzasadnia punkty za **ten** fragment
#: rozwiązania; dłuższy wywód jest komentarzem wewnętrznym całej recenzji i ma własne pole.
MAX_CRITERION_COMMENT = 500

#: Górna granica liczby kryteriów w jednym zadaniu. Rubryka jest listą, którą recenzent przechodzi
#: przy każdej pracy – trzydzieści pozycji to już nie rubryka, tylko formularz nie do wypełnienia.
MAX_CRITERIA = 30

#: Górna granica punktów za jedno kryterium. Ta sama, co przy wartościach skali w panelu.
MAX_CRITERION_POINTS = 1000
#: Najmniejsze maksimum kryterium w etapie dowolnym: 0,01 pkt. W etapie „tylko ze skali” – 1.
MIN_FREE_CRITERION_POINTS = Decimal("0.01")


def _bad_request(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_400_BAD_REQUEST)


def criteria_for(problem) -> list[RubricCriterion]:
    """Kryteria zadania w kolejności ustalonej przez koordynatora. Pusta lista = zadanie bez rubryki."""
    if problem is None:
        return []
    return list(RubricCriterion.objects.filter(problem=problem).order_by("order", "id"))


def rubric_free(problem) -> bool:
    """Czy kryteria tego zadania liczy się w ułamkach – czyli czy etap zadania ocenia dowolnymi wartościami.

    Tryb jest trybem **etapu** (``ScoringScale.free_values``), tak samo jak dla oceny zadania – jedna
    odpowiedź na pytanie „jak się tu ocenia” dla całej komisji etapu. Zadanie bez etapu liczy
    w pełnych punktach, jak przed tą zmianą.
    """
    stage = getattr(problem, "stage", None) if problem is not None else None
    return stage is not None and stage_free_values(stage)


def criterion_points(criterion, raw, *, free: bool) -> Decimal:
    """Punkty za jedno kryterium (JSON z API albo tekst z formularza) → ``Decimal`` albo ``DomainError`` 400.

    Jedyne miejsce, które czyta punkty kryterium – woła je ``validate_rubric`` dla API, szkicu
    i formularza panelu (``rubric_from_post`` przekazuje tam tekst). Dwa kroki:

    - **kształt**: w etapie dowolnym liczba albo tekst z przecinkiem („2,5”), najwyżej dwa miejsca
      po przecinku (``apps.core.points.parse_points``); w etapie „tylko ze skali” – wyłącznie
      liczba całkowita, jak przed tą zmianą. Odmowa: ``INVALID_RUBRIC``,
    - **zakres**: od 0 do maksimum kryterium według ``criterion_rule`` – tej samej klasy reguły,
      którą sprawdzana jest ocena zadania. Odmowa: ``RUBRIC_POINTS_OUT_OF_RANGE``, z brzmieniem jak
      dotąd (maksimum pokazane jak na ekranie: „0–2,5”, a nie „0–2.50”).
    """
    if free:
        try:
            number = parse_points(raw)
        except PointsError as exc:
            raise _bad_request(f"Punkty za kryterium „{criterion.title}”: {exc}", "INVALID_RUBRIC") from exc
    else:
        if not isinstance(raw, int) or isinstance(raw, bool):
            raise _bad_request(
                f"Punkty za kryterium „{criterion.title}” muszą być liczbą całkowitą.",
                "INVALID_RUBRIC",
            )
        number = Decimal(raw)
    if not criterion_rule(criterion.max_points, free=free).accepts(number):
        raise _bad_request(
            f"Punkty za kryterium „{criterion.title}” muszą mieścić się w "
            f"0–{format_points(criterion.max_points)}.",
            "RUBRIC_POINTS_OUT_OF_RANGE",
        )
    return number


def validate_rubric(
    problem, raw, *, partial: bool = False, free: bool | None = None
) -> tuple[list[dict], Decimal]:
    """Sprowadza punkty cząstkowe do kanonicznego kształtu i liczy sumę.

    Zwraca ``(items, total)``: listę ``{criterion_id, points, comment}`` w kolejności kryteriów
    zadania oraz sumę punktów. ``partial=True`` (szkic) dopuszcza kryteria jeszcze niewypełnione –
    wtedy ``points`` bywa ``None`` i nie wchodzi do sumy. Przy wystawianiu oceny (``partial=False``)
    komplet jest wymagany: ocena z pominiętym kryterium nie jest oceną według rubryki.

    Kolejność wyniku bierze się z kryteriów, a nie z kolejności pól w żądaniu – dzięki temu zapis
    w bazie czyta się tak samo, jak rubrykę na ekranie, niezależnie od tego, co przysłał klient.

    ``free`` – tryb etapu (ułamki w kryteriach); ``None`` znaczy „odczytaj z etapu zadania”
    (``rubric_free``). Wołający, który zna już regułę oceny, podaje go wprost i oszczędza zapytanie.
    Suma jest ``Decimal`` – w etapie „tylko ze skali” zawsze całkowita, jak dotąd.
    """
    criteria = criteria_for(problem)
    if not criteria:
        if raw:
            raise _bad_request("To zadanie nie ma rubryki oceniania.", "RUBRIC_NOT_APPLICABLE")
        return [], Decimal(0)
    if free is None:
        free = rubric_free(problem)
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise _bad_request("Rubryka musi być listą pozycji.", "INVALID_RUBRIC")

    by_criterion: dict[int, dict] = {}
    known = {criterion.pk for criterion in criteria}
    for item in raw:
        if not isinstance(item, dict):
            raise _bad_request("Pozycja rubryki musi być obiektem.", "INVALID_RUBRIC")
        criterion_id = item.get("criterion_id")
        if not isinstance(criterion_id, int) or isinstance(criterion_id, bool):
            raise _bad_request("Pozycja rubryki wymaga identyfikatora kryterium.", "INVALID_RUBRIC")
        if criterion_id not in known:
            # Kryterium z innego zadania (albo skasowane) nie może wejść do oceny tej pracy: suma
            # policzona z cudzych kryteriów wyglądałaby poprawnie, a nie znaczyłaby nic.
            raise _bad_request(
                f"Kryterium {criterion_id} nie należy do tego zadania.", "RUBRIC_UNKNOWN_CRITERION"
            )
        if criterion_id in by_criterion:
            raise _bad_request("Kryterium może wystąpić w rubryce tylko raz.", "INVALID_RUBRIC")
        by_criterion[criterion_id] = item

    items: list[dict] = []
    total = Decimal(0)
    for criterion in criteria:
        item = by_criterion.get(criterion.pk)
        points = item.get("points") if item is not None else None
        comment = (item or {}).get("comment", "")
        if not isinstance(comment, str):
            raise _bad_request("Komentarz przy kryterium musi być tekstem.", "INVALID_RUBRIC")
        if points is None:
            if not partial:
                raise _bad_request(f"Uzupełnij punkty za kryterium „{criterion.title}”.", "RUBRIC_INCOMPLETE")
        else:
            number = criterion_points(criterion, points, free=free)
            total += number
            # Liczba JSON, a nie tekst: ``2`` jak dotąd, ``2.5`` dla ułamka (``points_json``).
            points = points_json(number)
        items.append(
            {
                "criterion_id": criterion.pk,
                "points": points,
                "comment": comment[:MAX_CRITERION_COMMENT],
            }
        )
    return items, total


def is_complete(items) -> bool:
    """Czy rubryka ma wypełnione **wszystkie** kryteria – tylko wtedy suma jest oceną."""
    return bool(items) and all(item.get("points") is not None for item in items)


def assert_total_in_scale(total, rule):
    """Suma z rubryki musi być dopuszczalną oceną. Bez zaokrąglania – z tym, co wolno, w błędzie.

    Reguła przychodzi z zewnątrz (``apps.competitions.scoring.score_rule``), bo to tam stoi
    pierwszeństwo „skala zadania przed skalą etapu” i tryb etapu, a ten moduł nie może importować
    serwisów (importują one jego). W etapie „tylko ze skali” suma musi być wartością skali;
    w etapie z dowolnymi wartościami – mieścić się w zakresie (od wydania 0.35.0).
    """
    if not rule.accepts(total):
        if rule.free:
            where = f"wykracza poza {rule.describe()}"
        else:
            values = ", ".join(str(value) for value in sorted(rule.values))
            where = f"nie należy do skali tego zadania: {values}"
        raise _bad_request(
            f"Suma punktów z rubryki ({format_points(total)}) {where}. "
            "Popraw punkty przy kryteriach – system nie zaokrągla oceny za Ciebie.",
            "RUBRIC_TOTAL_NOT_IN_SCALE",
        )
    return total


def rubric_from_post(problem, data, *, free: bool | None = None) -> list[dict] | None:
    """Odczytuje rubrykę z formularza panelu (``rubric-<id>-points`` / ``rubric-<id>-comment``).

    Zwraca ``None``, gdy zadanie nie ma rubryki **albo** gdy w żądaniu nie ma ani jednego jej pola:
    „nie przysłano” znaczy „nie ruszaj”, a nie „wyczyść” – dokładnie tak, jak przy adnotacjach.
    Puste pole punktów to ``None`` (kryterium jeszcze nieocenione), co ma znaczenie przy szkicu.

    Wartości nieliczbowe są odrzucane tutaj, a nie w ``validate_rubric``: tam przychodzi JSON z API
    (gdzie liczba jest liczbą), a stąd – tekst z formularza HTML. Wyjątkiem jest etap z dowolnymi
    wartościami: tekst („2,5”) idzie dalej bez zmian i czyta go ``criterion_points`` – ten sam
    czytnik, co dla JSON-a z API, więc formularz i API nie mogą różnie rozumieć tej samej liczby.
    """
    criteria = criteria_for(problem)
    if not criteria:
        return None
    if free is None:
        free = rubric_free(problem)
    prefix_seen = False
    items: list[dict] = []
    for criterion in criteria:
        points_raw = data.get(f"rubric-{criterion.pk}-points")
        comment = data.get(f"rubric-{criterion.pk}-comment")
        if points_raw is None and comment is None:
            continue
        prefix_seen = True
        points: int | str | None
        text = (points_raw or "").strip()
        if not text:
            points = None
        elif free:
            points = text
        else:
            try:
                points = int(text)
            except ValueError as exc:
                raise _bad_request(
                    f"Punkty za kryterium „{criterion.title}” muszą być liczbą całkowitą.",
                    "INVALID_RUBRIC",
                ) from exc
        items.append({"criterion_id": criterion.pk, "points": points, "comment": comment or ""})
    return items if prefix_seen else None


def rubric_rows(review) -> list[dict]:
    """Rubryka recenzji gotowa do wyświetlenia: kryterium + punkty + komentarz.

    Kryteria bierzemy z zadania, a nie z samego zapisu w recenzji: koordynator mógł dopisać
    kryterium po zapisaniu szkicu i recenzent ma je wtedy zobaczyć (puste). Wpis wskazujący
    kryterium, którego już nie ma, jest pomijany – jego punkty zostały już policzone w ``score``,
    a wiersz bez nazwy niczego nie tłumaczy.
    """
    criteria = criteria_for(review.submission.problem)
    saved = {item.get("criterion_id"): item for item in (review.rubric or []) if isinstance(item, dict)}
    return [
        {
            "criterion": criterion,
            "points": (saved.get(criterion.pk) or {}).get("points"),
            "comment": (saved.get(criterion.pk) or {}).get("comment", ""),
        }
        for criterion in criteria
    ]


def parse_criteria_lines(text: str, *, free: bool = False) -> list[dict]:
    """Zamienia tekst „punkty;tytuł;opis” (po jednej pozycji w wierszu) na listę kryteriów.

    ``free`` – etap z dowolnymi wartościami ocen: maksimum kryterium wolno wtedy podać ułamkiem
    („2,5;Pomysł”, co 0,01; przecinek nie koliduje z zapisem, bo człony dzieli średnik) i wraca ono
    jako ``Decimal``. Bez ``free`` – jak przed tą zmianą: liczba całkowita 1–1000 jako ``int``.

    Textarea zamiast formsetu – z tego samego powodu, co przy skali punktacji: dodawanie i kasowanie
    wierszy formsetu kosztowałoby wyspę JavaScriptu, a strict CSP nie ma tu na nią miejsca dla
    czynności, którą da się zrobić jednym polem tekstowym. Opis jest opcjonalny (trzeci człon
    wolno pominąć), bo część kryteriów tłumaczy się samym tytułem.

    Rzuca ``ValueError`` z gotowym zdaniem po polsku – zamienia je na błąd pola warstwa formularza
    (``apps.web.forms.ProblemForm``), a na błąd domenowy serwis. Numer wiersza jest w komunikacie,
    bo przy ośmiu kryteriach „zła wartość” bez wskazania, która, jest zagadką.
    """
    items: list[dict] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(";", 2)]
        if len(parts) < 2:
            raise ValueError(
                f"Wiersz {number}: brakuje średnika. Zapis to „punkty;tytuł;opis”, "
                "np. „2;Poprawność rachunków;liczy się wynik i jednostki”."
            )
        points: int | Decimal
        if free:
            try:
                points = parse_points(parts[0])
            except PointsError as exc:
                raise ValueError(f"Wiersz {number}: „{parts[0]}” – {exc}") from exc
            if not MIN_FREE_CRITERION_POINTS <= points <= MAX_CRITERION_POINTS:
                raise ValueError(
                    f"Wiersz {number}: punkty muszą mieścić się w "
                    f"{format_points(MIN_FREE_CRITERION_POINTS)}–{MAX_CRITERION_POINTS}."
                )
        else:
            try:
                points = int(parts[0])
            except ValueError as exc:
                hint = (
                    " – ułamki są dozwolone wyłącznie w etapie z dowolnymi wartościami ocen"
                    if _looks_fractional(parts[0])
                    else ""
                )
                raise ValueError(f"Wiersz {number}: „{parts[0]}” nie jest liczbą całkowitą{hint}.") from exc
            if not 1 <= points <= MAX_CRITERION_POINTS:
                raise ValueError(f"Wiersz {number}: punkty muszą mieścić się w 1–{MAX_CRITERION_POINTS}.")
        if not parts[1]:
            raise ValueError(f"Wiersz {number}: brakuje tytułu kryterium.")
        items.append(
            {
                "max_points": points,
                "title": parts[1][:200],
                "description": parts[2] if len(parts) > 2 else "",
            }
        )
    if len(items) > MAX_CRITERIA:
        raise ValueError(f"Rubryka może mieć najwyżej {MAX_CRITERIA} kryteriów.")
    return items


def _looks_fractional(text: str) -> bool:
    """Czy tekst jest ułamkiem („2,5”, „2.5”) – żeby odmowa w etapie skali mówiła, dlaczego."""
    try:
        parse_points(text)
    except PointsError:
        return False
    return True


def format_criteria_lines(criteria) -> str:
    """Odwrotność ``parse_criteria_lines`` – rubryka z bazy w postaci, w jakiej wraca do formularza.

    Maksimum przez ``format_points``: kolumna jest dziesiętna, więc surowe ``2.00`` wróciłoby do
    formularza jako tekst, którego ``parse_criteria_lines`` w etapie skali nie przyjmie. Po polsku
    ułamek ma przecinek („2,5;Pomysł”) – czytnik przyjmuje go z powrotem bez zmian.
    """
    return "\n".join(
        f"{format_points(item.max_points)};{item.title}"
        + (f";{item.description}" if item.description else "")
        for item in criteria
    )


def set_criteria(problem, items, *, actor=None, request=None) -> int:
    """Zapisuje rubrykę zadania: aktualizuje po kolejności, dopisuje brakujące, kasuje nadmiarowe.

    Aktualizacja w miejscu, a nie „skasuj wszystko i utwórz od nowa”, jest tu istotna: recenzje
    trzymają w ``Review.rubric`` identyfikatory kryteriów, więc przy kasowaniu poprawienie
    literówki w tytule zrywałoby powiązanie z punktami, które ktoś już wpisał. Kryterium usunięte
    **z końca** listy znika naprawdę – ale to świadoma decyzja koordynatora, a nie skutek uboczny
    zapisu formularza.

    Zwraca liczbę kryteriów po zapisie. Wpis audytowy powstaje wyłącznie, gdy rubryka faktycznie
    się zmieniła – otwarcie i zapisanie formularza bez zmian nie jest zdarzeniem.
    """
    existing = criteria_for(problem)
    # Maksimum z kolumny dziesiętnej (``Decimal("2.00")``) jest równe ``2`` z formularza, więc zapis
    # bez zmian nadal nie jest zdarzeniem; do audytu ``Decimal`` trafia przez ``jsonable_points``
    # w ``audit`` – jako ``2`` albo ``2.5``.
    before = [
        {"max_points": item.max_points, "title": item.title, "description": item.description}
        for item in existing
    ]
    for index, item in enumerate(items):
        if index < len(existing):
            criterion = existing[index]
            criterion.order = index + 1
            criterion.title = item["title"]
            criterion.description = item.get("description", "")
            criterion.max_points = item["max_points"]
            criterion.save(update_fields=["order", "title", "description", "max_points"])
        else:
            RubricCriterion.objects.create(
                problem=problem,
                order=index + 1,
                title=item["title"],
                description=item.get("description", ""),
                max_points=item["max_points"],
            )
    for criterion in existing[len(items) :]:
        criterion.delete()

    if before != items:
        audit(
            actor,
            "problem.rubric_set",
            problem,
            {"from": before, "to": items},
            request=request,
        )
    return len(items)
