"""Dane karty zadania (panel koordynatora).

Zadanie jest dziś opisane w pięciu miejscach naraz: treść i limity w formularzu zadania, skala
przy etapie, rubryka przy ocenianiu, reguły przydziału na ekranie przydziałów, a prace – w tabeli
całego etapu, z której trzeba je wyłuskać wyszukiwarką. Koordynator pytający „co z zadaniem 3”
musi więc obejść pięć ekranów i zapamiętać, gdzie co było. Karta odpowiada na to pytanie w jednym
miejscu i dokłada czynności, które z odpowiedzi wynikają (blokada do oceny, przydział, ocena
końcowa, paczka ZIP) – wszystkie przez **istniejące** adresy akcji, bez ani jednej nowej reguły.

Importy z ``apps.grading`` są lokalne, wewnątrz funkcji: ``grading.models`` importuje
``competitions.models`` już przy starcie aplikacji, więc import na górze tego pliku zamknąłby cykl.

Sekcje opcjonalne (rubryka, szablony komentarzy, podobieństwa) są **zgadywane po obecności
modułu**, a nie po ustawieniu: instalacja bez danego modułu ma pokazać kartę bez tej sekcji,
a nie 500. Każde takie wejście zwraca ``None``, co szablon czyta jako „tego tu nie ma”.
"""

from __future__ import annotations

from apps.core.links import optional_url, participant_card

from .models import Problem

#: Skok szerokości słupka na wykresie rozkładu ocen. Styl w atrybucie jest w tym serwisie
#: niedopuszczalny (polityka bezpieczeństwa), więc szerokość musi być nazwą klasy z zamkniętej
#: listy – dokładnie tak, jak na pasku postępu etapu i na stronie statystyk. Dokładna liczba stoi
#: obok słupka i to ona jest odpowiedzią; słupek pokazuje proporcję.
BAR_STEP = 5


def bar_width(count: int, total: int) -> int:
    """Szerokość słupka w procentach, zaokrąglona do pełnych 5% – nazwa klasy CSS, nie styl.

    Wartość niezerowa nigdy nie schodzi do zera: jedna praca na dwustu to 0,5%, a słupek szerokości
    zero wyglądałby jak „nikt nie dostał tylu punktów”, co byłoby nieprawdą.
    """
    if total <= 0 or count <= 0:
        return 0
    return max(BAR_STEP, round(count * 100 / total / BAR_STEP) * BAR_STEP)


def _scale(problem: Problem) -> dict:
    """Skala obowiązująca to zadanie: własna albo odziedziczona po etapie.

    ``source`` mówi **skąd** wzięły się te liczby, bo to jest właśnie pytanie koordynatora: czy
    zmiana skali etapu ruszy to zadanie, czy ma ono nadpisanie, o którym ktoś zapomniał.
    """
    from apps.core.api import DomainError
    from apps.grading.services import allowed_scores, scale_items

    from .scoring import problem_maximum, stage_free_values, uses_own_range

    stage = problem.stage
    try:
        values = sorted(allowed_scores(stage, problem))
    except DomainError:
        values = []
    scale = getattr(stage, "scoring_scale", None)
    free = stage_free_values(stage)
    return {
        "values": values,
        "items": scale_items(stage, problem),
        # Zadanie z samym maksimum (tryb dowolny, wydanie 0.35.0) też ma zakres „własny” – zmiana
        # skali etapu go nie ruszy, i to jest dokładnie to, o co pyta koordynator.
        "source": "problem" if uses_own_range(problem, free=free) else "stage",
        # Maksimum z reguły oceny (``scoring.problem_maximum``), a nie z pola: to ta sama liczba,
        # którą widzi recenzent przy polu punktów i tabela wyników w nagłówku kolumny.
        "max_points": problem_maximum(stage, problem),
        "free_values": free,
        "stage_has_scale": scale is not None,
    }


def _rubric(problem: Problem) -> list | None:
    """Kryteria rubryki zadania. ``None`` = tej instalacji nie dotyczy (brak modułu rubryk)."""
    try:
        from apps.grading.rubric import criteria_for
    except ImportError:  # pragma: no cover - rubryka jest opcjonalna
        return None
    return list(criteria_for(problem))


def _snippets(problem: Problem) -> list | None:
    """Wspólne szablony komentarzy przypięte do tego zadania. ``None`` = brak modułu szablonów.

    Wyłącznie wspólne (bez właściciela): prywatnych szablonów recenzenta nie widzi nikt poza nim,
    także koordynator – tak stanowi ``apps.grading.snippets``.
    """
    try:
        from apps.grading.snippets import problem_snippets
    except ImportError:  # pragma: no cover - szablony są opcjonalne
        return None
    return list(problem_snippets(problem))


def _rules(problem: Problem, pool: list) -> dict:
    """Reguły „to zadanie recenzuje ta osoba” razem z pulą, z której wybiera się kolejną.

    Pula przychodzi z zewnątrz, a nie jest tu pobierana: ta sama lista aktywnych recenzentów stoi
    również przy przydziale pojedynczej pracy, a dwa pobrania dałyby dwa razy to samo zapytanie.
    """
    from apps.grading.models import ProblemReviewerRule

    rules = list(
        ProblemReviewerRule.objects.filter(problem=problem)
        .select_related("reviewer", "reviewer__user")
        .order_by("created_at", "id")
    )
    taken = {rule.reviewer_id for rule in rules}
    return {
        "rows": rules,
        # Pula bez osób, które regułę już mają: druga taka sama reguła jest niemożliwa
        # (unikalność w bazie), a pozycja nie do wybrania byłaby pułapką na koordynatora.
        "candidates": [member for member in pool if member.pk not in taken],
    }


def _submission_rows(problem: Problem, query: str) -> list[dict]:
    """Prace tego zadania – ten sam wiersz, co na ekranie przydziałów etapu, zawężony do zadania.

    Reużycie ``stage_assignment_rows`` jest tu decyzją, a nie oszczędnością: gdyby karta liczyła
    prace własnym zapytaniem, dwa ekrany panelu odpowiadałyby na to samo pytanie („co wolno z tą
    pracą zrobić”) dwoma różnymi warunkami i prędzej czy później by się rozjechały.
    """
    from apps.grading.services import stage_assignment_rows

    rows = [
        row
        for row in stage_assignment_rows(problem.stage, query)
        if row["submission"].problem_id == problem.pk
    ]
    from .scoring import coordinator_score_widget, safe_score_rule

    scale_values = _scale(problem)["values"]
    # Tryb etapu i zakres zadania (wydanie 0.35.0) – ten sam kształt pola, co na ekranie
    # przydziałów (``templates/web/coordinator/_score_input.html``).
    widget = coordinator_score_widget(safe_score_rule(problem.stage, problem))
    for row in rows:
        row["scale_values"] = scale_values
        row["score_widget"] = widget
        row["participant_url"] = participant_card(row["submission"].entry.participant)
    return rows


def _distribution(problem: Problem, scale_values: list[int]) -> dict:
    """Rozkład ocen końcowych tego zadania: ile prac dostało ile punktów.

    Oś liczymy ze **skali**, a nie z ocen, które padły: wartość, której nikt nie dostał, jest
    informacją („nikt nie dostał 6”), a wykres bez niej sugerowałby, że takiej oceny nie ma.
    Gdy skali nie ma (etap jej nie ustawił), oś powstaje z samych ocen – lepszy wykres z dziurami
    niż brak wykresu.
    """
    from apps.grading.models import FinalGrade

    scores = list(FinalGrade.objects.filter(submission__problem=problem).values_list("score", flat=True))
    total = len(scores)
    axis = scale_values or sorted(set(scores))
    counts = {value: 0 for value in axis}
    for score in scores:
        counts.setdefault(score, 0)
        counts[score] += 1
    bars = [
        {
            "value": value,
            "count": count,
            "width": bar_width(count, total),
            "share": round(count * 100 / total) if total else 0,
        }
        for value, count in sorted(counts.items())
    ]
    return {
        "bars": bars,
        "total": total,
        "mean": round(sum(scores) / total, 2) if total else None,
    }


def _model_solution_url(problem: Problem) -> str | None:
    """Adres wzorcówki albo ``None``, gdy zadanie jej nie ma (albo model nie zna tego pola).

    Plik leży na prywatnym storage i nie ma publicznego adresu – jedyną drogą jest widok panelu,
    który sprawdza rolę. Stąd odnośnik do widoku, a nie do pliku.
    """
    if not getattr(problem, "model_solution_pdf", None):
        return None
    return optional_url("web:problem-model-solution", problem.pk)


def problem_card(problem: Problem, *, query: str = "") -> dict:
    """Komplet danych karty jednego zadania.

    Słownik, a nie obiekt: karta jest zestawem niezależnych sekcji, z których każda bywa pusta
    (brak modułu, etap bez skali, zadanie bez prac), a szablon czyta je po nazwie.
    """
    from apps.grading.services import reviewer_pool
    from apps.results.models import ResultsPublication

    stage = problem.stage
    scale = _scale(problem)
    pool = reviewer_pool()
    return {
        "problem": problem,
        "stage": stage,
        "scale": scale,
        "rubric": _rubric(problem),
        "snippets": _snippets(problem),
        "rules": _rules(problem, pool),
        "reviewer_pool": pool,
        "rows": _submission_rows(problem, query),
        "query": query,
        "distribution": _distribution(problem, scale["values"]),
        "model_solution_url": _model_solution_url(problem),
        # Podobieństwa liczy się dla całego etapu (porównuje się prace między sobą), więc karta
        # prowadzi na ekran etapu – filtra „tylko to zadanie” ten ekran nie ma i karta go nie udaje.
        "similarity_url": optional_url("web:coordinator-stage-similarity", stage.pk),
        "results_published": ResultsPublication.objects.filter(stage=stage).exists(),
    }
