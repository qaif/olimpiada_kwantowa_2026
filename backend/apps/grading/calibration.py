"""Kalibracja recenzentów: czy ktoś systematycznie punktuje surowiej albo łagodniej od reszty.

Po co ten moduł istnieje. Ocenianie jest ślepe i dwustopniowe, więc pojedynczy rozjazd dwóch ocen
nie mówi nic o żadnym z recenzentów – dwie osoby mogą w dobrej wierze przeczytać rozwiązanie
inaczej. Dopiero **rozkład** tych rozjazdów po wszystkich pracach jednej osoby jest informacją:
recenzent, który w trzydziestu pracach dał średnio o półtora punktu mniej niż drugi recenzent
i niż ocena uzgodniona, nie miał trzydzieści razy pecha. To jest pytanie, które organizator
zadaje po ocenianiu, przygotowując instruktaż na kolejną edycję – i dlatego ekran stoi obok
postępu oceniania, a nie obok akcji prowadzenia zawodów.

Czego ten moduł **nie** robi i robić nie będzie:

- nie ocenia recenzentów i nie wystawia im not. „Surowy” nie znaczy „zły”: przy zadaniu
  z ostrą rubryką surowość bywa po prostu poprawnym czytaniem kryteriów. Liczby są materiałem
  do rozmowy, nie werdyktem,
- nie zmienia ani jednej oceny. Cały moduł jest odczytem; korekta punktów ma własną drogę
  (``apps.grading.services.set_review_score``) z uzasadnieniem i wpisem w audycie,
- nie pokazuje danych uczestników. Wiersz dotyczy recenzenta, a liczby są zagregowane po
  pracach – z tabeli nie da się odczytać, ile punktów dostała konkretna praca.

Odchylenia liczymy **ze znakiem** i to jest cała istota ekranu: średnia z wartości bezwzględnych
zrównałaby recenzenta chaotycznego (raz +2, raz −2) z konsekwentnie surowym (za każdym razem −2),
a to są dwa zupełnie różne problemy i dwie różne rozmowy.

Liczba zapytań jest stała i niezależna od liczby prac w etapie: recenzje rundy 1 i oceny
uzgodnione czytamy hurtem, a średnie składamy w Pythonie. Pairwise odchylenie „wobec drugiego
recenzenta” wymaga zestawienia recenzji **tej samej pracy** ze sobą, czego nie da się wyrazić
jednym ``annotate`` bez samozłączenia; grupowanie w Pythonie jest tu tańsze i czytelniejsze niż
podzapytanie skorelowane na każdą recenzję z osobna.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.accounts.models import CommitteeMember
from apps.competitions.models import Stage
from apps.grading.models import ROUND_BLIND, FinalGrade, Review, ReviewStatus

#: Od jakiego średniego odchylenia wobec oceny uzgodnionej podpisujemy recenzenta „surowy”
#: albo „łagodny”. Pół punktu w skali 0/2/5/6 to różnica, której nie da się wytłumaczyć
#: zaokrągleniem – a zarazem na tyle mało, że próg nie przemilcza realnych tendencji.
TENDENCY_THRESHOLD = 0.5

#: Ile recenzji musi mieć wiersz, żeby w ogóle podpisywać go tendencją. Przy trzech pracach
#: średnia jest opowieścią o trzech pracach, a nie o recenzencie – ekran pokazuje wtedy liczby
#: i jawne „za mało danych”, zamiast nazywać kogoś surowym na podstawie jednego rozjazdu.
MIN_REVIEWS_FOR_TENDENCY = 5

TENDENCY_STRICT = "surowy"
TENDENCY_LENIENT = "łagodny"
TENDENCY_NEUTRAL = "zgodny z komisją"
TENDENCY_UNKNOWN = "za mało danych"

#: Kolumny, po których wolno sortować tabelę. Zamknięty słownik, a nie nazwa pola z adresu:
#: parametr zapytania pochodzi od użytkownika i nie ma prawa sięgać do dowolnego atrybutu.
#: Wartością jest funkcja klucza; ``None`` w danych sortuje się na koniec (patrz ``_sort_key``).
SORT_COLUMNS = ("reviewer", "reviews", "vs_peer", "vs_final", "disagreements")
DEFAULT_SORT = "reviews"


@dataclass(frozen=True)
class CalibrationRow:
    """Jeden recenzent w zestawieniu. Zamrożona struktura – szablon ją wyłącznie czyta."""

    reviewer: CommitteeMember
    #: Wystawione recenzje rundy 1 w tym etapie. Runda 2 (rozjemcza) nie wchodzi: trzeci recenzent
    #: zna już to, że poprzednicy się nie zgodzili, więc jego ocena nie jest niezależna.
    reviews: int
    #: Średnie odchylenie od oceny drugiego recenzenta tej samej pracy. ``None``, gdy żadna
    #: z prac nie miała drugiej oceny (przydział awaryjny ``per_submission=1``).
    mean_vs_peer: float | None
    #: Średnie odchylenie od oceny uzgodnionej. ``None``, gdy żadna z prac nie ma jeszcze
    #: ``FinalGrade`` – czyli gdy ekran otwarto w trakcie oceniania.
    mean_vs_final: float | None
    #: Prace, w których oceny rundy 1 się rozjechały (czyli trafiły do moderacji).
    disagreements: int
    #: Prace, przy których dało się rozjazd w ogóle stwierdzić – mianownik udziału rozjazdów.
    comparable: int

    @property
    def disagreement_share(self) -> float | None:
        """Udział rozjazdów. ``None``, gdy nie było czego porównać – zero byłoby wtedy nieprawdą."""
        if not self.comparable:
            return None
        return self.disagreements / self.comparable

    @property
    def tendency(self) -> str:
        """Podpis „surowy / łagodny / zgodny z komisją”, wyliczony z odchylenia wobec komisji.

        Wobec **oceny uzgodnionej**, a nie wobec drugiego recenzenta: para recenzentów bywa
        zgodnie surowa, a odchylenie między nimi jest wtedy zerowe. Ocena uzgodniona jest
        najbliższym, co system ma do punktu odniesienia „jak oceniła komisja”.
        """
        if self.reviews < MIN_REVIEWS_FOR_TENDENCY or self.mean_vs_final is None:
            return TENDENCY_UNKNOWN
        if self.mean_vs_final <= -TENDENCY_THRESHOLD:
            return TENDENCY_STRICT
        if self.mean_vs_final >= TENDENCY_THRESHOLD:
            return TENDENCY_LENIENT
        return TENDENCY_NEUTRAL


def _mean(values: list[float]) -> float | None:
    """Średnia albo ``None`` dla pustej listy. Zero byłoby tu kłamstwem („odchylenie zerowe”)."""
    if not values:
        return None
    return sum(values) / len(values)


def _submitted_round_one(stage: Stage) -> list[dict]:
    """Wystawione recenzje rundy 1 w etapie – jedno zapytanie, same potrzebne kolumny.

    ``CANCELLED`` odpada razem z resztą stanów (filtr po ``SUBMITTED``): recenzja odebrana
    recenzentowi albo unieważniona nową wersją pracy nie liczy się do oceny końcowej, więc nie
    może też liczyć się do jego kalibracji.
    """
    return list(
        Review.objects.filter(
            submission__entry__stage=stage,
            round=ROUND_BLIND,
            status=ReviewStatus.SUBMITTED,
            score__isnull=False,
        )
        .values("reviewer_id", "submission_id", "score")
        .order_by("submission_id", "id")
    )


def _final_scores(stage: Stage) -> dict[int, int]:
    """Oceny uzgodnione prac etapu: ``{submission_id: score}``. Jedno zapytanie na cały ekran."""
    return dict(
        FinalGrade.objects.filter(submission__entry__stage=stage).values_list("submission_id", "score")
    )


def _reviewers_by_id(reviewer_ids: set[int]) -> dict[int, CommitteeMember]:
    """Profile recenzentów z doczytanym kontem – tożsamość recenzenta widzi wyłącznie koordynator."""
    return {
        member.pk: member
        for member in CommitteeMember.objects.filter(pk__in=reviewer_ids).select_related("user")
    }


def _accumulate(rows: list[dict], finals: dict[int, int]) -> dict[int, dict]:
    """Zbiera surowe liczby per recenzent z recenzji pogrupowanych po pracy.

    Osobna funkcja od budowy wierszy, bo to jedyne miejsce z arytmetyką: dalej są już tylko
    średnie i etykiety. Grupujemy po pracy, bo „odchylenie od drugiego recenzenta” w ogóle nie
    istnieje poza kontekstem jednej pracy.
    """
    by_submission: dict[int, list[dict]] = {}
    for row in rows:
        by_submission.setdefault(row["submission_id"], []).append(row)

    stats: dict[int, dict] = {}

    def bucket(reviewer_id: int) -> dict:
        return stats.setdefault(
            reviewer_id,
            {"reviews": 0, "vs_peer": [], "vs_final": [], "disagreements": 0, "comparable": 0},
        )

    for submission_id, reviews in by_submission.items():
        final = finals.get(submission_id)
        scores = [review["score"] for review in reviews]
        # Rozjazd stwierdzamy dopiero przy **dwóch** ocenach tej samej pracy: jedna ocena nie ma
        # z czym się rozjechać, a liczenie jej jako „zgodnej” zawyżałoby zgodność recenzenta,
        # który dostał same prace z przydziału awaryjnego.
        disagreed = len(reviews) > 1 and len(set(scores)) > 1
        for review in reviews:
            entry = bucket(review["reviewer_id"])
            entry["reviews"] += 1
            if len(reviews) > 1:
                entry["comparable"] += 1
                if disagreed:
                    entry["disagreements"] += 1
                # Przy trzech ocenach rundy 1 (dosyłka po odebraniu pracy) porównujemy ze
                # średnią pozostałych – „drugi recenzent” jest wtedy zbiorem, a nie osobą.
                others = [other["score"] for other in reviews if other is not review]
                entry["vs_peer"].append(review["score"] - sum(others) / len(others))
            if final is not None:
                entry["vs_final"].append(review["score"] - final)
    return stats


def _sort_key(column: str):
    """Klucz sortowania dla jednej kolumny. Puste wartości zawsze na końcu, niezależnie od kierunku.

    Wiersz bez odchylenia („żadna praca nie ma jeszcze oceny uzgodnionej”) nie jest ani najlepszy,
    ani najgorszy – jest nieznany. Wrzucenie go na górę przy sortowaniu rosnącym sugerowałoby
    recenzenta idealnie zgodnego z komisją.
    """

    def key(row: CalibrationRow):
        if column == "reviewer":
            return (0, row.reviewer.user.email.lower())
        if column == "reviews":
            return (0, row.reviews)
        if column == "disagreements":
            share = row.disagreement_share
            return (1, 0.0) if share is None else (0, share)
        value = row.mean_vs_peer if column == "vs_peer" else row.mean_vs_final
        return (1, 0.0) if value is None else (0, value)

    return key


def sort_rows(rows: list[CalibrationRow], sort: str | None) -> tuple[list[CalibrationRow], str, bool]:
    """Sortuje tabelę według parametru z adresu. Zwraca ``(wiersze, kolumna, malejąco)``.

    Parametr ma postać ``kolumna`` albo ``-kolumna`` – ta sama konwencja, co w ``order_by`` ORM-a,
    więc odnośnik nagłówka tabeli jest zwykłym przełączeniem minusa. Nieznana nazwa cicho spada
    do porządku domyślnego: adres wklejony z czyjejś wiadomości nie może kończyć się błędem 500,
    a „posortowano inaczej, niż prosiłeś” jest widoczne od razu na ekranie.
    """
    raw = (sort or "").strip()
    descending = raw.startswith("-")
    column = raw.lstrip("-")
    if column not in SORT_COLUMNS:
        column, descending = DEFAULT_SORT, True
    ordered = sorted(rows, key=_sort_key(column), reverse=descending)
    return ordered, column, descending


def stage_calibration(stage: Stage, *, sort: str | None = None) -> dict:
    """Zestawienie kalibracyjne całego etapu: wiersz na recenzenta plus stan sortowania.

    Zwracamy słownik, a nie samą listę, bo ekran musi wiedzieć **także**, po czym posortowano
    (żeby podświetlić nagłówek i zbudować odnośniki odwracające kierunek) oraz ile prac w ogóle
    ma już ocenę uzgodnioną – bez tej liczby puste kolumny odchyleń wyglądałyby na awarię,
    a są zwyczajnym „ocenianie jeszcze trwa”.
    """
    rows = _submitted_round_one(stage)
    finals = _final_scores(stage)
    stats = _accumulate(rows, finals)
    reviewers = _reviewers_by_id(set(stats))
    table = [
        CalibrationRow(
            reviewer=reviewers[reviewer_id],
            reviews=entry["reviews"],
            mean_vs_peer=_mean(entry["vs_peer"]),
            mean_vs_final=_mean(entry["vs_final"]),
            disagreements=entry["disagreements"],
            comparable=entry["comparable"],
        )
        for reviewer_id, entry in stats.items()
        if reviewer_id in reviewers
    ]
    ordered, column, descending = sort_rows(table, sort)
    return {
        "rows": ordered,
        "sort": column,
        "descending": descending,
        "graded": len(finals),
        # Prace z oceną uzgodnioną **spośród tych, które ktokolwiek recenzował**: to jest
        # mianownik, którego czytelnik szuka, patrząc na puste kolumny odchyleń.
        "reviewed": len({row["submission_id"] for row in rows}),
    }
