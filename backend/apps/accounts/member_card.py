"""Dane karty członka komisji i listy członków komisji (panel koordynatora).

Po co osobny moduł, skoro te same liczby pokazuje już ekran postępu etapu: bo tamten odpowiada na
pytanie „jak idzie **ten etap**”, a koordynator dzwoniący do recenzenta ma inne – „co ta osoba ma
na głowie i co z nią zrobić”. Odpowiedź jest rozsypana po pięciu ekranach (konta, przydziały,
postęp, kalibracja, zgłoszenia), więc karta zbiera ją w jedno miejsce i dokłada czynności, które
z tej odpowiedzi wynikają (odbierz pracę, popraw punkty, przydziel, dodaj regułę).

Moduł mieszka w ``apps.accounts``, bo jego przedmiotem jest **człowiek w komitecie**, a nie etap
ani ocenianie. Importy z ``apps.grading`` i ``apps.competitions`` są dlatego lokalne, wewnątrz
funkcji: ``grading.models`` importuje ``accounts.models`` już przy starcie aplikacji, więc import
na górze tego pliku zamknąłby cykl. Ta sama zasada obowiązuje w widokach panelu.

Karta liczy wszystko z **jednego** pobrania recenzji tej osoby (``_member_reviews``): obciążenie
per etap, czas pracy, zaległości i tabela recenzji to cztery widoki na ten sam zbiór, a nie cztery
niezależne pytania do bazy. Dzięki temu koszt karty nie rośnie z liczbą etapów w edycji.
"""

from __future__ import annotations

from django.db.models import Count, Max, Q
from django.utils import timezone

from apps.accounts.anonymised import anonymised_q
from apps.core.links import participant_card

from .models import CommitteeMember

#: Ile wpisów śladu audytowego pokazuje sekcja „Historia”. Pięćdziesiąt to tyle, ile mieści się
#: na ekranie bez zamieniania karty w przeglądarkę audytu – pełna historia ma własny ekran
#: (``/coordinator/audit/``) z filtrami i stronicowaniem.
AUDIT_LIMIT = 50

#: Typy obiektów audytu, które opisują **tę osobę**: konto i profil członka komitetu. Wpisy
#: o recenzjach i pracach tu nie wchodzą – dotyczą prac, a nie człowieka, i przy recenzencie
#: z setką ocen zasłoniłyby wszystko, co o nim samym postanowiono.
AUDIT_TARGET_TYPES = ("accounts.user", "accounts.committeemember")

#: Ile prac czekających na przydział pokazuje sekcja „Przydziel pracę” – na jeden etap.
#: Trzydzieści to tyle, ile da się przejrzeć wzrokiem; komplet prac etapu ma własny ekran.
ASSIGNABLE_LIMIT = 30


def _is_overdue(review, now) -> bool:
    """Czy recenzja jest zaległa – ta sama reguła, co ``grading.reports.overdue_filter``.

    Powtórzenie jest w Pythonie, a nie kolejnym zapytaniem z ``Q``, bo karta ma już całą listę
    recenzji w pamięci; drugie przejście po bazie liczyłoby to samo raz jeszcze. Reguła jest
    czytana z ``reports`` (stałe, obecność pola ``due_at``), więc nie może się z nią rozjechać.
    """
    from datetime import timedelta

    from apps.grading.reports import FALLBACK_OVERDUE_DAYS, PENDING_REVIEW_STATUSES, review_has_due_at

    if review.status not in PENDING_REVIEW_STATUSES:
        return False
    if review_has_due_at():
        due_at = review.due_at
        return due_at is not None and due_at < now
    return review.assigned_at < now - timedelta(days=FALLBACK_OVERDUE_DAYS)


def _work_seconds(review) -> int:
    """Zmierzony czas pracy nad recenzją albo 0, gdy modułu pomiaru w tej instalacji nie ma."""
    try:
        from apps.grading.worklog import review_seconds
    except ImportError:  # pragma: no cover - moduł pomiaru jest opcjonalny
        return 0
    return review_seconds(review)


def format_work_time(seconds: int) -> str:
    """Czas pracy jako napis („1 h 12 min”). Bez modułu pomiaru zwraca pustą wartość.

    Pusty napis, a nie „0 s”: brak pomiaru i zmierzone zero to dwie różne rzeczy, a szablon
    odróżnia je samą obecnością tekstu.
    """
    try:
        from apps.grading.worklog import format_duration
    except ImportError:  # pragma: no cover - moduł pomiaru jest opcjonalny
        return ""
    return format_duration(seconds)


def _member_reviews(member: CommitteeMember) -> list:
    """Wszystkie recenzje tej osoby, z kompletem danych potrzebnych karcie – jedno zapytanie.

    ``select_related`` sięga aż do skali punktacji etapu, bo przy każdej recenzji stoi lista
    wyboru punktów („Zmień punkty”), a skala bywa nadpisana przy zadaniu. Bez tego karta robiłaby
    dwa zapytania na wiersz: jedno po zadanie, drugie po skalę jego etapu.
    """
    from apps.grading.models import Review

    return list(
        Review.objects.filter(reviewer=member)
        .select_related(
            "submission",
            "submission__entry",
            "submission__entry__stage",
            "submission__entry__stage__edition",
            "submission__entry__stage__scoring_scale",
            "submission__entry__participant",
            "submission__problem",
            "work_log",
        )
        .order_by("-assigned_at", "-id")
    )


def _scale_values(stage, problem) -> list[int]:
    """Dopuszczalne punkty dla tego zadania – pusta lista, gdy etap nie ma skali.

    Brak skali nie może wywrócić karty: znika wtedy sam formularz korekty punktów, a nie strona.
    """
    from apps.core.api import DomainError
    from apps.grading.services import allowed_scores

    try:
        return sorted(allowed_scores(stage, problem))
    except DomainError:
        return []


def _workload(reviews: list, now) -> list[dict]:
    """Obciążenie w rozbiciu na etapy: liczniki stanów, zaległości i zmierzony czas pracy.

    Etapy idą od najnowszego, bo pytanie „co ta osoba ma teraz na głowie” dotyczy etapu bieżącego,
    a zeszłoroczny finał jest tłem. Anulowane recenzje mają własny licznik i nie wchodzą do żadnego
    innego: cofnięty przydział nie jest ani robotą do zrobienia, ani oceną wystawioną.
    """
    from apps.grading.models import ReviewStatus

    by_stage: dict[int, dict] = {}
    for review in reviews:
        stage = review.submission.entry.stage
        row = by_stage.setdefault(
            stage.pk,
            {
                "stage": stage,
                "assigned": 0,
                "draft": 0,
                "submitted": 0,
                "cancelled": 0,
                "overdue": 0,
                "seconds": 0,
            },
        )
        if review.status == ReviewStatus.ASSIGNED:
            row["assigned"] += 1
        elif review.status == ReviewStatus.DRAFT:
            row["draft"] += 1
        elif review.status == ReviewStatus.SUBMITTED:
            row["submitted"] += 1
        elif review.status == ReviewStatus.CANCELLED:
            row["cancelled"] += 1
        if _is_overdue(review, now):
            row["overdue"] += 1
        row["seconds"] += _work_seconds(review)
    rows = sorted(by_stage.values(), key=lambda row: (row["stage"].opens_at, row["stage"].pk), reverse=True)
    for row in rows:
        row["work_time"] = format_work_time(row["seconds"]) if row["seconds"] else ""
    return rows


def _review_rows(reviews: list, now) -> list[dict]:
    """Tabela „Recenzje”: jeden wiersz na recenzję, z kodem uczestnika i listą punktów do korekty."""
    from apps.competitions.scoring import coordinator_score_widget, safe_score_rule

    rows = []
    # Skalę liczymy raz na zadanie, a nie raz na wiersz: recenzent finału ma kilkadziesiąt recenzji
    # z tych samych kilku zadań, a pierwszeństwo „skala zadania przed skalą etapu” jest przy każdym
    # z nich takie samo.
    scales: dict[int, list[int]] = {}
    # Pole punktów w trybie etapu (wydanie 0.35.0) – lista skali albo pole liczbowe z zakresem.
    widgets: dict[int, dict | None] = {}
    for review in reviews:
        submission = review.submission
        participant = submission.entry.participant
        stage = submission.entry.stage
        if submission.problem_id not in scales:
            scales[submission.problem_id] = _scale_values(stage, submission.problem)
            widgets[submission.problem_id] = coordinator_score_widget(
                safe_score_rule(stage, submission.problem)
            )
        seconds = _work_seconds(review)
        rows.append(
            {
                "review": review,
                "stage": stage,
                "problem": submission.problem,
                "submission": submission,
                "public_code": participant.public_code,
                "participant_url": participant_card(participant),
                "overdue": _is_overdue(review, now),
                "scale_values": scales[submission.problem_id],
                "score_widget": widgets[submission.problem_id],
                "work_time": format_work_time(seconds) if seconds else "",
            }
        )
    return rows


def _assignment_forms(reviews: list) -> list[dict]:
    """Prace, które wolno tej osobie przydzielić – po jednej liście na etap bieżącej edycji.

    Zakres to **bieżąca edycja**, a nie cała historia: przydział zeszłorocznej pracy nie jest
    czynnością, którą ktokolwiek wykonuje, a lista wyboru z tysiącem pozycji przestaje być listą.
    Prace, które ta osoba już recenzuje, są pominięte – serwis i tak by ich nie przyjął, a pozycja
    nie do wybrania w liście wyboru jest zaproszeniem do komunikatu o błędzie. Pominięcie liczymy
    z listy recenzji, którą karta i tak ma w pamięci, a nie kolejnym zapytaniem.

    Lista jest **ucięta** do ``ASSIGNABLE_LIMIT`` pozycji na etap. Powód jest dwojaki: istniejący
    adres przydziału niesie identyfikator pracy w ścieżce (``…/submissions/<id>/assign-reviewer/``),
    więc każda pozycja musi być osobnym formularzem, a etap eliminacyjny ma tych prac tysiące –
    karta zamieniłaby się w kilometr przycisków. Przydział hurtowy ma zresztą swój ekran
    (przydziały etapu) i karta do niego odsyła; tutaj chodzi o dołożenie komuś kilku prac.
    """
    from apps.competitions.services import current_edition
    from apps.grading.models import ReviewStatus
    from apps.grading.services import _assignable_submissions

    edition = current_edition()
    if edition is None:
        return []
    taken = {review.submission_id for review in reviews if review.status != ReviewStatus.CANCELLED}
    forms = []
    for stage in edition.stages.order_by("opens_at", "id"):
        items = [
            {
                "submission": submission,
                "label": (
                    f"{submission.entry.participant.public_code} – "
                    f"zad. {submission.problem.number} (wersja {submission.version})"
                ),
            }
            for submission in _assignable_submissions(stage)
            if submission.pk not in taken
        ]
        if items:
            forms.append(
                {
                    "stage": stage,
                    "submissions": items[:ASSIGNABLE_LIMIT],
                    "hidden": max(0, len(items) - ASSIGNABLE_LIMIT),
                }
            )
    return forms


def _rules(member: CommitteeMember) -> dict:
    """Reguły „to zadanie recenzuje ta osoba” plus zadania, dla których da się jeszcze taką dodać.

    Kandydatami są zadania **etapów niezamkniętych** bieżącej edycji: reguła dopisuje recenzje do
    prac już zablokowanych i do tych, które wejdą później, więc w etapie zamkniętym nie ma czego
    dopisywać. Zadania, dla których reguła już istnieje, wypadają z listy – druga taka sama reguła
    jest niemożliwa (unikalność w bazie), a pozycja nie do wybrania byłaby pułapką.
    """
    from apps.competitions.models import Problem
    from apps.competitions.services import current_edition
    from apps.grading.models import ProblemReviewerRule

    rules = list(
        ProblemReviewerRule.objects.filter(reviewer=member)
        .select_related("problem", "problem__stage")
        .order_by("problem__stage__opens_at", "problem__number", "id")
    )
    edition = current_edition()
    if edition is None:
        return {"rows": rules, "candidates": []}
    taken = {rule.problem_id for rule in rules}
    candidates = [
        problem
        for problem in Problem.objects.filter(stage__edition=edition, stage__closed_at__isnull=True)
        .select_related("stage")
        .order_by("stage__opens_at", "number", "id")
        if problem.pk not in taken
    ]
    return {"rows": rules, "candidates": candidates}


def _calibration(reviews: list) -> dict | None:
    """Wiersz kalibracyjny tej osoby dla **jednego** etapu – najnowszego z wystawionymi ocenami.

    Jednego, a nie wszystkich: zestawienie kalibracyjne liczy się dla całego etapu naraz (trzeba
    znać oceny drugiego recenzenta i oceny uzgodnione), więc każdy dodatkowy etap to komplet
    zapytań. Pytanie „czy ta osoba ocenia surowo” dotyczy zresztą tego, co robi teraz – a pełne
    zestawienie ma własny ekran przy etapie.

    ``None`` znaczy „nie ma czego pokazać”: brak modułu kalibracji, brak wystawionych ocen albo
    etap, w którym tej osoby nie ma w zestawieniu (np. sama runda rozjemcza).
    """
    try:
        from apps.grading.calibration import stage_calibration
    except ImportError:  # pragma: no cover - moduł kalibracji jest opcjonalny
        return None
    from apps.grading.models import ROUND_BLIND, ReviewStatus

    stages = {
        review.submission.entry.stage.pk: review.submission.entry.stage
        for review in reviews
        if review.status == ReviewStatus.SUBMITTED and review.round == ROUND_BLIND
    }
    if not stages:
        return None
    stage = max(stages.values(), key=lambda item: (item.opens_at, item.pk))
    reviewer_ids = {review.reviewer_id for review in reviews}
    for row in stage_calibration(stage)["rows"]:
        if row.reviewer.pk in reviewer_ids:
            return {"stage": stage, "row": row}
    return None


def _issues(member: CommitteeMember) -> list | None:
    """Zgłoszenia „z tą pracą jest coś nie tak” złożone przez tę osobę. ``None`` = brak modułu."""
    try:
        from apps.grading.models import WorkIssue
    except ImportError:  # pragma: no cover - model zgłoszeń jest opcjonalny
        return None
    return list(
        WorkIssue.objects.filter(review__reviewer=member)
        .select_related("submission", "submission__entry", "submission__problem")
        .order_by("-created_at", "-id")[:AUDIT_LIMIT]
    )


def _audit_entries(member: CommitteeMember) -> list:
    """Ostatnie decyzje zapisane o tym koncie i o tym profilu – bez wpisów o cudzych pracach."""
    from apps.core.models import AuditLog

    user_type, member_type = AUDIT_TARGET_TYPES
    return list(
        AuditLog.objects.filter(
            Q(target_type=user_type, target_id=str(member.user_id))
            | Q(target_type=member_type, target_id=str(member.pk))
        )
        .select_related("actor")
        .order_by("-at", "-id")[:AUDIT_LIMIT]
    )


def _reminder_stages(workload: list[dict]) -> list:
    """Etapy, w których ta osoba ma jeszcze coś do zrobienia – tylko tam przypomnienie ma treść.

    Pusta lista znaczy „nie ma o czym przypominać”: list o zerowej liczbie zaległych recenzji jest
    spamem, a spam od organizatora uczy komitet ignorować jego pocztę (por. ``grading.reports``).
    """
    try:
        from apps.grading.reports import remind_reviewers  # noqa: F401  (sprawdzamy obecność akcji)
    except ImportError:  # pragma: no cover - przypominajka jest opcjonalna
        return []
    return [row for row in workload if row["assigned"] + row["draft"] > 0]


def member_card(member: CommitteeMember, *, now=None) -> dict:
    """Komplet danych karty jednego członka komisji.

    Zwracamy słownik, a nie obiekt: karta jest zestawem niezależnych sekcji, z których każda bywa
    pusta (moduł nieobecny, brak recenzji, etap bez skali), a szablon czyta je po nazwie.
    """
    now = now or timezone.now()
    reviews = _member_reviews(member)
    workload = _workload(reviews, now)
    return {
        "member": member,
        "user": member.user,
        "groups": [group.name for group in member.user.groups.all()],
        "workload": workload,
        "totals": {
            "assigned": sum(row["assigned"] for row in workload),
            "draft": sum(row["draft"] for row in workload),
            "submitted": sum(row["submitted"] for row in workload),
            "overdue": sum(row["overdue"] for row in workload),
            "seconds": sum(row["seconds"] for row in workload),
        },
        "total_work_time": format_work_time(sum(row["seconds"] for row in workload)),
        "reviews": _review_rows(reviews, now),
        "assignment_forms": _assignment_forms(reviews),
        "rules": _rules(member),
        "calibration": _calibration(reviews),
        "issues": _issues(member),
        "audit_entries": _audit_entries(member),
        "reminder_stages": _reminder_stages(workload),
    }


def member_list_rows(
    *, status: str = "", stage_id: int | None = None, now=None, include_deleted: bool = False
) -> list[dict]:
    """Lista wszystkich członków komisji z obciążeniem – materiał na ekran „Komitet → członkowie”.

    Wiersze powstają z **całej** tabeli członków, a nie z recenzji: osoba bez ani jednego przydziału
    jest informacją o rozkładzie pracy, a nie brakiem wiersza. Liczniki dokłada jeden agregat
    pogrupowany po recenzencie, więc koszt nie rośnie z liczbą recenzji.

    ``stage_id`` zawęża **liczniki**, a nie listę osób: pytanie brzmi „ile ta osoba ma w tym
    etapie”, a odpowiedź „zero” jest właśnie tym, czego koordynator szuka, planując dosyłkę.

    Członkowie z kontem usuniętym na żądanie (``apps.accounts.anonymised``) zostają w bazie, gdy
    wystawili recenzje – ale to nie są już osoby, którym się dokłada pracy. Domyślnie ich tu nie
    ma; ``include_deleted=True`` to przełącznik „Pokaż usunięte konta” z ekranu listy.
    """
    from apps.grading.models import Review, ReviewStatus
    from apps.grading.reports import overdue_filter

    now = now or timezone.now()
    members_qs = CommitteeMember.objects.select_related("user").order_by(
        "user__last_name", "user__first_name", "user__email"
    )
    if status:
        members_qs = members_qs.filter(status=status)
    if not include_deleted:
        members_qs = members_qs.exclude(anonymised_q("user"))
    members = list(members_qs)

    reviews = Review.objects.filter(reviewer_id__in=[member.pk for member in members])
    if stage_id:
        reviews = reviews.filter(submission__entry__stage_id=stage_id)
    counters = {
        row["reviewer_id"]: row
        for row in reviews.values("reviewer_id").annotate(
            assigned=Count("id", filter=Q(status=ReviewStatus.ASSIGNED)),
            draft=Count("id", filter=Q(status=ReviewStatus.DRAFT)),
            submitted=Count("id", filter=Q(status=ReviewStatus.SUBMITTED)),
            overdue=Count("id", filter=overdue_filter(now)),
            last_submitted=Max("submitted_at"),
        )
    }
    rows = []
    for member in members:
        counts = counters.get(member.pk, {})
        # „Ostatnia aktywność” to późniejsza z dwóch dat: ostatnie logowanie i ostatnia wystawiona
        # ocena. Samo logowanie bywa przypadkowe, a sama ocena milczy o kimś, kto właśnie zaczął –
        # razem odpowiadają na pytanie „czy ta osoba w ogóle jest z nami w tej edycji”.
        candidates = [value for value in (member.user.last_login, counts.get("last_submitted")) if value]
        rows.append(
            {
                "member": member,
                "assigned": counts.get("assigned", 0),
                "draft": counts.get("draft", 0),
                "submitted": counts.get("submitted", 0),
                "overdue": counts.get("overdue", 0),
                "pending": counts.get("assigned", 0) + counts.get("draft", 0),
                "last_activity": max(candidates) if candidates else None,
            }
        )
    # Najpierw zalegający, potem najbardziej obciążeni – ta sama kolejność, co na ekranie postępu
    # etapu: lista ma odpowiadać na pytanie „do kogo zadzwonić”, a nie „kto jest pierwszy w alfabecie”.
    rows.sort(key=lambda row: (-row["overdue"], -row["pending"], row["member"].user.email))
    return rows
