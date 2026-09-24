"""Informacja zwrotna dla uczestnika po ogłoszeniu wyników etapu.

Co ten moduł składa w jedno: punkty za każde zadanie, komentarze recenzentów napisane **do
uczestnika**, jego miejsce w ogłoszonej tabeli oraz próg kwalifikacji i decyzję. Dotąd wszystkie
te informacje istniały, ale w czterech różnych miejscach (panel, publiczna tabela, próg w panelu
koordynatora, status wpisu) i uczestnik musiał je sobie zestawić sam.

Zasady, na których ten moduł stoi:

- **przed publikacją nie ma nic**. ``participant_feedback`` zwraca ``None``, a widok zamienia to
  na 404. Nie 403: odpowiedź nie ma potwierdzać, że wyniki są policzone i czekają na ogłoszenie,
- **recenzent zostaje anonimowy**. Wychodzą wyłącznie ``Review.comment_for_participant``
  i adnotacje z ``public=True``; ``comment_internal`` nie opuszcza tego modułu nigdy, a autorzy
  są podpisani „Recenzent A/B” w kolejności rund (PROJEKT.md 2.4),
- **miejsce bierze się ze snapshotu**, a nie z przeliczenia na żywo. Ogłoszona tabela jest
  zamrożona, więc miejsce z niej jest tym samym miejscem, które widzi publiczność. Wiersze
  snapshotu są anonimowe i dobrze – dowiązanie idzie przez ``ResultsPublication.entry_totals``,
  czyli mapę ``{wpis: suma}`` zapisaną w chwili publikacji,
- punkty za zadania czytamy **na żywo** z ``FinalGrade``, tak samo jak ``results_for_participant``:
  uczestnikowi należy się prawda o jego pracy także wtedy, gdy komisja zmieniła ocenę po
  ogłoszeniu tabeli. Rozjazd z tabelą jest wtedy pokazany wprost, a nie zamiatany.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Prefetch

from apps.competitions.models import (
    Problem,
    QualificationMode,
    Stage,
    StageEntry,
    StageEntryStatus,
)
from apps.core.points import format_points, to_points
from apps.grading.code_view import public_line_notes
from apps.grading.models import Review, ReviewStatus
from apps.submissions.models import Submission, SubmissionStatus

from .models import ResultsPublication

#: Podpisy recenzentów w kolejności ich wystąpienia przy pracy. Litery, a nie numery rund: runda
#: jest szczegółem procedury (druga recenzja ślepa, trzecia po moderacji), a uczestnikowi
#: potrzebna jest wyłącznie informacja, że to dwie różne osoby.
REVIEWER_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def reviewer_label(index: int) -> str:
    """„Recenzent A”, „Recenzent B”… – a po wyczerpaniu alfabetu numer, żeby nie było kolizji."""
    if index < len(REVIEWER_LETTERS):
        return f"Recenzent {REVIEWER_LETTERS[index]}"
    return f"Recenzent {index + 1}"


@dataclass(frozen=True)
class ReviewFeedback:
    """Jedna recenzja w postaci, w jakiej wolno ją pokazać uczestnikowi."""

    label: str
    comment: str
    annotations: list[dict] = field(default_factory=list)
    #: Uwagi przypięte do linii kodu (``{line, text}``) – osobne pole, a nie kolejne pozycje
    #: ``annotations``, bo czyta się je inaczej: „linia 42”, a nie „strona 2”. Oba kształty
    #: mieszkają w tym samym polu JSON recenzji i rozdziela je ``grading.code_view``.
    line_notes: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class ProblemFeedback:
    """Jedno zadanie etapu: punkty i to, co recenzenci napisali do uczestnika."""

    number: int
    title: str
    score: Decimal
    reviews: list[ReviewFeedback] = field(default_factory=list)
    #: ``True``, gdy do zadania nie ma żadnej wersji pracy – zero punktów za brak, a nie za błąd.
    missing: bool = False

    @property
    def has_comments(self) -> bool:
        return bool(self.reviews)


@dataclass(frozen=True)
class StageFeedback:
    """Komplet informacji zwrotnej z jednego etapu – wszystko, co widok ma do wypisania."""

    stage: Stage
    publication: ResultsPublication
    entry: StageEntry
    problems: list[ProblemFeedback]
    total: Decimal
    #: Suma z ogłoszonej tabeli. ``None``, gdy publikacja nie zna tego wpisu (tabela sprzed
    #: wprowadzenia ``entry_totals`` albo wpis utworzony po publikacji).
    published_total: Decimal | None
    rank: int | None
    #: Liczba wierszy ogłoszonej tabeli – „miejsce 7 na 312” mówi więcej niż samo „miejsce 7”.
    rank_of: int
    qualified: bool
    status_label: str
    #: Opis progu kwalifikacji zdaniem albo pusty napis, gdy etap progu nie ma (np. finał).
    threshold: str
    #: Najniższa suma wśród zakwalifikowanych w ogłoszonej tabeli – faktyczna granica awansu.
    cutoff_total: Decimal | None

    @property
    def differs_from_published(self) -> bool:
        """Czy bieżące punkty rozjechały się z ogłoszoną tabelą (późna decyzja komisji)."""
        return self.published_total is not None and self.published_total != self.total


def describe_threshold(stage: Stage) -> str:
    """Próg kwalifikacji etapu jednym zdaniem – albo pusty napis, gdy etapu nie ma czym opisać.

    Zdanie powstaje z ``QualificationRule``, czyli z tych samych parametrów, na których liczy
    ``results.services._qualified_entry_ids``. Gdyby widok układał je sam, opis progu i sam próg
    rozjechałyby się przy pierwszej zmianie trybu.
    """
    rule = getattr(stage, "qualification_rule", None)
    if rule is None:
        return ""
    # Próg bywa od wydania 0.35.0 ułamkowy (38,5) – przez ``format_points``, żeby zdanie nie
    # mówiło „38.50 pkt” ani „38,50 pkt”, tylko tak, jak pokazujemy punkty wszędzie indziej.
    minimum = format_points(rule.min_points)
    if rule.mode == QualificationMode.MIN_POINTS:
        return f"do następnego etapu przechodzą osoby z wynikiem co najmniej {minimum} pkt"
    if rule.mode == QualificationMode.TOP_N:
        return f"do następnego etapu przechodzi {rule.top_n} najlepszych wyników"
    if rule.mode == QualificationMode.TOP_N_PER_DISTRICT:
        return f"do następnego etapu przechodzi {rule.top_n} najlepszych wyników w każdym województwie"
    if rule.mode == QualificationMode.HYBRID:
        return (
            f"do następnego etapu przechodzą osoby z wynikiem co najmniej {minimum} pkt, "
            f"mieszczące się jednocześnie w pierwszej {rule.top_n}"
        )
    return ""


def _rank_from_snapshot(publication: ResultsPublication, published_total: Decimal | None) -> int | None:
    """Miejsce uczestnika odczytane z zamrożonej tabeli.

    Wiersze snapshotu są anonimowe i **nie da się** ich przypisać do osoby – i tak ma zostać.
    Miejsce wynika jednak z samej sumy punktów: ``_rank_rows`` nadaje remisom to samo miejsce,
    więc każdy wiersz o danej sumie ma dokładnie ten sam ``rank``. Wystarczy więc znaleźć
    pierwszy wiersz z sumą uczestnika, którą znamy z ``entry_totals``.

    ``None``, gdy sumy nie znamy albo gdy tabela takiej sumy nie ma – nie zgadujemy miejsca.

    Porównanie idzie przez ``to_points``: snapshot niesie ``int`` albo ``float`` (``4.25``),
    a ``Decimal("4.25") == 4.25`` w Pythonie **nie** jest prawdą – dopiero po sprowadzeniu obu
    stron do ``Decimal`` przez tekst liczby są równe tak, jak widzi je człowiek.
    """
    if published_total is None:
        return None
    for row in publication.rows:
        if to_points(row.get("total")) == published_total:
            rank = row.get("rank")
            return int(rank) if isinstance(rank, int) else None
    return None


def _cutoff_from_snapshot(publication: ResultsPublication) -> int | None:
    """Najniższa suma wśród zakwalifikowanych w ogłoszonej tabeli – granica awansu „w praktyce”.

    Opis progu (``describe_threshold``) mówi, jaką regułę ogłosił organizator; ta liczba mówi,
    gdzie ta reguła faktycznie przecięła tabelę. Przy trybie „N najlepszych” to jedyna postać
    progu, którą da się w ogóle podać liczbą.
    """
    totals = [to_points(row.get("total")) for row in publication.rows if row.get("qualified")]
    numbers = [total for total in totals if total is not None]
    return min(numbers) if numbers else None


def _latest_submissions(entry: StageEntry) -> dict[int, Submission]:
    """Najnowsza nieodrzucona wersja pracy dla każdego zadania – jedno zapytanie na cały etap.

    Ta sama reguła, co w ``results.services._latest_submissions``: wersja odrzucona przez
    antywirusa nigdy nie weszła do oceniania, więc liczy się ostatnia wersja przed nią.
    Recenzje dociągamy prefetchem uporządkowanym po rundzie, bo to ta kolejność wyznacza
    podpisy „Recenzent A/B”.
    """
    rows = (
        Submission.objects.filter(entry=entry)
        .exclude(status=SubmissionStatus.REJECTED_INFECTED)
        .select_related("final_grade")
        .prefetch_related(Prefetch("reviews", queryset=Review.objects.order_by("round", "id")))
        .order_by("problem_id", "-version")
    )
    latest: dict[int, Submission] = {}
    for submission in rows:
        latest.setdefault(submission.problem_id, submission)
    return latest


def _reviews_of(submission: Submission | None) -> list[ReviewFeedback]:
    """Komentarze recenzentów do uczestnika. Recenzje bez treści dla niego są pomijane.

    Pominięcie jest istotne dla anonimowości: pusta pozycja „Recenzent B” nie niosłaby żadnej
    informacji poza jedną – ilu recenzentów czytało tę pracę. Numeracja liter idzie po **liście
    wypisanych** recenzji, a nie po numerze rundy, więc z podpisu nie da się odczytać, czy praca
    trafiła do moderacji.
    """
    if submission is None:
        return []
    items: list[ReviewFeedback] = []
    for review in submission.reviews.all():
        if review.status != ReviewStatus.SUBMITTED:
            continue
        comment = (review.comment_for_participant or "").strip()
        annotations = review.public_annotations()
        notes = public_line_notes(review)
        if not comment and not annotations and not notes:
            continue
        items.append(
            ReviewFeedback(
                label=reviewer_label(len(items)),
                comment=comment,
                annotations=annotations,
                line_notes=notes,
            )
        )
    return items


def participant_feedback(participant, stage: Stage) -> StageFeedback | None:
    """Informacja zwrotna uczestnika z jednego etapu albo ``None``, gdy nie ma czego pokazać.

    ``None`` znaczy jedno z trzech: etap nie ma ogłoszonych wyników, uczestnik nie brał w nim
    udziału albo nie ma profilu uczestnika. Widok zamienia wszystkie trzy przypadki na to samo
    404 – rozróżnianie ich w odpowiedzi byłoby wyciekiem informacji o cudzych wpisach i o stanie
    niezakończonej procedury.
    """
    if participant is None:
        return None
    publication = ResultsPublication.objects.filter(stage=stage).first()
    if publication is None:
        return None
    entry = (
        StageEntry.objects.filter(participant=participant, stage=stage)
        .select_related("stage", "stage__edition")
        .first()
    )
    if entry is None:
        return None

    latest = _latest_submissions(entry)
    problems: list[ProblemFeedback] = []
    total = Decimal(0)
    for problem in Problem.objects.filter(stage=stage).order_by("number", "id"):
        submission = latest.get(problem.pk)
        grade = getattr(submission, "final_grade", None) if submission is not None else None
        score = grade.score if grade is not None else Decimal(0)
        total += score
        problems.append(
            ProblemFeedback(
                number=problem.number,
                title=problem.title,
                score=score,
                reviews=_reviews_of(submission),
                missing=submission is None,
            )
        )

    published_total = to_points((publication.entry_totals or {}).get(str(entry.pk)))
    return StageFeedback(
        stage=entry.stage,
        publication=publication,
        entry=entry,
        problems=problems,
        total=total,
        published_total=published_total,
        rank=_rank_from_snapshot(publication, published_total),
        rank_of=len(publication.rows),
        qualified=entry.status == StageEntryStatus.QUALIFIED,
        status_label=entry.get_status_display(),
        threshold=describe_threshold(stage),
        cutoff_total=_cutoff_from_snapshot(publication),
    )
