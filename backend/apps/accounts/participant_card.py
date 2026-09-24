"""Karta uczestnika: wszystko, co organizator wie o jednej osobie, złożone w jednym odczycie.

Po co osobny moduł, skoro dane leżą w pięciu aplikacjach. Bo dzisiaj odpowiedź na jedno pytanie
uczestnika („dlaczego nie przeszedłem dalej?”, „czy moja praca w ogóle doszła?”) wymaga obejścia
sześciu ekranów panelu: konta, przydziałów etapu, moderacji, reklamacji, wyników i audytu – a na
każdym z nich trzeba wpisać ten sam kod publiczny i za każdym razem wierzyć, że trafiło się na tę
samą osobę. Karta zbiera to w jedno miejsce i **niczego nie liczy sama**: każda liczba, status
i decyzja pochodzi z tego samego źródła, co ekran, na którym powstała.

Trzy zasady, które ten moduł trzyma:

- **wyłącznie odczyt.** Nie ma tu ani jednego zapisu. Czynności (przydział recenzenta, korekta
  punktów, ocena końcowa, blokada do oceny) wykonują istniejące widoki-akcje koordynatora; karta
  pokazuje wyłącznie formularze, które na nie celują. Dzięki temu reguła domenowa ma nadal jedno
  miejsce, a karta nie może się z nią rozjechać,
- **stała liczba zapytań.** Uczestnik finału ma kilkanaście prac w kilku wersjach, do tego
  recenzje, reklamacje i audyt – wersja „po jednym zapytaniu na wiersz” zamieniłaby tę stronę
  w kilkaset zapytań. Wszystko jedzie pakietami: wpisy, prace z plikami, recenzje, oceny końcowe,
  reklamacje, dyplomy i audyt to po jednym zapytaniu na rodzaj, niezależnie od liczby wierszy,
- **cudze dane tu nie wchodzą.** Każde zapytanie jest zawężone kluczem tego uczestnika. Jedynym
  wyjątkiem jest pula recenzentów do formularza przydziału – ta sama lista, co na ekranie
  przydziałów etapu, i z tego samego serwisu.

Importy aplikacji „nad” kontami (zawody, rozwiązania, ocenianie, wyniki, reklamacje) są lokalne
w funkcjach, tak samo jak w ``apps.accounts.data_export`` i z tego samego powodu: ``apps.accounts``
ładuje się przed nimi, więc import na poziomie modułu byłby cyklem przy starcie procesu.
"""

from __future__ import annotations

from django.apps import apps as django_apps
from django.db.models import Prefetch, Q
from django.urls import NoReverseMatch, reverse

from apps.core.models import AuditLog

from .guardian import guardian_status
from .models import Participant

#: Ile wpisów audytu pokazuje karta. Pięćdziesiąt, bo strona ma dać **obraz ostatnich zdarzeń**,
#: a nie zastąpić przeglądarkę audytu – pełną historię z filtrami ma ``/coordinator/audit/``,
#: do której karta prowadzi odnośnikiem.
AUDIT_LIMIT = 50

#: Typy obiektów audytu, które dotyczą tej osoby. Zapisane wprost, bo wpis audytowy trzyma typ
#: jako tekst ``app.model`` (``apps.core.models.audit``) i nie ma po czym dojść do niego z modelu.
TARGET_USER = "accounts.user"
TARGET_PARTICIPANT = "accounts.participant"
TARGET_SUBMISSION = "submissions.submission"

#: Nazwa aplikacji zgłoszeń pomocy. Sekcja „Zgłoszenia” pojawia się na karcie dopiero wtedy, gdy
#: aplikacja **istnieje i jest zainstalowana** – dopóki jej nie ma, karta nie może o niej kłamać.
SUPPORT_APP = "apps.support"

#: Nazwa adresu sprawy w panelu koordynatora. Rozwiązywana w locie i z zabezpieczeniem, bo należy
#: do ekranu zgłoszeń, a nie do karty: brak tego adresu ma zabrać odnośnik, a nie wywrócić stronę.
SUPPORT_DETAIL_URL_NAME = "web:coordinator-support-detail"


def card_queryset():
    """Zapytanie, z którego widok bierze uczestnika karty – konto i szkoła ze słownika w jednym odczycie.

    Osobno, a nie w widoku, bo lista ``select_related`` jest częścią obietnicy „stała liczba
    zapytań”: bez niej samo wypisanie nagłówka strony kosztowałoby dwa dodatkowe zapytania.
    """
    return Participant.objects.select_related("user", "school_ref")


def support_tickets(user) -> list[dict] | None:
    """Zgłoszenia pomocy tej osoby albo ``None``, gdy aplikacji zgłoszeń w tej instalacji nie ma.

    ``None`` znaczy „nie ma czego pokazać, bo nie ma takiej funkcji”, i różni się od pustej listy,
    która znaczy „ta osoba nie zgłaszała niczego”. Karta na tej różnicy opiera decyzję, czy sekcja
    „Zgłoszenia” w ogóle się renderuje – pusty nagłówek „Zgłoszenia: brak” przy nieistniejącym
    module obiecywałby, że zgłoszeń po prostu nie było.

    Dwie bramki, bo są to dwa różne braki: aplikacji może nie być w ``INSTALLED_APPS`` (wtedy jej
    tabel nie ma w bazie, choć import modelu by się udał), albo może w ogóle nie mieć modeli.

    Adres sprawy rozwiązujemy tutaj, a nie w szablonie, i z zabezpieczeniem: ekran zgłoszeń należy
    do innej części panelu, a karta ma przeżyć jego przebudowę bez odnośnika, a nie wywalić się
    na ``NoReverseMatch`` w środku renderowania.
    """
    if not django_apps.is_installed(SUPPORT_APP):
        return None
    try:
        from apps.support.models import SupportTicket
    except ImportError:
        return None
    tickets = SupportTicket.objects.filter(user=user).order_by("-created_at", "-id")[:AUDIT_LIMIT]
    rows = []
    for ticket in tickets:
        try:
            url = reverse(SUPPORT_DETAIL_URL_NAME, args=[ticket.pk])
        except NoReverseMatch:
            url = ""
        rows.append({"ticket": ticket, "url": url})
    return rows


def _entries(participant: Participant) -> list:
    """Wpisy uczestnika do etapów, od najstarszego etapu – po jednym zapytaniu na całą kartę.

    ``select_related`` obejmuje wszystko, co stoi w nagłówku sekcji etapu: sam etap z edycją,
    skalę punktacji (listy wyboru ocen), próg kwalifikacji (reguła, wobec której czyta się decyzję
    ręczną), ogłoszoną tabelę wyników i zapis na rozmowę razem z terminem. Każda z tych relacji
    jest „jeden do jednego” po stronie etapu, więc doklejenie jej nie zwielokrotnia wierszy.
    """
    from apps.competitions.models import StageEntry

    return list(
        StageEntry.objects.filter(participant=participant)
        .select_related(
            "stage",
            "stage__edition",
            "stage__scoring_scale",
            "stage__qualification_rule",
            "stage__results_publication",
            "manual_qualified_by",
            "interview_booking",
            "interview_booking__slot",
        )
        .order_by("stage__opens_at", "stage_id")
    )


def _submissions(entries: list) -> list:
    """Wszystkie wersje wszystkich prac tych wpisów, od najnowszej wersji w obrębie zadania.

    Karta pokazuje **każdą** wersję, a nie tylko najnowszą, i to jest jej sens w tym miejscu:
    pytanie „czy praca doszła na czas” rozstrzyga się na wersji, którą uczestnik wysłał przed
    deadlinem, a nie na tej, którą podmienił potem. Pliki jadą prefetchem posortowanym malejąco
    po kluczu, bo dokładnie tego porządku oczekuje ``Submission.latest_file`` – inaczej każdy
    wiersz odpytałby bazę osobno.
    """
    from apps.submissions.models import Submission, SubmissionFile

    if not entries:
        return []
    return list(
        Submission.objects.filter(entry__in=entries)
        .select_related("problem")
        .prefetch_related(Prefetch("files", queryset=SubmissionFile.objects.order_by("-id")))
        .order_by("entry_id", "problem__number", "problem_id", "-version")
    )


def _reviews_by_submission(submission_ids: list[int]) -> dict[int, list]:
    """Recenzje pogrupowane po pracy. Anulowane też – cofnięty przydział jest informacją."""
    from apps.grading.models import Review

    grouped: dict[int, list] = {}
    if not submission_ids:
        return grouped
    for review in (
        Review.objects.filter(submission_id__in=submission_ids)
        .select_related("reviewer", "reviewer__user")
        .order_by("round", "id")
    ):
        grouped.setdefault(review.submission_id, []).append(review)
    return grouped


def _grades_by_submission(submission_ids: list[int]) -> dict[int, object]:
    from apps.grading.models import FinalGrade

    if not submission_ids:
        return {}
    grades = FinalGrade.objects.select_related("decided_by").filter(submission_id__in=submission_ids)
    return {grade.submission_id: grade for grade in grades}


def _appeals(participant: Participant) -> list:
    """Reklamacje tej osoby wraz z rozstrzygnięciem – od najnowszej.

    ``filed_by`` zamiast drogi przez rozwiązania: reklamację składa uczestnik i to on jest jej
    stroną, a praca bywa później podmieniona albo przeniesiona między wersjami.
    """
    from apps.appeals.models import Appeal

    return list(
        Appeal.objects.filter(filed_by=participant)
        .select_related(
            "submission",
            "submission__problem",
            "submission__entry",
            "submission__entry__stage",
            "decision",
            "decision__decided_by",
        )
        .order_by("-filed_at", "-id")
    )


def _certificates(participant: Participant) -> list:
    """Dyplomy i zaświadczenia wystawione na wpisy tej osoby (dokumenty opiekuna tu nie należą)."""
    from apps.results.models import Certificate

    return list(
        Certificate.objects.filter(entry__participant=participant)
        .select_related("edition", "entry", "entry__stage")
        .order_by("-issued_at", "-id")
    )


def _audit(participant: Participant, submission_ids: list[int]) -> list[AuditLog]:
    """Ostatnie wpisy audytu, w których ta osoba jest wykonawcą **albo** przedmiotem.

    Obie role są tu potrzebne i znaczą co innego: „uczestnik oddał pracę” to jego czynność,
    a „koordynator poprawił dane konta” – czynność na nim. Strona, która pokazywałaby tylko
    jedną z nich, odpowiadałaby na połowę pytania „co się z tą sprawą działo”.

    Identyfikatory są porównywane jako tekst, bo ``AuditLog.target_id`` jest polem tekstowym
    (wpis audytowy musi przeżyć skasowanie obiektu, na który wskazuje).
    """
    condition = (
        Q(actor_id=participant.user_id)
        | Q(target_type=TARGET_USER, target_id=str(participant.user_id))
        | Q(target_type=TARGET_PARTICIPANT, target_id=str(participant.pk))
    )
    if submission_ids:
        condition |= Q(target_type=TARGET_SUBMISSION, target_id__in=[str(pk) for pk in submission_ids])
    entries = AuditLog.objects.filter(condition).select_related("actor").order_by("-at", "-id")
    return list(entries[:AUDIT_LIMIT])


def _published_result(entry) -> dict | None:
    """Wiersz tej osoby w ogłoszonej tabeli etapu albo ``None``, gdy wyników jeszcze nie ma.

    Suma pochodzi z ``ResultsPublication.entry_totals`` – z mapy zamrożonej w chwili publikacji,
    a nie z bieżącego ``StageEntry.total_points``. To jest cała różnica, po którą się tu sięga:
    korekta oceny po ogłoszeniu zmienia sumę bieżącą, ale **nie** ogłoszony dokument, a uczestnik
    pyta o to, co widzi na stronie wyników.

    Miejsce odczytujemy ze snapshotu po sumie, bo wiersze ogłoszonej tabeli są z założenia
    anonimowe (kod uczestnika widnieje tam tylko w trybie ``CODE``). To nie jest zgadywanie:
    ``_rank_rows`` nadaje miejsce wyłącznie na podstawie sumy i remis dostaje to samo miejsce,
    więc wszystkie wiersze o równej sumie mają równe miejsce. Brak sumy znaczy, że wpisu nie było
    w ogłoszonej tabeli (np. dyskwalifikacja albo kwalifikacja po publikacji) – i tak to mówimy.

    Kwalifikację bierzemy ze statusu wpisu, a nie ze snapshotu: przy równej sumie dwóch osób
    jedna może być zakwalifikowana decyzją komitetu, a druga nie, więc dopasowanie po sumie
    dałoby tu odpowiedź nieprawdziwą.
    """
    from apps.competitions.models import StageEntryStatus

    publication = getattr(entry.stage, "results_publication", None)
    if publication is None:
        return None
    from apps.core.points import to_points

    totals = publication.entry_totals if isinstance(publication.entry_totals, dict) else {}
    # ``to_points`` po obu stronach: snapshot sprzed 0.35.0 niesie ``int``, nowszy także ``float``
    # (4.25), a porównanie ma być porównaniem liczb, a nie typów JSON-a.
    total = to_points(totals.get(str(entry.pk)))
    rank = None
    if total is not None:
        rank = next(
            (row.get("rank") for row in publication.rows if to_points(row.get("total")) == total), None
        )
    return {
        "publication": publication,
        "in_table": total is not None,
        "total": total,
        "rank": rank,
        "qualified": entry.status == StageEntryStatus.QUALIFIED,
    }


def _problem_blocks(entry, submissions: list, reviews: dict, grades: dict, appeals: dict) -> list[dict]:
    """Prace jednego wpisu pogrupowane po zadaniu: wszystkie wersje plus obieg oceny najnowszej.

    Ocena, recenzje, reklamacja i formularze czynności wiszą **przy najnowszej wersji**, bo to ona
    jest oceniana – wersja starsza jest w tej tabeli dokumentem („tak wyglądało oddanie z 3 marca”),
    a nie przedmiotem decyzji.

    ``assignable`` i ``lockable`` powtarzają warunki z ekranu przydziałów etapu
    (``apps.grading.services.stage_assignment_rows``) i tak samo jak tam **nie są bramką**:
    rozstrzyga serwis. Tutaj decydują wyłącznie o tym, czy stawiać formularz, którego zapis
    i tak by odmówił.
    """
    from apps.competitions.scoring import coordinator_score_widget, safe_score_rule
    from apps.grading.services import scale_items
    from apps.submissions.models import AvStatus, SubmissionStatus

    by_problem: dict[int, list] = {}
    for submission in submissions:
        by_problem.setdefault(submission.problem_id, []).append(submission)

    blocks = []
    for problem_id, versions in by_problem.items():
        problem = versions[0].problem
        latest = versions[0]
        rows = []
        for submission in versions:
            file = submission.latest_file
            rows.append(
                {
                    "submission": submission,
                    "file": file,
                    # Ta sama reguła, co w ``SubmissionDownloadView``: plik przed czystym skanem
                    # pobiera wyłącznie jego autor. Odnośnik, który obiecywałby pobranie kończące
                    # się odmową, byłby gorszy niż jego brak.
                    "downloadable": file is not None and file.av_status == AvStatus.CLEAN,
                    "scan_pending": file is not None and file.av_status != AvStatus.CLEAN,
                }
            )
        blocks.append(
            {
                "problem": problem,
                "problem_id": problem_id,
                "versions": rows,
                "latest": latest,
                "reviews": reviews.get(latest.pk, []),
                "final_grade": grades.get(latest.pk),
                "appeals": appeals.get(latest.pk, []),
                "scale_items": scale_items(entry.stage, problem),
                # Tryb etapu (wydanie 0.35.0): w etapie z dowolnymi wartościami formularze korekt
                # mają pole liczbowe z zakresem zadania zamiast listy skali.
                "score_widget": coordinator_score_widget(safe_score_rule(entry.stage, problem)),
                "assignable": latest.status in (SubmissionStatus.LOCKED, SubmissionStatus.IN_REVIEW),
                "lockable": latest.status == SubmissionStatus.SUBMITTED,
            }
        )
    return sorted(blocks, key=lambda block: (block["problem"].number, block["problem_id"]))


def participant_card(participant: Participant) -> dict:
    """Komplet danych karty jednej osoby – gotowy kontekst szablonu.

    Kolejność kluczy odpowiada kolejności sekcji na stronie, bo to ona jest tu umową z szablonem:
    dane, zgody, etapy i prace, wyniki, reklamacje, zgłoszenia, audyt.
    """
    from apps.grading.models import ReviewStatus
    from apps.grading.services import reviewer_pool

    entries = _entries(participant)
    submissions = _submissions(entries)
    by_entry: dict[int, list] = {}
    for submission in submissions:
        by_entry.setdefault(submission.entry_id, []).append(submission)

    # Recenzje, oceny i reklamacje dotyczą wyłącznie najnowszej wersji każdego zadania – starsze
    # wersje są w karcie dokumentem, nie przedmiotem oceny. Zawężenie zapytań do tych prac jest
    # więc jednocześnie zawężeniem do tego, co naprawdę pokazujemy.
    latest_ids: list[int] = []
    for entry_submissions in by_entry.values():
        seen: set[int] = set()
        for submission in entry_submissions:
            if submission.problem_id not in seen:
                seen.add(submission.problem_id)
                latest_ids.append(submission.pk)

    reviews = _reviews_by_submission(latest_ids)
    grades = _grades_by_submission(latest_ids)
    appeals = _appeals(participant)
    appeals_by_submission: dict[int, list] = {}
    for appeal in appeals:
        appeals_by_submission.setdefault(appeal.submission_id, []).append(appeal)
    certificates = _certificates(participant)
    certificates_by_entry: dict[int, list] = {}
    for certificate in certificates:
        certificates_by_entry.setdefault(certificate.entry_id, []).append(certificate)

    stages = [
        {
            "entry": entry,
            "stage": entry.stage,
            "rule": getattr(entry.stage, "qualification_rule", None),
            "booking": getattr(entry, "interview_booking", None),
            "problems": _problem_blocks(
                entry, by_entry.get(entry.pk, []), reviews, grades, appeals_by_submission
            ),
            "result": _published_result(entry),
            "certificates": certificates_by_entry.get(entry.pk, []),
        }
        for entry in entries
    ]

    return {
        "participant": participant,
        "account": participant.user,
        "guardian": guardian_status(participant),
        "consents": list(participant.consents.all()),
        "stages": stages,
        "certificates": certificates,
        "appeals": appeals,
        # ``None`` = aplikacji zgłoszeń jeszcze nie ma, więc sekcja się nie renderuje.
        "tickets": support_tickets(participant.user),
        "audit_entries": _audit(participant, [submission.pk for submission in submissions]),
        # Materiał do formularzy czynności. Pula recenzentów jest ta sama, co na ekranie
        # przydziałów etapu – lista wyboru nie może pokazywać osoby, której serwis nie przepuści.
        "reviewer_pool": reviewer_pool(),
        "assigned_status": ReviewStatus.ASSIGNED,
        # Odebrać wolno recenzję w każdym stanie poza anulowaną – także wystawioną. Czy w tej
        # konkretnej sprawie wolno, rozstrzyga serwis; ekran po prostu nie chowa przycisku.
        "withdrawable_statuses": (ReviewStatus.ASSIGNED, ReviewStatus.DRAFT, ReviewStatus.SUBMITTED),
    }
