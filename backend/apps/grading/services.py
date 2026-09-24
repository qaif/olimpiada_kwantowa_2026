"""Logika domenowa oceniania: przydział recenzentów, wystawianie ocen, konsensus, moderacja.

Widoki tylko orkiestrują. Zasady wspólne dla całego modułu:

- każda zmiana stanu ``Submission`` idzie pod blokadą ``SELECT ... FOR UPDATE`` na tym zgłoszeniu.
  To jedyny punkt szeregowania: dwa równoczesne ``submit_review`` nie mogą utworzyć dwóch
  ``FinalGrade`` (relacja jeden-do-jednego w bazie jest dopiero drugą linią obrony),
- konflikt interesów na etapie wojewódzkim liczy się wyłącznie z ``CommitteeMember.district``:
  recenzent z województwa uczestnika nie dostaje jego pracy. Województwo członka komitetu jest
  opcjonalne (decyzja organizatora), więc jego brak nikogo nie wyklucza, a ``district_verified``
  jest tylko informacją dla koordynatora i niczego nie bramkuje. Konkurs z własnym podziałem
  terytorialnym (flaga ``custom_regions``) porównuje w tym samym miejscu regiony zamiast kodów
  województw – **reguła jest ta sama**, zmienia się słownik, którym jest wyrażona (§ 1.4.4),
- czas zawsze przez ``timezone.now()``.
"""

from __future__ import annotations

import logging
from collections import Counter

from django.db import connection, models, transaction
from django.utils import timezone
from rest_framework import status as http

from apps.accounts.models import (
    GROUP_COORDINATOR,
    GROUP_REVIEWER,
    CommitteeMember,
    CommitteeStatus,
    Region,
    region_for_district,
)
from apps.accounts.services import active_reviewer_profile
from apps.competitions.models import Problem, Stage, StageKind
from apps.core.api import DomainError
from apps.core.models import audit
from apps.submissions.models import Submission, SubmissionFile, SubmissionStatus

from .deadlines import review_due_at
from .models import (
    ROUND_BLIND,
    ROUND_TIEBREAK,
    FinalGrade,
    GradeMethod,
    ProblemReviewerRule,
    Review,
    ReviewCancelReason,
    ReviewerRole,
    ReviewStatus,
)
from .rubric import assert_total_in_scale, is_complete, validate_rubric

logger = logging.getLogger(__name__)

#: Stany zgłoszenia, w których wolno wystawić ocenę (moderacja obsługuje rundę rozjemczą).
REVIEWABLE_STATUSES = (SubmissionStatus.IN_REVIEW, SubmissionStatus.MODERATION)
#: Powód anulowania zapisywany w recenzji dla każdego powodu wpisywanego do audytu.
#:
#: Dwa słowniki, bo mają dwóch czytelników. W audycie stoją nazwy historyczne (``REVIEW_REVISED``,
#: ``COORDINATOR_OVERRIDE``) – szukają po nich istniejące raporty i ich zmiana zerwałaby ciągłość
#: akt. W recenzji stoi wartość z ``ReviewCancelReason``, z której panel robi jedno zdanie dla
#: recenzenta. Mapa trzyma oba zbiory w zgodzie zamiast powtarzać wartość przy każdym wywołaniu.
#: Wartość domyślna to decyzja koordynatora: pozostałe anulowania robi on ręcznie.
AUDIT_REASON_TO_CANCEL_REASON = {
    "MODERATION_RESOLVED": ReviewCancelReason.MODERATION_RESOLVED,
    "REVIEW_REVISED": ReviewCancelReason.REVISED,
    "COORDINATOR_OVERRIDE": ReviewCancelReason.OVERRIDE,
    "SUPERSEDED": ReviewCancelReason.SUPERSEDED,
}
#: Komunikat dla recenzenta, którego przydział został anulowany.
REVIEW_CANCEL_MESSAGES = {
    ReviewCancelReason.COORDINATOR: "Koordynator odebrał Ci tę pracę.",
    ReviewCancelReason.SUPERSEDED: (
        "Uczestnik wysłał nową wersję rozwiązania – ta recenzja została anulowana."
    ),
    ReviewCancelReason.OVERRIDE: "Koordynator wpisał ocenę końcową tej pracy.",
    ReviewCancelReason.MODERATION_RESOLVED: "Rozjazd ocen rozstrzygnął już ktoś inny.",
    ReviewCancelReason.REVISED: "Recenzent rundy 1 poprawił ocenę i rozjazd zniknął.",
}
#: Treść dla recenzji anulowanej bez zapisanego powodu, czyli sprzed wprowadzenia ``cancel_reason``.
#: To samo zdanie, które panel pokazywał wtedy każdej anulowanej recenzji – i prawdziwe dla
#: zdecydowanej większości tamtych wierszy, bo jedyną drogą do anulowania recenzji rundy 1 było
#: odebranie pracy przez koordynatora. Wartość ogólna („przydział anulowano”) byłaby formalnie
#: ostrożniejsza, ale odbierałaby informację komuś, kto czyta swoją starą historię.
REVIEW_CANCELLED_UNKNOWN_MESSAGE = REVIEW_CANCEL_MESSAGES[ReviewCancelReason.COORDINATOR]
#: Przestrzeń kluczy blokad doradczych Postgresa dla tego modułu (pg_advisory_xact_lock(int4, int4)).
#: Stała nie może kolidować z innymi modułami – każdy, kto doda blokadę doradczą, bierze własną.
ADVISORY_LOCK_NAMESPACE_ASSIGNMENT = 1005
#: Twarde limity adnotacji – JSON od recenzenta nie może urosnąć w nieskończoność.
MAX_ANNOTATIONS = 500
MAX_ANNOTATION_TEXT = 2000
MAX_COMMENT_LENGTH = 20000


# --- błędy domenowe ---------------------------------------------------------------------------


def _conflict(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_409_CONFLICT)


def _bad_request(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_400_BAD_REQUEST)


# --- pomocnicze -------------------------------------------------------------------------------


def _norm_district(value: str | None) -> str:
    return (value or "").strip().casefold()


def cancel_message(review: Review) -> str:
    """Zdanie, którym panel recenzenta tłumaczy anulowany przydział.

    Jedna funkcja dla listy i dla szczegółów recenzji: oba ekrany mówią o tym samym zdarzeniu,
    a dwie kopie słownika rozjechałyby się przy pierwszym nowym powodzie anulowania.
    """
    return REVIEW_CANCEL_MESSAGES.get(review.cancel_reason, REVIEW_CANCELLED_UNKNOWN_MESSAGE)


def _cancel_review(review: Review, *, reason: str) -> None:
    """Przestawia recenzję w ``CANCELLED`` razem z powodem widocznym dla recenzenta.

    ``reason`` jest powodem *audytowym* – na powód zapisywany w recenzji tłumaczy go
    ``AUDIT_REASON_TO_CANCEL_REASON``, żeby żadna ścieżka anulowania nie zostawiła recenzentowi
    pustego pola i pytania „dlaczego zniknęło mi zadanie”.
    """
    review.status = ReviewStatus.CANCELLED
    review.cancel_reason = AUDIT_REASON_TO_CANCEL_REASON.get(reason, ReviewCancelReason.COORDINATOR)
    review.save(update_fields=["status", "cancel_reason"])


def is_coordinator(user) -> bool:
    if not user or not user.is_authenticated or not user.is_active:
        return False
    return user.groups.filter(name=GROUP_COORDINATOR).exists()


def _lock_stage_for_assignment(stage: Stage) -> None:
    """Blokada doradcza na czas transakcji przydziału dla jednego etapu.

    Bez niej dwa równoczesne ``POST stages/{id}/assign/`` czytają ten sam obraz świata: każde widzi
    rozwiązanie bez recenzji i przydziela mu własnych recenzentów. Efektem jest albo czterech
    recenzentów zamiast dwóch, albo ``IntegrityError`` (500) na unikalności przydziału. Blokada
    doradcza, a nie ``select_for_update`` na ``Stage``: szeregujemy *operację*, a nie wiersz etapu,
    który sam się tu nie zmienia. Zwalnia ją koniec transakcji – także przy wyjątku.

    Jedyne surowe SQL w module i jedyne możliwe: to funkcja Postgresa bez odpowiednika w ORM.
    Parametry idą przez placeholdery sterownika, nie przez formatowanie napisu.
    """
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)", [ADVISORY_LOCK_NAMESPACE_ASSIGNMENT, int(stage.pk)]
        )


def reviewer_pool() -> list[CommitteeMember]:
    """Aktywni recenzenci: status ACTIVE, konto aktywne, grupa ``reviewer``."""
    return list(
        CommitteeMember.objects.filter(
            status=CommitteeStatus.ACTIVE,
            user__is_active=True,
            user__groups__name=GROUP_REVIEWER,
        )
        .select_related("user")
        .distinct()
        .order_by("pk")
    )


#: Jedyne wejście do flagi podziału terytorialnego w tym module (§ 1.0 (c)). Nazwa stoi
#: w ``FEATURE_DEFAULTS`` (``apps/tenancy/models.py``) i jest domyślnie wyłączona.
CUSTOM_REGIONS_FLAG = "custom_regions"


def _competition_of(stage: Stage):
    """Konkurs etapu – drogą przez edycję, jedyną, jaką etap ma (ten sam idiom, co w wynikach).

    Odczyt kosztuje dwa zapytania **raz na obiekt etapu** (Django trzyma obiekty powiązane przy
    instancji), a w ogóle się nie wykonuje poza etapem wojewódzkim: wołający sprawdza rodzaj etapu
    przed zapytaniem o konkurs, bo poza etapem wojewódzkim konfliktu nie ma i nie było.
    """
    return stage.edition.competition


def conflict_by_region(stage: Stage) -> bool:
    """Czy konflikt interesów tego etapu liczy się po regionach konkursu, czy po województwach.

    Wołający pyta o to **raz na czynność**, żeby nie sięgać do regionu uczestnika w konkursie,
    który regionów nie używa – tam każde takie sięgnięcie byłoby zapytaniem, którego przed etapem 2
    nie było. Poza etapem wojewódzkim odpowiedź jest ``False`` bez ani jednego zapytania.
    """
    if stage.kind != StageKind.DISTRICT:
        return False
    return _competition_of(stage).has_feature(CUSTOM_REGIONS_FLAG)


def _has_district_conflict_by_code(member: CommitteeMember, participant_district: str | None) -> bool:
    """Dzisiejsza reguła, co do znaku: równość kodów województw po normalizacji zapisu."""
    member_district = _norm_district(member.district)
    if not member_district:
        return False
    return member_district == _norm_district(participant_district)


def _region_of(competition, profile) -> Region | None:
    """Region profilu: z kolumny ``region``, a w jej braku – odwzorowanie jego województwa.

    Odwrót na województwo jest **warunkiem ciągłości**, a nie wygodą. Migracja
    ``accounts.0026_regions_from_voivodeships`` wypełniła ``region`` każdemu istniejącemu profilowi,
    ale profil założony między wdrożeniem regionów a włączeniem flagi ma tam ``NULL`` – i gdyby taki
    profil znaczył „brak konfliktu”, dzień włączenia flagi byłby dniem, w którym recenzent dostaje
    pracę ucznia z własnego województwa. ``region_for_district`` odwzorowuje szesnaście kodów
    jeden do jednego, więc dla takiej pary wynik reguły jest dokładnie ten, co dziś.
    """
    if profile.region_id is not None:
        return profile.region
    return region_for_district(competition, profile.district)


def participant_conflict_region(stage: Stage, participant) -> Region | None:
    """Region uczestnika do porównania – odczytany **wyłącznie** w konkursie, który regionów używa.

    Skrót dla pięciu miejsc wołających regułę konfliktu: w konkursie bez flagi nie dotyka kolumny
    ``region``, więc nie dokłada ani jednego zapytania do dzisiejszego przydziału recenzentów;
    w konkursie z flagą podaje regule region wprost z profilu, zamiast odtwarzać go z województwa
    (którego konkurs z własnym podziałem może w ogóle nie wypełniać).
    """
    return participant.region if conflict_by_region(stage) else None


def has_district_conflict(
    member: CommitteeMember,
    stage: Stage,
    participant_district: str | None = None,
    *,
    participant_region: Region | None = None,
) -> bool:
    """Czy recenzent jest w konflikcie okręgu dla tego uczestnika na tym etapie.

    **Ta sama reguła, wyrażona na regionach tam, gdzie regiony są** (§ 1.4.4). Trzy warunki zostają
    co do joty, niezależnie od flagi:

    - dotyczy **wyłącznie** etapu wojewódzkiego (``StageKind.DISTRICT``) – rodzaj etapu mówi, które
      to zawody, i to jest właściwe kryterium dla reguły o mieszkaniu w tym samym okręgu;
    - brak wartości u członka komitetu **nie** wyklucza go z niczego: kto nie podał ani województwa,
      ani regionu, ocenia prace zewsząd (decyzja organizatora);
    - ``district_verified`` nie bierze udziału – wartość niepotwierdzona, ale równa wartości
      uczestnika, jest konfliktem, bo to bezpieczniejszy kierunek.

    Przy wyłączonej fladze ``custom_regions`` (czyli w Konkursie #1) wykonuje się dokładnie
    dzisiejsze ciało: równość kodów województw. Przy włączonej porównywane są regiony, a region
    z ``counts_for_conflict=False`` (np. „poza Polską”) konfliktu nie tworzy – dwóch uczestników
    z zagranicy nie jest ze sobą w konflikcie z tytułu miejsca zamieszkania.

    ``participant_region`` jest **optymalizacją, nie warunkiem**: wołający, który już ma region
    uczestnika, oszczędza zapytanie, a wołający, który go nie poda, dostanie tę samą odpowiedź –
    region zostanie odczytany z profilu albo odtworzony z województwa. Pominięcie tego argumentu
    w którymkolwiek z pięciu miejsc wołających nie może więc dać cichego „brak konfliktu”.
    """
    if stage.kind != StageKind.DISTRICT:
        return False
    competition = _competition_of(stage)
    if not competition.has_feature(CUSTOM_REGIONS_FLAG):
        return _has_district_conflict_by_code(member, participant_district)
    if participant_region is None:
        participant_region = region_for_district(competition, participant_district)
    if participant_region is None or not participant_region.counts_for_conflict:
        return False
    member_region = _region_of(competition, member)
    return member_region is not None and member_region.pk == participant_region.pk


def _assert_reviewer_eligible(reviewer: CommitteeMember) -> None:
    """Czy tę osobę w ogóle wolno przydzielić do oceniania.

    Ta sama bramka, co filtr ``reviewer_pool`` – wypisana osobno, bo przydział ręczny nie przechodzi
    przez pulę: koordynator wskazuje konkretną osobę z listy i musi dostać powód odmowy, a nie
    „tej osoby nie ma w puli”.
    """
    if reviewer.status != CommitteeStatus.ACTIVE or not reviewer.user.is_active:
        raise _bad_request("Recenzent nie jest aktywny.", "REVIEWER_NOT_ELIGIBLE")
    if not reviewer.user.groups.filter(name=GROUP_REVIEWER).exists():
        raise _bad_request("Wskazana osoba nie jest recenzentem.", "REVIEWER_NOT_ELIGIBLE")


def _validate_line_note(item: dict) -> dict:
    """Uwaga przypięta do linii kodu: ``{line, text, public}``.

    Drugi dopuszczalny kształt wpisu w ``Review.annotations``, obok prostokąta na stronie. Powód
    rozdzielenia: rozwiązanie oddane jako ``.py`` albo ``.ipynb`` nie ma stron ani współrzędnych,
    a recenzent mówi o nim „linia 42”. Wspólne pole, a nie osobna tabela, bo to nadal ta sama
    rzecz – uwaga recenzenta do fragmentu pracy, z tą samą regułą jawności (``public``) i tym
    samym limitem długości. Rozpoznanie kształtu idzie po obecności klucza ``line``
    (patrz ``validate_annotations``), więc stare wpisy są nadal poprawne i nietknięte.
    """
    line = item.get("line")
    text = item.get("text", "")
    public = item.get("public", False)
    if not isinstance(line, int) or isinstance(line, bool) or line < 1:
        raise _bad_request("Uwaga do kodu wymaga numeru linii (line >= 1).", "INVALID_ANNOTATIONS")
    if not isinstance(text, str):
        raise _bad_request("Treść adnotacji musi być tekstem.", "INVALID_ANNOTATIONS")
    if not isinstance(public, bool):
        raise _bad_request("Pole 'public' musi być logiczne.", "INVALID_ANNOTATIONS")
    return {"line": line, "text": text[:MAX_ANNOTATION_TEXT], "public": public}


def validate_annotations(raw) -> list[dict]:
    """Sprowadza adnotacje do jednego z dwóch kanonicznych kształtów.

    ``{page, rect[4], text, public}`` – prostokąt na stronie PDF-u albo na zdjęciu rozwiązania;
    ``{line, text, public}`` – uwaga do linii kodu (``.py``, ``.ipynb``). O tym, który kształt
    obowiązuje dany wpis, rozstrzyga obecność klucza ``line``: dzięki temu adnotacje zapisane przed
    wprowadzeniem podglądu kodu przechodzą tę funkcję bez zmiany, a klient, który nie wie o uwagach
    do linii, nigdy ich przypadkiem nie utworzy.

    JSON przychodzi od recenzenta, więc jest walidowany strukturalnie i przycinany – nie trafia
    do bazy „jak leci”.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _bad_request("Adnotacje muszą być listą.", "INVALID_ANNOTATIONS")
    if len(raw) > MAX_ANNOTATIONS:
        raise _bad_request(f"Za dużo adnotacji (limit {MAX_ANNOTATIONS}).", "INVALID_ANNOTATIONS")
    cleaned: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            raise _bad_request("Adnotacja musi być obiektem.", "INVALID_ANNOTATIONS")
        if "line" in item:
            cleaned.append(_validate_line_note(item))
            continue
        page = item.get("page")
        rect = item.get("rect")
        text = item.get("text", "")
        public = item.get("public", False)
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise _bad_request("Adnotacja wymaga numeru strony (page >= 1).", "INVALID_ANNOTATIONS")
        if not isinstance(rect, (list, tuple)) or len(rect) != 4:
            raise _bad_request("Adnotacja wymaga prostokąta rect=[x, y, w, h].", "INVALID_ANNOTATIONS")
        if any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in rect):
            raise _bad_request("Współrzędne rect muszą być liczbami.", "INVALID_ANNOTATIONS")
        if not isinstance(text, str):
            raise _bad_request("Treść adnotacji musi być tekstem.", "INVALID_ANNOTATIONS")
        if not isinstance(public, bool):
            raise _bad_request("Pole 'public' musi być logiczne.", "INVALID_ANNOTATIONS")
        cleaned.append(
            {
                "page": page,
                "rect": [float(value) for value in rect],
                "text": text[:MAX_ANNOTATION_TEXT],
                "public": public,
            }
        )
    return cleaned


def _clean_comment(value: str | None) -> str:
    return (value or "")[:MAX_COMMENT_LENGTH]


def allowed_scores(stage: Stage, problem: Problem | None = None) -> set[int]:
    """Dopuszczalne oceny: skala **zadania**, jeśli je ma, w przeciwnym razie skala etapu.

    Pierwszeństwo zadania jest całą regułą „skala punktacji zadań” (prośba organizatora): etap
    niesie skalę domyślną, a pojedyncze zadanie wolno punktować inaczej – np. zadanie otwarte
    w skali 0–10 obok zadań 0/2/5/6. Puste ``Problem.scoring_values`` znaczy „dziedzicz po etapie”,
    a nie „brak skali”: dzięki temu dodanie zadania nie wymaga przepisywania skali etapu, a zmiana
    skali etapu obejmuje wszystkie zadania, które własnej nie mają.

    ``problem`` jest opcjonalny wyłącznie dla wywołań, które pytają o skalę etapu jako całości
    (ekran koordynatora, walidacja reklamacji bez pracy). Każde miejsce, które zna zgłoszenie,
    **musi** podać jego zadanie – inaczej ocena przechodząca walidację nie musiałaby należeć do
    skali, według której praca jest oceniana.

    Zwracany zbiór jest zawsze w postaci **przechowywanej**, bo jego jedynym zastosowaniem jest
    porównanie z tym, co wolno wpisać do kolumny ``score``. Skala etapu z punktami ujemnymi leży
    w bazie przesunięta (``ScoringScale.offset``, etap 2 § 1.2.6 b), więc idzie tu przez
    ``stored_allowed_values``; skala **zadania** przesunięcia nie ma i mieć nie może, więc wraca
    dosłownie. Przy ``offset = 0`` – czyli w każdym konkursie bez punktów ujemnych, w tym
    w Konkursie #1 – oba zbiory są tym samym zbiorem, co przed etapem 2, co do wartości.
    """
    if problem is not None:
        values = problem.allowed_values()
        if values:
            return values
    scale = getattr(stage, "scoring_scale", None)
    if scale is None:
        raise _conflict("Etap nie ma skali punktacji.", "SCORING_SCALE_MISSING")
    values = scale.stored_allowed_values()
    if not values:
        raise _conflict("Skala punktacji etapu jest pusta.", "SCORING_SCALE_MISSING")
    return values


def scale_items(stage: Stage, problem: Problem | None = None) -> list[dict]:
    """Pozycje obowiązującej skali jako ``[{"value": int, "label": str}]`` – z zadania albo z etapu.

    Bliźniak ``allowed_scores`` dla ekranów: recenzent i koordynator wybierają ocenę z listy
    **z etykietami** („2 – istotny postęp”), a nie z gołych liczb. Pierwszeństwo jest to samo, więc
    lista wyboru nie może pokazać wartości, której zapis by nie przyjął.

    Brak skali to pusta lista, a nie wyjątek: ekran ma stanąć także dla etapu, którego skalę ktoś
    skasował – znika z niego wtedy sam formularz oceny, a nie cała strona.
    """
    raw = (problem.scoring_values if problem is not None else None) or None
    if raw is None:
        scale = getattr(stage, "scoring_scale", None)
        raw = scale.values if scale is not None else []
    return [
        {"value": item["value"], "label": item.get("label", "")}
        for item in raw or []
        if isinstance(item, dict)
        and isinstance(item.get("value"), int)
        and not isinstance(item.get("value"), bool)
    ]


def _assert_score_in_scale(stage: Stage, score, problem: Problem | None = None) -> int:
    values = allowed_scores(stage, problem)
    if not isinstance(score, int) or isinstance(score, bool) or score not in values:
        raise _bad_request(f"Ocena {score} nie należy do skali {sorted(values)}.", "SCORE_NOT_IN_SCALE")
    return score


def _locked_submission(submission_id: int) -> Submission:
    """Zgłoszenie pod blokadą wiersza – jedyny punkt szeregowania zapisów oceniania."""
    return (
        Submission.objects.select_for_update(of=("self",))
        .select_related(
            "entry",
            "entry__participant",
            "entry__stage",
            "entry__stage__scoring_scale",
            # ``problem`` wchodzi do zapytania, bo od skali per zadanie każda walidacja oceny
            # potrzebuje zadania – bez tego byłoby jedno zapytanie na każdy zapis recenzji.
            "problem",
        )
        .get(pk=submission_id)
    )


# --- przydział --------------------------------------------------------------------------------


def _assignable_submissions(stage: Stage) -> list[Submission]:
    """Zablokowane (i już przydzielane) rozwiązania etapu – po jednym, najnowszym, na zadanie.

    ``close_stage`` blokuje wyłącznie najnowszą nadającą się do oceny wersję, więc filtr po
    statusie zwykle wystarcza. Wybór maksymalnej wersji per (wpis, zadanie) jest zabezpieczeniem:
    starsza wersja nigdy nie może wejść do oceniania obok nowszej.
    """
    rows = (
        Submission.objects.filter(
            entry__stage=stage,
            status__in=(SubmissionStatus.LOCKED, SubmissionStatus.IN_REVIEW),
        )
        # ``entry__participant__user`` i ``problem`` są tu dla ekranu przydziałów ręcznych
        # (nazwisko w wyszukiwarce, numer zadania w tabeli) – bez nich lista robi zapytanie na wiersz.
        .select_related("entry", "entry__participant", "entry__participant__user", "problem")
        .order_by("entry_id", "problem_id", "-version")
    )
    best: dict[tuple[int, int], Submission] = {}
    for submission in rows:
        best.setdefault((submission.entry_id, submission.problem_id), submission)
    return sorted(best.values(), key=lambda item: item.pk)


def is_assignable(submission: Submission) -> bool:
    """Ten sam predykat, co ``_assignable_submissions``, ale dla jednego zgłoszenia.

    Przydział ręczny musi odpowiadać na pytanie „czy tę konkretną pracę wolno jeszcze komuś dać”,
    a nie budować listy całego etapu. Warunek jest dwuczłonowy, dokładnie jak tam: stan LOCKED albo
    IN_REVIEW **i** brak nowszej wersji tej samej pracy w tych stanach – starsza wersja nie może
    wejść do oceniania obok nowszej.
    """
    if submission.status not in (SubmissionStatus.LOCKED, SubmissionStatus.IN_REVIEW):
        return False
    return not (
        Submission.objects.filter(
            entry_id=submission.entry_id,
            problem_id=submission.problem_id,
            status__in=(SubmissionStatus.LOCKED, SubmissionStatus.IN_REVIEW),
            version__gt=submission.version,
        ).exists()
    )


def problem_rule_reviewers(problem_ids) -> dict[int, list[CommitteeMember]]:
    """Recenzenci z reguł „z góry”, pogrupowani po zadaniu, w kolejności utworzenia reguł.

    Pula reguł jest zawężona do osób, które nadal wolno przydzielać: recenzent zawieszony albo
    z wyłączonym kontem nie może dostać pracy dlatego, że kiedyś powstała dla niego reguła.
    """
    rules = (
        ProblemReviewerRule.objects.filter(problem_id__in=list(problem_ids))
        .select_related("reviewer", "reviewer__user")
        .order_by("created_at", "id")
    )
    eligible_ids = {member.pk for member in reviewer_pool()}
    grouped: dict[int, list[CommitteeMember]] = {}
    for rule in rules:
        if rule.reviewer_id in eligible_ids:
            grouped.setdefault(rule.problem_id, []).append(rule.reviewer)
    return grouped


#: Jedyne wejście do flagi ról recenzenckich (§ 1.0 (c)). Domyślnie wyłączona, Konkurs #1 jej
#: nie włącza, a etap bez ról zachowuje się jak przed etapem 2 nawet po jej włączeniu.
REVIEWER_ROLES_FLAG = "reviewer_roles"


def stage_reviewer_roles(stage: Stage) -> list[ReviewerRole]:
    """Role rundy ślepej tego etapu – w kolejności, w jakiej mają być obsadzane.

    Pusta lista znaczy „ten etap ról nie ma” i jest odpowiedzią w dwóch różnych sytuacjach:
    konkurs nie włączył flagi albo włączył ją, ale organizator ról jeszcze nie wpisał. Obie mają
    dawać dokładnie dzisiejsze zachowanie przydziału, więc rozróżniać ich nie ma po co.

    Flaga sprawdzana przed zapytaniem: konkurs bez ról nie płaci za nie odczytem tabeli.
    """
    if not _competition_of(stage).has_feature(REVIEWER_ROLES_FLAG):
        return []
    return list(stage.reviewer_roles.filter(round=ROUND_BLIND).order_by("position", "id"))


def _role_slots(stage: Stage) -> list[ReviewerRole]:
    """Miejsca do obsadzenia w rundzie ślepej: rola powtórzona tyle razy, ilu chce recenzentów.

    Lista miejsc, a nie sama liczba, bo przydział musi wiedzieć nie tylko **ilu** recenzentów
    dołożyć, ale i **którą rolę** wpisać każdemu z nich.
    """
    return [role for role in stage_reviewer_roles(stage) for _ in range(role.count)]


def _skipped(submission: Submission, reason: str, reviewer_id: int | None = None) -> dict:
    """Wiersz listy ``skipped``. Uczestnik wyłącznie pseudonimem – audyt czytają też osoby bez RODO."""
    return {
        "submission_id": submission.pk,
        "public_code": submission.entry.participant.public_code,
        "reason": reason,
        "reviewer_id": reviewer_id,
    }


@transaction.atomic
def assign_reviewers(stage: Stage, per_submission: int = 2, *, actor=None, request=None) -> dict:
    """Przydziela ``per_submission`` różnych recenzentów każdemu zablokowanemu rozwiązaniu etapu.

    Równoważenie: kolejny przydział dostaje recenzent z najmniejszą bieżącą liczbą recenzji w tym
    etapie. Idempotentny – rozwiązanie, które ma już komplet recenzentów rundy 1, jest pomijane.

    Rozwiązanie, dla którego nie da się skompletować recenzentów bez konfliktu interesów, **nie**
    wywraca całego etapu: trafia na listę ``skipped`` (z powodem), a reszta zostaje przydzielona
    i zacommitowana. Koordynator dostaje wtedy listę spraw do ręcznego załatwienia zamiast
    komunikatu „nic się nie udało”. Dopiero gdy nie udało się przydzielić **niczego**, leci 409 –
    wtedy nie ma czego commitować i cisza byłaby myląca.

    Reguły „z góry” (``ProblemReviewerRule``) mają pierwszeństwo przed równoważeniem: recenzenci
    wskazani dla zadania wchodzą na miejsca ``per_submission`` w kolejności utworzenia reguł, a
    automat dobiera dopiero resztę. Gdy reguł jest więcej niż miejsc, przydzielani są **wszyscy** –
    świadoma decyzja organizatora wygrywa z liczbą, którą wpisał w formularzu przydziału. Reguła
    nie łamie jednak konfliktu interesów: recenzent skonfliktowany z tym uczestnikiem jest dla tej
    jednej pracy pomijany i trafia do ``skipped`` z powodem ``RULE_REVIEWER_CONFLICT``.

    **Role recenzenckie** (etap 2 § 1.2.7) zmieniają tu dokładnie jedno: skąd bierze się liczba
    recenzentów. Etap z rolami mówi sam, ilu ich ma – suma ``ReviewerRole.count`` rundy ślepej –
    i wtedy ``per_submission`` jest ignorowane, bo liczba wpisana w formularzu przydziału byłaby
    drugą, sprzeczną deklaracją tej samej rzeczy. Etap bez ról (czyli każdy etap Konkursu #1)
    czyta argument tak jak dotąd. Nowe recenzje dostają role po kolei (``position``), a recenzja
    przydzielona wcześniej – zanim organizator role wpisał – zostaje bez roli i nic jej to nie robi.
    """
    if per_submission < 1:
        raise _bad_request("Liczba recenzentów musi być dodatnia.", "INVALID_PER_SUBMISSION")

    # Role czytane **raz na cały przydział**: to jest odczyt konfiguracji etapu, a nie decyzja
    # o pojedynczej pracy (§ 1.0 (c)). Pusta lista znaczy „etap ról nie ma” i jest jedynym stanem
    # Konkursu #1 – wtedy poniżej nie wykonuje się ani jedna nowa linijka.
    slots = _role_slots(stage)
    if slots:
        per_submission = len(slots)

    _lock_stage_for_assignment(stage)
    submissions = _assignable_submissions(stage)
    pool = reviewer_pool()
    rules = problem_rule_reviewers({submission.problem_id for submission in submissions})
    loads: Counter[int] = Counter({member.pk: 0 for member in pool})
    for reviewer_id in Review.objects.filter(submission__entry__stage=stage).values_list(
        "reviewer_id", flat=True
    ):
        loads[reviewer_id] += 1

    created_total = 0
    touched: list[int] = []
    skipped: list[dict] = []
    # Jedna chwila przydziału i jeden termin na cały przebieg: to jest **ta sama fala** prac, więc
    # recenzenci mają dostać ten sam termin co do sekundy. Liczenie ``now`` osobno przy każdej pracy
    # dawałoby terminy różniące się milisekundami i komunikat koordynatora nie mógłby podać jednej daty.
    now = timezone.now()
    due_at = review_due_at(stage, now)
    # Flaga podziału terytorialnego czytana **raz na cały przydział**, a nie przy każdej parze
    # praca–recenzent: w konkursie bez regionów sięgnięcie po ``participant.region`` byłoby
    # zapytaniem na każdą pracę, którego przed etapem 2 nie było (§ 1.0 (c)).
    by_region = conflict_by_region(stage)
    for submission in submissions:
        already = set(
            Review.objects.filter(submission=submission, round=ROUND_BLIND)
            .exclude(status=ReviewStatus.CANCELLED)
            .values_list("reviewer_id", flat=True)
        )
        # Ile miejsc tej pracy jest już obsadzonych **przed** tym przebiegiem – stąd zaczynają się
        # role dla nowych recenzji. Liczone tutaj, bo ``already`` rośnie jeszcze w kroku 1.
        filled = len(already)
        participant_district = submission.entry.participant.district
        participant_region = submission.entry.participant.region if by_region else None

        # Krok 1: recenzenci z reguł zadania. Konflikt interesów raportujemy per praca i per osoba –
        # koordynator musi wiedzieć, która reguła nie zadziałała i dla kogo, żeby załatwić to ręcznie.
        chosen: list[CommitteeMember] = []
        for member in rules.get(submission.problem_id, ()):
            if member.pk in already:
                continue
            if has_district_conflict(
                member, stage, participant_district, participant_region=participant_region
            ):
                skipped.append(_skipped(submission, "RULE_REVIEWER_CONFLICT", member.pk))
                continue
            chosen.append(member)
            already.add(member.pk)

        # Krok 2: dopełnienie z puli. Reguły zajmują miejsca z ``per_submission``, więc gdy zajęły
        # wszystkie (albo więcej), automat nie dokłada już nikogo.
        missing = per_submission - len(already)
        if missing > 0:
            eligible = [
                member
                for member in pool
                if member.pk not in already
                and not has_district_conflict(
                    member, stage, participant_district, participant_region=participant_region
                )
            ]
            if len(eligible) < missing:
                skipped.append(_skipped(submission, "NOT_ENOUGH_REVIEWERS"))
            else:
                chosen.extend(sorted(eligible, key=lambda member: (loads[member.pk], member.pk))[:missing])

        if not chosen:
            continue
        # Nowe recenzje obsadzają role od pierwszego wolnego miejsca. Bez ról ``slots`` jest puste
        # i każda recenzja powstaje z ``role = None``, dokładnie jak przed etapem 2. Recenzentów
        # ponad liczbę miejsc (więcej reguł niż ról) też przydzielamy – bez roli, bo świadoma
        # decyzja organizatora wygrywa z liczbą, a wymyślanie dla niej roli byłoby zgadywaniem.
        Review.objects.bulk_create(
            [
                Review(
                    submission=submission,
                    reviewer=member,
                    round=ROUND_BLIND,
                    role=slots[filled + index] if filled + index < len(slots) else None,
                    status=ReviewStatus.ASSIGNED,
                    assigned_at=now,
                    due_at=due_at,
                )
                for index, member in enumerate(chosen)
            ]
        )
        for member in chosen:
            loads[member.pk] += 1
        created_total += len(chosen)
        if submission.status == SubmissionStatus.LOCKED:
            submission.status = SubmissionStatus.IN_REVIEW
            submission.save(update_fields=["status"])
        touched.append(submission.pk)

    if created_total == 0 and any(item["reason"] == "NOT_ENOUGH_REVIEWERS" for item in skipped):
        # Nic nie dało się przydzielić – nie ma czego commitować, więc odpowiadamy błędem domenowym.
        # Warunek patrzy na *powód*, a nie na samą niepustość listy: przy pominiętej regule (konflikt
        # recenzenta wskazanego z góry) reszta przydziału mogła się udać albo nie być potrzebna,
        # a komunikat „za mało recenzentów” byłby wtedy nieprawdą.
        raise _conflict("Za mało recenzentów bez konfliktu interesów dla tego etapu.", "NOT_ENOUGH_REVIEWERS")

    audit(
        actor,
        "review.assigned",
        stage,
        {
            "submissions": len(touched),
            "assignments": created_total,
            "per_submission": per_submission,
            "due_at": due_at.isoformat() if due_at else None,
            "skipped": [item["submission_id"] for item in skipped],
        },
        request=request,
    )
    logger.info(
        "Etap %s: przydzielono %s recenzji dla %s rozwiązań, pominięto %s",
        stage.pk,
        created_total,
        len(touched),
        len(skipped),
    )
    # ``due_at`` jest w wyniku, bo komunikat po przydziale ma powiedzieć koordynatorowi, do kiedy
    # recenzenci mają czas – to najczęstsze pytanie tuż po kliknięciu „Przydziel recenzentów”,
    # a odpowiedź na nie zna w tej chwili wyłącznie serwis.
    return {
        "submissions": len(touched),
        "assignments": created_total,
        "due_at": due_at,
        "skipped": skipped,
    }


# --- przydział ręczny -------------------------------------------------------------------------


def _create_blind_assignment(submission: Submission, reviewer: CommitteeMember) -> Review:
    """Tworzy (albo wskrzesza) przydział rundy 1 dla wskazanej pary praca–recenzent.

    Wskrzeszenie zamiast ``create``, bo unikalność w bazie obejmuje także recenzje ``CANCELLED``:
    po cofnięciu przydziału (``unassign_reviewer``) drugi ``create`` dla tej samej pary skończyłby
    się ``IntegrityError`` (500) zamiast zwykłym ponownym przydziałem. Anulowany rekord wraca więc
    do ``ASSIGNED`` ze świeżą datą – historia zmian zostaje w audycie, nie w duplikacie wiersza.
    """
    now = timezone.now()
    # Termin liczy się od **tego** przydziału, a nie od pierwotnego: praca wraca do kogoś nowego
    # (albo do tej samej osoby po odebraniu i oddaniu), więc odziedziczony termin sprzed dwóch
    # tygodni znaczyłby „po terminie od pierwszej sekundy”.
    due_at = review_due_at(submission.entry.stage, now)
    existing = Review.objects.filter(
        submission=submission, reviewer=reviewer, round=ROUND_BLIND, status=ReviewStatus.CANCELLED
    ).first()
    if existing is not None:
        existing.status = ReviewStatus.ASSIGNED
        # Powód anulowania znika razem z anulowaniem: przydział znów jest do zrobienia, a stary
        # powód wyświetlałby się przy recenzji, która nie jest już anulowana.
        existing.cancel_reason = ""
        existing.assigned_at = now
        existing.due_at = due_at
        # Przypomnienia zaczynają się od nowa razem z terminem – inaczej wskrzeszony przydział
        # czekałby na pierwszy list do jutra, bo „już przypominaliśmy” (sprzed anulowania).
        existing.reminded_at = None
        existing.save(update_fields=["status", "cancel_reason", "assigned_at", "due_at", "reminded_at"])
        return existing
    return Review.objects.create(
        submission=submission,
        reviewer=reviewer,
        round=ROUND_BLIND,
        status=ReviewStatus.ASSIGNED,
        assigned_at=now,
        due_at=due_at,
    )


def _start_review_if_locked(submission: Submission) -> None:
    """LOCKED → IN_REVIEW po pierwszym przydziale – dokładnie jak w ścieżce automatycznej."""
    if submission.status == SubmissionStatus.LOCKED:
        submission.status = SubmissionStatus.IN_REVIEW
        submission.save(update_fields=["status"])


@transaction.atomic
def add_problem_reviewer_rule(problem, reviewer: CommitteeMember, *, actor=None, request=None) -> dict:
    """Tworzy regułę „to zadanie recenzuje ta osoba” i stosuje ją od razu do prac już zablokowanych.

    Reguła obowiązująca dopiero od następnego przebiegu ``assign_reviewers`` byłaby pułapką:
    koordynator, który dodaje ją po zamknięciu etapu, zobaczyłby „zapisano” i żadnej zmiany na
    liście przydziałów. Dlatego serwis od razu dopisuje recenzje do wszystkich prac tego zadania,
    które nadaje się jeszcze przydzielać, i zwraca liczniki – ile dopisano, ile pominięto z powodu
    konfliktu województwa i ile miało tego recenzenta już wcześniej.
    """
    _assert_reviewer_eligible(reviewer)
    if ProblemReviewerRule.objects.filter(problem=problem, reviewer=reviewer).exists():
        raise _conflict("Ta reguła już istnieje.", "RULE_ALREADY_EXISTS")

    stage = problem.stage
    rule = ProblemReviewerRule.objects.create(
        problem=problem,
        reviewer=reviewer,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )

    assigned = 0
    conflicts = 0
    already = 0
    # Jw.: flaga raz na całą regułę, a region uczestnika czytany wyłącznie tam, gdzie rozstrzyga.
    by_region = conflict_by_region(stage)
    for submission in _assignable_submissions(stage):
        if submission.problem_id != problem.pk:
            continue
        if (
            Review.objects.filter(submission=submission, reviewer=reviewer, round=ROUND_BLIND)
            .exclude(status=ReviewStatus.CANCELLED)
            .exists()
        ):
            already += 1
            continue
        participant = submission.entry.participant
        if has_district_conflict(
            reviewer,
            stage,
            participant.district,
            participant_region=participant.region if by_region else None,
        ):
            conflicts += 1
            continue
        _create_blind_assignment(submission, reviewer)
        _start_review_if_locked(submission)
        assigned += 1

    audit(
        actor,
        "review.rule_added",
        rule,
        {
            "problem_id": problem.pk,
            "reviewer_id": reviewer.pk,
            "assigned": assigned,
            "conflicts": conflicts,
            "already": already,
        },
        request=request,
    )
    logger.info(
        "Zadanie %s: reguła dla recenzenta %s, dopisano %s recenzji (konflikty: %s)",
        problem.pk,
        reviewer.pk,
        assigned,
        conflicts,
    )
    return {"rule": rule, "assigned": assigned, "conflicts": conflicts, "already": already}


@transaction.atomic
def remove_problem_reviewer_rule(rule: ProblemReviewerRule, *, actor=None, request=None) -> None:
    """Kasuje regułę. Recenzje, które z niej powstały, **zostają** – to już jest czyjaś praca.

    Kasowanie przydziałów razem z regułą kusi („cofnijmy wszystko”), ale znaczyłoby usuwanie
    rozpoczętych, a bywa że wystawionych ocen jednym kliknięciem w ekranie o regułach. Cofnięcie
    pojedynczego przydziału jest osobną, świadomą czynnością (``unassign_reviewer``).
    """
    problem = rule.problem
    diff = {"rule_id": rule.pk, "problem_id": rule.problem_id, "reviewer_id": rule.reviewer_id}
    rule.delete()
    # Celem wpisu jest zadanie, a nie skasowana reguła: po ``delete()`` jej identyfikator nie
    # wskazuje już niczego, a historia i tak jest czytana „po zadaniu”.
    audit(actor, "review.rule_removed", problem, diff, request=request)


@transaction.atomic
def assign_reviewer_to_submission(
    submission: Submission, reviewer: CommitteeMember, *, actor=None, request=None
) -> Review:
    """Przydziela wskazanego recenzenta do jednej, wskazanej pracy (runda ślepa).

    Furtka dla sytuacji, których automat nie ogarnia: praca pominięta jako ``skipped``, recenzent,
    który wypadł z obiegu, dociążenie konkretnej osoby. Odmowy są jawne i mają kody maszynowe –
    koordynator ma wiedzieć, *dlaczego* nie wolno, a nie dostać cichy brak efektu.

    Konflikt interesów obowiązuje tak samo, jak w przydziale automatycznym: ręczne wskazanie jest
    decyzją organizacyjną, a nie zwolnieniem z procedury (PROJEKT.md 2.2).
    """
    locked = _locked_submission(submission.pk)
    _assert_reviewer_eligible(reviewer)
    if not is_assignable(locked):
        raise _conflict(
            f"Rozwiązanie w stanie {locked.status} nie przyjmuje przydziałów.", "SUBMISSION_NOT_ASSIGNABLE"
        )
    if (
        Review.objects.filter(submission=locked, reviewer=reviewer)
        .exclude(status=ReviewStatus.CANCELLED)
        .exists()
    ):
        raise _conflict("Ten recenzent ma już tę pracę przydzieloną.", "ALREADY_ASSIGNED")
    if has_district_conflict(
        reviewer,
        locked.entry.stage,
        locked.entry.participant.district,
        participant_region=participant_conflict_region(locked.entry.stage, locked.entry.participant),
    ):
        raise _conflict("Recenzent ma konflikt interesów (województwo).", "REVIEWER_CONFLICT_OF_INTEREST")

    review = _create_blind_assignment(locked, reviewer)
    _start_review_if_locked(locked)
    audit(
        actor,
        "review.assigned_manually",
        review,
        {"submission_id": locked.pk, "reviewer_id": reviewer.pk, "round": ROUND_BLIND},
        request=request,
    )
    review.submission = locked
    return review


@transaction.atomic
def unassign_reviewer(review: Review, *, actor=None, request=None) -> Review:
    """Koordynator odbiera recenzentowi pracę – na każdym etapie jej życia (→ CANCELLED).

    Organizator prosił wprost: „koordynator może odebrać członkowi komitetu zadanie”. Dotyczy to
    także recenzji rozpoczętej (szkic) i **wystawionej** – bo to jest właśnie ten przypadek,
    w którym odebranie ma sens: ocena okazała się nie do utrzymania (konflikt interesów wyszedł
    po fakcie, praca oceniona pobieżnie), a recenzent nie ma już jej poprawiać (``revise_review``).

    Rekord zostaje – ślad po przydziale i wystawione punkty są częścią historii. Zmienia się status,
    a ``_settle_round_one`` przestaje taką recenzję liczyć. Odebranie recenzji wystawionej zdejmuje
    też ocenę uzgodnioną konsensusem: skoro jedna z dwóch zgodnych ocen wypadła, podstawa konsensusu
    zniknęła i praca wraca do oceniania.

    Bramki są te same, co przy poprawianiu własnej oceny (``withdrawal_block_reason``): ogłoszonych
    wyników, pracy zamkniętej ani oceny rozstrzygniętej przez człowieka to narzędzie nie rusza –
    od zmiany takiej oceny jest ``override_final_grade`` z obowiązkowym uzasadnieniem.
    """
    submission = _locked_submission(review.submission_id)
    review = Review.objects.select_related("reviewer", "reviewer__user").get(pk=review.pk)
    review.submission = submission

    reason = withdrawal_block_reason(review)
    if reason is not None:
        raise _conflict(GRADE_CHANGE_BLOCK_MESSAGES[reason], reason)

    previous_status = review.status
    was_submitted = previous_status == ReviewStatus.SUBMITTED
    started = previous_status != ReviewStatus.ASSIGNED
    _cancel_review(review, reason="COORDINATOR")

    diff = {"submission_id": submission.pk, "reviewer_id": review.reviewer_id}
    if started:
        diff |= {"round": review.round, "from_status": previous_status, "score": review.score}
    # Dwie nazwy dla dwóch różnych zdarzeń: „cofnięto przydział, którego nikt nie tknął” to nie to
    # samo, co „odebrano komuś rozpoczętą albo wystawioną recenzję”. Historia musi je rozróżniać,
    # a istniejące raporty szukają cofniętych przydziałów po ``review.unassigned``.
    audit(actor, "review.withdrawn" if started else "review.unassigned", review, diff, request=request)

    if was_submitted and review.round == ROUND_BLIND:
        _withdraw_consensus_grade(submission, reason="REVIEW_WITHDRAWN", actor=actor, request=request)
        # Świadome odstępstwo od „po prostu przelicz rundę jeszcze raz”: z jedną pozostałą recenzją
        # ``_settle_round_one`` utworzyłby ocenę uzgodnioną z jednego głosu, a praca w
        # GRADED_PROVISIONAL nie przyjmuje już żadnego przydziału – koordynator nie miałby jak dać
        # jej następnemu recenzentowi. Zostaje więc w IN_REVIEW i czeka na zastępstwo.
        if len(_round_one_reviews(submission)) > 1:
            _settle_round_one(submission, request=request)
    review.submission = submission
    return review


@transaction.atomic
def assign_third_reviewer(submission: Submission, reviewer: CommitteeMember, *, actor=None, request=None):
    """Wyznacza trzeciego recenzenta (runda 2) dla rozwiązania w moderacji."""
    locked = _locked_submission(submission.pk)
    if locked.status != SubmissionStatus.MODERATION:
        raise _conflict("Rozwiązanie nie jest w moderacji.", "NOT_IN_MODERATION")
    _assert_reviewer_eligible(reviewer)
    if Review.objects.filter(submission=locked, reviewer=reviewer, round=ROUND_BLIND).exists():
        raise _conflict(
            "Trzecim recenzentem nie może być autor oceny z rundy 1.", "REVIEWER_CONFLICT_OF_INTEREST"
        )
    if has_district_conflict(
        reviewer,
        locked.entry.stage,
        locked.entry.participant.district,
        participant_region=participant_conflict_region(locked.entry.stage, locked.entry.participant),
    ):
        raise _conflict("Recenzent ma konflikt interesów (województwo).", "REVIEWER_CONFLICT_OF_INTEREST")
    if Review.objects.filter(submission=locked, round=ROUND_TIEBREAK).exists():
        raise _conflict("Trzeci recenzent jest już wyznaczony.", "THIRD_REVIEWER_ALREADY_ASSIGNED")

    now = timezone.now()
    review = Review.objects.create(
        submission=locked,
        reviewer=reviewer,
        round=ROUND_TIEBREAK,
        status=ReviewStatus.ASSIGNED,
        assigned_at=now,
        # Rozjemca dostaje termin liczony tak samo jak każdy inny recenzent – od chwili, w której
        # dostał pracę. Runda 2 zaczyna się zwykle po deadline recenzji etapu, więc sufit z
        # ``review_due_at`` zwykle jej nie dotyczy (patrz reguła w ``apps.grading.deadlines``).
        due_at=review_due_at(locked.entry.stage, now),
    )
    audit(
        actor,
        "review.third_assigned",
        review,
        {"submission_id": locked.pk, "reviewer_id": reviewer.pk},
        request=request,
    )
    return review


# --- wystawianie ocen -------------------------------------------------------------------------


def _assert_review_open(review: Review, submission_status: str) -> None:
    """Wspólna bramka zapisu recenzji: stan recenzji **i** stan zgłoszenia.

    Sam stan recenzji nie wystarcza: po rozstrzygnięciu rozjazdu przez koordynatora albo po
    finalizacji etapu praca jest zamknięta, a wiszący przydział nie może już do niej niczego dopisać
    (maszyna stanów PROJEKT.md 2.4). Bez tego szkic zapisywał się do zgłoszenia w stanie FINAL.
    """
    if review.status == ReviewStatus.SUBMITTED:
        raise _conflict("Recenzja została już wystawiona.", "REVIEW_ALREADY_SUBMITTED")
    if review.status == ReviewStatus.CANCELLED:
        raise _conflict("Ten przydział został anulowany.", "REVIEW_CANCELLED")
    if submission_status not in REVIEWABLE_STATUSES:
        raise _conflict(
            f"Rozwiązanie w stanie {submission_status} nie przyjmuje ocen.", "SUBMISSION_NOT_REVIEWABLE"
        )


def save_draft(
    review: Review,
    *,
    score=None,
    comment_internal=None,
    comment_for_participant=None,
    annotations=None,
    rubric=None,
):
    """Zapis szkicu recenzji. Bez walidacji finalnej – ocena może być jeszcze niepełna.

    Rubryka w szkicu jest przyjmowana **częściowo** (``partial=True``): recenzent zapisuje robotę
    w połowie zadania i kryteria, do których nie doszedł, zostają puste. Suma ląduje w ``score``
    dopiero, gdy wypełnione są wszystkie kryteria – i nawet wtedy szkic nie sprawdza jej zgodności
    ze skalą, bo to jest właśnie ta decyzja, którą recenzent domyka przy wystawianiu oceny.
    """
    _assert_review_open(review, review.submission.status)
    fields: list[str] = []
    if rubric is not None:
        items, total = validate_rubric(review.submission.problem, rubric, partial=True)
        review.rubric = items
        fields.append("rubric")
        if is_complete(items):
            score = total
    if score is not None:
        review.score = score
        fields.append("score")
    if comment_internal is not None:
        review.comment_internal = _clean_comment(comment_internal)
        fields.append("comment_internal")
    if comment_for_participant is not None:
        review.comment_for_participant = _clean_comment(comment_for_participant)
        fields.append("comment_for_participant")
    if annotations is not None:
        review.annotations = validate_annotations(annotations)
        fields.append("annotations")
    if review.status != ReviewStatus.DRAFT:
        review.status = ReviewStatus.DRAFT
        fields.append("status")
    if fields:
        review.save(update_fields=fields)
    return review


def _create_final_grade(
    submission: Submission, *, score: int, method: str, decided_by=None, rationale: str = "", request=None
) -> FinalGrade:
    """Tworzy ocenę uzgodnioną i przenosi zgłoszenie do GRADED_PROVISIONAL.

    Wołane wyłącznie spod blokady ``select_for_update`` na ``submission`` – dzięki temu wyścig
    dwóch recenzji nie może utworzyć dwóch ocen. Relacja jeden-do-jednego zatrzymałaby drugi
    zapis w bazie, ale użytkownik dostałby wtedy 500 zamiast poprawnego przebiegu.
    """
    if FinalGrade.objects.filter(submission=submission).exists():
        raise _conflict("Rozwiązanie ma już ocenę uzgodnioną.", "ALREADY_GRADED")
    grade = FinalGrade.objects.create(
        submission=submission,
        score=score,
        method=method,
        decided_by=decided_by,
        decided_at=timezone.now(),
        rationale=_clean_comment(rationale),
    )
    submission.status = SubmissionStatus.GRADED_PROVISIONAL
    submission.save(update_fields=["status"])
    audit(
        decided_by,
        "grade.decided",
        grade,
        {"submission_id": submission.pk, "score": score, "method": method},
        request=request,
    )
    _cancel_pending_tiebreak(submission, actor=decided_by, request=request)
    return grade


def _cancel_pending_tiebreak(
    submission: Submission, *, reason: str = "MODERATION_RESOLVED", actor=None, request=None
) -> int:
    """Anuluje niewystawione recenzje rundy 2, gdy rozjazd przestał istnieć.

    Gdy rozjazd rozstrzygnie koordynator, przydział trzeciego recenzenta traci przedmiot. Zostawiony
    w ASSIGNED wisiałby na jego liście zadań i pozwalał dopisać ocenę do zamkniętej już pracy.
    Rekord zostaje (ślad po przydziale jest częścią historii), zmienia się tylko status.

    ``reason`` idzie do audytu, bo powody bywają różne i rozjemca ma prawo wiedzieć, dlaczego
    zadanie zniknęło mu z listy: rozstrzygnięcie moderacji to nie to samo, co poprawiona recenzja
    rundy 1, po której oceny znów są zgodne.
    """
    pending = list(
        Review.objects.filter(submission=submission, round=ROUND_TIEBREAK).exclude(
            status__in=(ReviewStatus.SUBMITTED, ReviewStatus.CANCELLED)
        )
    )
    for review in pending:
        _cancel_review(review, reason=reason)
        audit(
            actor,
            "review.cancelled",
            review,
            {"submission_id": submission.pk, "round": review.round, "reason": reason},
            request=request,
        )
    return len(pending)


def _round_one_reviews(submission: Submission) -> list[Review]:
    """Recenzje rundy 1, które liczą się do rozstrzygnięcia – bez anulowanych (odebranych)."""
    return list(
        Review.objects.filter(submission=submission, round=ROUND_BLIND).exclude(status=ReviewStatus.CANCELLED)
    )


def _counts_towards_consensus(review: Review) -> bool:
    """Czy ta recenzja bierze udział w ustalaniu zgodności ocen.

    Recenzja **bez roli** liczy się zawsze – i to jest całe zachowanie sprzed etapu 2, bo przed nim
    żadna recenzja roli nie miała. Rolę, która do zgodności się nie liczy, wskazuje organizator
    (``ReviewerRole.counts_towards_consensus``, § 1.2.7): tak wchodzi przewodniczący komisji
    piszący własną opinię obok dwóch niezależnych recenzji, nie unieważniając ich zgodności.
    """
    return review.role_id is None or review.role.counts_towards_consensus


def _consensus_score(reviews: list[Review]) -> int | None:
    """Wspólna ocena kompletnej rundy 1 albo ``None`` (runda niekompletna lub oceny różne).

    Wydzielone z ``_settle_round_one``, bo o zgodność ocen pyta też poprawka recenzji: zanim
    powstanie ocena uzgodniona, trzeba zdjąć wiszący przydział rozjemczy z własnym powodem
    w audycie.

    Recenzje z ról nieliczących się do zgodności odpadają z tego rachunku **przed** sprawdzeniem
    kompletu: gdyby odpadały dopiero z porównania ocen, nieoddana opinia przewodniczącego
    trzymałaby pracę w ocenianiu mimo dwóch zgodnych recenzji. W etapie bez ról (czyli w całym
    Konkursie #1) lista wchodzi tu i wychodzi stąd nietknięta.
    """
    reviews = [review for review in reviews if _counts_towards_consensus(review)]
    if not reviews or not all(review.is_submitted for review in reviews):
        return None
    scores = {review.score for review in reviews}
    return scores.pop() if len(scores) == 1 else None


def _settle_round_one(submission: Submission, *, request=None) -> None:
    """Po komplecie ocen rundy 1: zgodne → ``FinalGrade(CONSENSUS)``, różne → moderacja.

    Rozstrzyga faktyczna liczba recenzji rundy 1, a nie założone „dwie”. Przy ``per_submission=1``
    (przydział awaryjny) jedna wystawiona ocena też domyka sprawę – inaczej praca zostawałaby
    na zawsze w IN_REVIEW, bo drugiej oceny nie miałby kto wystawić.
    """
    reviews = _round_one_reviews(submission)
    agreed = _consensus_score(reviews)
    if agreed is not None:
        # Konsensus nie ma człowieka rozstrzygającego – decyduje reguła, stąd decided_by=None.
        _create_final_grade(
            submission,
            score=agreed,
            method=GradeMethod.CONSENSUS,
            decided_by=None,
            rationale=f"Zgodne oceny niezależnych recenzentów ({len(reviews)}).",
            request=request,
        )
        return
    if not reviews or not all(review.is_submitted for review in reviews):
        return
    scores = {review.score for review in reviews}
    if submission.status != SubmissionStatus.MODERATION:
        submission.status = SubmissionStatus.MODERATION
        submission.save(update_fields=["status"])
        audit(
            None,
            "submission.moderation",
            submission,
            {"scores": sorted(score for score in scores if score is not None)},
            request=request,
        )


def _score_from_rubric(submission: Submission, score, rubric) -> tuple[int, list[dict] | None]:
    """Uzgadnia ocenę z rubryką: gdy rubryka przyszła, **ona** wyznacza punkty.

    Zwraca ``(score, items)``; ``items`` to ``None``, gdy rubryki nie przysłano albo zadanie jej
    nie ma – wtedy zapis przebiega dokładnie jak dotąd, a stary klient API nie przestaje działać.

    Suma jest sprawdzana względem skali **przed** zapisem i bez zaokrąglania: recenzent, którego
    kryteria zsumowały się do 4 przy skali 0/2/5/6, dostaje listę dopuszczalnych wartości i sam
    rozstrzyga, gdzie ocenił za wysoko. Cicha korekta byłaby zmianą jego decyzji, a nie literówki.
    """
    if rubric is None:
        return score, None
    items, total = validate_rubric(submission.problem, rubric)
    if not items:
        return score, None
    assert_total_in_scale(total, allowed_scores(submission.entry.stage, submission.problem))
    return total, items


@transaction.atomic
def submit_review(
    review: Review,
    score,
    comment_internal: str = "",
    comment_for_participant: str = "",
    annotations=None,
    *,
    rubric=None,
    request=None,
) -> Review:
    """Wystawia ocenę i rozstrzyga dalszy los zgłoszenia (PROJEKT.md 2.4).

    Kolejność blokad jest stała: najpierw ``Submission`` (``select_for_update``), potem recenzja.
    Dzięki temu dwa równoczesne wystawienia ocen szeregują się i tylko jedno z nich domyka rundę.

    ``rubric`` (punkty cząstkowe za kryteria zadania) jest opcjonalna: zadanie bez rubryki i klient,
    który jej nie przysyła, zachowują się jak dotąd. Gdy przyjdzie – to ona wyznacza ``score``,
    a przysłana ocena jest ignorowana, bo suma liczy się po stronie serwera.
    """
    submission = _locked_submission(review.submission_id)
    review = Review.objects.select_related("reviewer", "reviewer__user").get(pk=review.pk)

    _assert_review_open(review, submission.status)
    score, rubric_items = _score_from_rubric(submission, score, rubric)
    score = _assert_score_in_scale(submission.entry.stage, score, submission.problem)
    cleaned_annotations = validate_annotations(annotations)

    review.score = score
    review.comment_internal = _clean_comment(comment_internal)
    review.comment_for_participant = _clean_comment(comment_for_participant)
    review.annotations = cleaned_annotations
    review.status = ReviewStatus.SUBMITTED
    review.submitted_at = timezone.now()
    update_fields = [
        "score",
        "comment_internal",
        "comment_for_participant",
        "annotations",
        "status",
        "submitted_at",
    ]
    if rubric_items is not None:
        review.rubric = rubric_items
        update_fields.append("rubric")
    review.save(update_fields=update_fields)
    audit(
        review.reviewer.user,
        "review.submitted",
        review,
        {"submission_id": submission.pk, "round": review.round, "score": score},
        request=request,
    )

    if review.round == ROUND_TIEBREAK:
        # ``rationale`` bierze się z ``comment_internal``, a nie z ``comment_for_participant``,
        # świadomie: to ślad procedury dla koordynatora i komisji odwoławczej – dlaczego rozjazd
        # rozstrzygnięto tak, a nie inaczej. Komentarz dla uczestnika bywa pusty (trzeci recenzent
        # pisze przede wszystkim do komisji), więc oparcie o niego zostawiałoby ocenę bez
        # uzasadnienia w aktach. Ochroną nie jest tu treść pola, tylko warstwa prezentacji:
        # ``submissions.serializers.SubmissionFinalGradeSerializer`` oddaje ``rationale``
        # wyłącznie dla ``method=APPEAL``, więc do uczestnika ten tekst nie trafia (PROJEKT.md 2.4).
        _create_final_grade(
            submission,
            score=score,
            method=GradeMethod.THIRD_REVIEW,
            decided_by=review.reviewer.user,
            rationale=review.comment_internal,
            request=request,
        )
    else:
        _settle_round_one(submission, request=request)
    review.submission = submission
    return review


# --- poprawa własnej oceny i odebranie pracy ----------------------------------------------------

#: Komunikaty odmowy dla poprawiania własnej recenzji (recenzent) i odbierania pracy (koordynator).
#: Jeden słownik na oba tryby, bo powody są te same: to ta sama praca i ta sama ocena, tylko raz
#: pyta o nią autor recenzji, a raz organizator. Widok czyta stąd podpowiedź, serwis – treść 409.
GRADE_CHANGE_BLOCK_MESSAGES = {
    "REVIEW_CANCELLED": "Koordynator odebrał Ci tę pracę – recenzji nie można już zmienić.",
    "REVIEW_NOT_SUBMITTED": "Ta recenzja nie została jeszcze wystawiona – wyślij ocenę zwykłą drogą.",
    "ALREADY_CANCELLED": "Ta recenzja została już odebrana.",
    "RESULTS_PUBLISHED": "Wyniki tego etapu są już ogłoszone – oceny nie da się zmienić.",
    "SUBMISSION_CLOSED": "Ta praca jest zamknięta – oceny nie da się już zmienić.",
    "GRADE_DECIDED": "Ocenę tej pracy rozstrzygnął już koordynator albo trzeci recenzent.",
}


def _stage_and_submission_block(review: Review) -> str | None:
    """Bramka wspólna obu trybom: ogłoszone wyniki etapu i stan pracy.

    Ogłoszona tabela jest dokumentem z chwili publikacji – cicha zmiana oceny rozjeżdżałaby akta
    z tym, co ludzie już przeczytali. Praca w reklamacji należy do komisji odwoławczej, a praca
    finalna ma etap dawno za sobą; w obu przypadkach od zmiany oceny jest procedura odwoławcza
    albo korekta koordynatora, a nie recenzent wracający do swojego formularza.
    """
    submission = review.submission
    if _results_published(submission.entry.stage):
        return "RESULTS_PUBLISHED"
    if submission.status in (SubmissionStatus.FINAL, SubmissionStatus.APPEALED):
        return "SUBMISSION_CLOSED"
    return None


def revision_block_reason(review: Review) -> str | None:
    """Kod powodu, dla którego recenzent nie może już poprawić tej recenzji (albo ``None``).

    Ta sama funkcja odpowiada widokowi („pokazać formularz poprawki czy podpowiedź, dlaczego nie”)
    i serwisowi („czym odmówić”) – inaczej ekran obiecywałby coś, czego zapis nie przyjmie.

    Oceny rozstrzygniętej przez człowieka – moderacja, trzeci recenzent, korekta koordynatora,
    decyzja reklamacyjna – recenzent rundy 1 już nie rusza: jego punkty przestały być podstawą
    oceny końcowej, a cicha zmiana w tle podważałaby cudze rozstrzygnięcie. Inaczej w rundzie 2:
    tam ocena końcowa *jest* tą recenzją, więc poprawiać ją wolno dokładnie tak długo, jak długo
    w aktach stoi ocena wystawiona przez tego recenzenta (``THIRD_REVIEW``).
    """
    if review.status == ReviewStatus.CANCELLED:
        return "REVIEW_CANCELLED"
    if review.status != ReviewStatus.SUBMITTED:
        return "REVIEW_NOT_SUBMITTED"
    blocked = _stage_and_submission_block(review)
    if blocked is not None:
        return blocked
    grade = FinalGrade.objects.filter(submission_id=review.submission_id).first()
    if review.round == ROUND_TIEBREAK:
        if grade is None or grade.method != GradeMethod.THIRD_REVIEW:
            return "GRADE_DECIDED"
        return None
    if grade is not None and grade.method != GradeMethod.CONSENSUS:
        return "GRADE_DECIDED"
    return None


def withdrawal_block_reason(review: Review) -> str | None:
    """Kod powodu, dla którego koordynator nie może już odebrać tej recenzji (albo ``None``).

    Odebrać wolno recenzję w każdym stanie poza anulowaną – przydzieloną, szkic i wystawioną.
    Blokuje dopiero rozstrzygnięcie stojące ponad recenzją: ogłoszone wyniki, praca zamknięta
    i ocena końcowa ustalona przez człowieka. Jeden warunek na ocenę wystarcza na oba przypadki
    z prośby organizatora: ocena konsensusu jest pochodną recenzji (odebranie jednej z nich ją
    znosi), a każda inna – łącznie z oceną trzeciego recenzenta (``THIRD_REVIEW``) – jest czyjąś
    decyzją i zmienia się wyłącznie przez ``override_final_grade``, z uzasadnieniem w aktach.
    """
    if review.status == ReviewStatus.CANCELLED:
        return "ALREADY_CANCELLED"
    blocked = _stage_and_submission_block(review)
    if blocked is not None:
        return blocked
    grade = FinalGrade.objects.filter(submission_id=review.submission_id).first()
    if grade is not None and grade.method != GradeMethod.CONSENSUS:
        return "GRADE_DECIDED"
    return None


def _withdraw_consensus_grade(submission: Submission, *, reason: str, actor=None, request=None) -> bool:
    """Zdejmuje ocenę uzgodnioną konsensusem i zawraca pracę do oceniania (IN_REVIEW).

    Konsensus nie jest niczyją decyzją, tylko skutkiem zgodności dwóch ocen. Gdy jedna z nich się
    zmieniła albo wypadła, podstawa zniknęła – zostawienie oceny byłoby trzymaniem wyniku, którego
    nic już nie potwierdza. Kasujemy więc wiersz (a nie „poprawiamy” go w miejscu), bo zaraz po tym
    ``_settle_round_one`` policzy rundę od nowa i utworzy ocenę zgodną z bieżącym stanem.

    Ślad zostaje w audycie ``grade.withdrawn`` – z punktami i powodem, bo skasowanego wiersza nie
    da się już o nic zapytać. Celem wpisu jest zgłoszenie, nie skasowana ocena.
    """
    grade = FinalGrade.objects.filter(submission=submission, method=GradeMethod.CONSENSUS).first()
    if grade is None:
        return False
    score = grade.score
    grade.delete()
    submission.status = SubmissionStatus.IN_REVIEW
    submission.save(update_fields=["status"])
    audit(
        actor,
        "grade.withdrawn",
        submission,
        {"submission_id": submission.pk, "score": score, "method": GradeMethod.CONSENSUS, "reason": reason},
        request=request,
    )
    return True


def _update_tiebreak_grade(submission: Submission, review: Review, score: int, *, request=None) -> None:
    """Poprawka rozjemcy przepisuje ocenę końcową w miejscu – to wciąż to samo rozstrzygnięcie.

    Kasowanie i tworzenie oceny od nowa byłoby tu nieuczciwe wobec historii: tryb, autor i powód
    się nie zmieniają, zmienia się liczba punktów. Stan pracy zostaje (``GRADED_PROVISIONAL``),
    bo rozjazd jest nadal rozstrzygnięty.
    """
    grade = FinalGrade.objects.get(submission=submission)
    previous = grade.score
    grade.score = score
    grade.decided_at = timezone.now()
    grade.rationale = _clean_comment(review.comment_internal)
    grade.save(update_fields=["score", "decided_at", "rationale"])
    audit(
        review.reviewer.user,
        "grade.updated",
        grade,
        {"submission_id": submission.pk, "from": previous, "to": score, "method": grade.method},
        request=request,
    )


@transaction.atomic
def revise_review(
    review: Review,
    score,
    comment_internal: str = "",
    comment_for_participant: str = "",
    annotations=None,
    *,
    rubric=None,
    request=None,
) -> Review:
    """Recenzent poprawia własną, już wystawioną ocenę (prośba organizatora, PROJEKT.md 2.4).

    Wystawiona recenzja przestała być nieodwracalna: dopóki nikt nie zbudował na niej
    rozstrzygnięcia, jej autor może ją zmienić. Granicę wyznacza ``revision_block_reason`` –
    w szczególności praca odebrana przez koordynatora (``CANCELLED``) jest poza zasięgiem.

    Kolejność blokad jak w ``submit_review``: najpierw ``Submission``, potem recenzja. Rundę 1
    serwis przelicza od nowa, bo poprawka zmienia właśnie to, z czego liczy się konsensus:
    ocena uzgodniona konsensusem znika, praca wraca do IN_REVIEW i dopiero wtedy
    ``_settle_round_one`` rozstrzyga ją zgodnie z nowym stanem (znów zgodne → konsensus,
    rozjazd → moderacja). ``submitted_at`` zostaje nietknięte – chwila pierwszego wystawienia
    oceny jest faktem, poprawka dopisuje ``revised_at``.
    """
    submission = _locked_submission(review.submission_id)
    review = Review.objects.select_related("reviewer", "reviewer__user").get(pk=review.pk)
    review.submission = submission

    reason = revision_block_reason(review)
    if reason is not None:
        raise _conflict(GRADE_CHANGE_BLOCK_MESSAGES[reason], reason)
    score, rubric_items = _score_from_rubric(submission, score, rubric)
    score = _assert_score_in_scale(submission.entry.stage, score, submission.problem)
    cleaned_annotations = validate_annotations(annotations)

    previous = review.score
    review.score = score
    review.comment_internal = _clean_comment(comment_internal)
    review.comment_for_participant = _clean_comment(comment_for_participant)
    review.annotations = cleaned_annotations
    review.revised_at = timezone.now()
    update_fields = [
        "score",
        "comment_internal",
        "comment_for_participant",
        "annotations",
        "revised_at",
    ]
    if rubric_items is not None:
        review.rubric = rubric_items
        update_fields.append("rubric")
    review.save(update_fields=update_fields)
    audit(
        review.reviewer.user,
        "review.revised",
        review,
        {"submission_id": submission.pk, "round": review.round, "from": previous, "to": score},
        request=request,
    )

    if review.round == ROUND_TIEBREAK:
        _update_tiebreak_grade(submission, review, score, request=request)
    else:
        _withdraw_consensus_grade(
            submission, reason="REVIEW_REVISED", actor=review.reviewer.user, request=request
        )
        if _consensus_score(_round_one_reviews(submission)) is not None:
            # Rozjazd zniknął wraz z poprawką, więc wiszący przydział rozjemczy traci przedmiot
            # jeszcze **przed** utworzeniem oceny uzgodnionej. Inaczej anulowałby go
            # ``_create_final_grade`` z powodem „rozstrzygnięta moderacja”, a rozjemca ma w aktach
            # zobaczyć prawdziwą przyczynę: recenzent poprawił swoją ocenę.
            _cancel_pending_tiebreak(
                submission, reason="REVIEW_REVISED", actor=review.reviewer.user, request=request
            )
        _settle_round_one(submission, request=request)
    review.submission = submission
    return review


# --- korekta ocen przez koordynatora -----------------------------------------------------------

#: Stany zgłoszenia, w których koordynator może wpisać albo poprawić ocenę końcową. Poza pracami
#: w obiegu oceniania są tu także prace już ocenione – korekta po fakcie jest właśnie tym, po co
#: ten tryb powstał. Nie ma tu stanów sprzed zamknięcia etapu: przed deadline uczestnik może
#: jeszcze podmienić plik, więc wpisana ocena dotyczyłaby wersji, której nikt nie widział.
GRADABLE_STATUSES = (
    SubmissionStatus.MODERATION,
    SubmissionStatus.GRADED_PROVISIONAL,
    SubmissionStatus.APPEALED,
    SubmissionStatus.FINAL,
)
#: Minimalna długość uzasadnienia korekty. Krótsze nie jest uzasadnieniem, tylko kliknięciem –
#: a ten wpis czyta potem komisja odwoławcza.
MIN_OVERRIDE_RATIONALE = 10


def _cancel_pending_reviews(submission: Submission, *, reason: str, actor=None, request=None) -> int:
    """Anuluje wszystkie niedokończone recenzje pracy (ASSIGNED i DRAFT), zostawiając wystawione.

    Ta sama reguła, co przy rozstrzygniętym rozjeździe (``_cancel_pending_tiebreak``), tylko dla
    obu rund: praca z wpisaną oceną końcową nie ma już czego dostarczyć recenzentowi, a wiszący
    przydział zostawiałby mu na liście zadanie bez przedmiotu. Szkic anulujemy razem z przydziałem –
    jest tak samo niedokończony i tak samo stoi w kolejce.
    """
    pending = list(
        Review.objects.filter(submission=submission).exclude(
            status__in=(ReviewStatus.SUBMITTED, ReviewStatus.CANCELLED)
        )
    )
    for review in pending:
        _cancel_review(review, reason=reason)
        audit(
            actor,
            "review.cancelled",
            review,
            {"submission_id": submission.pk, "round": review.round, "reason": reason},
            request=request,
        )
    return len(pending)


def _results_published(stage: Stage) -> bool:
    """Czy etap ma już ogłoszoną tabelę wyników.

    Import lokalny: ``apps.results.services`` woła ocenianie, więc import na poziomie modułu
    zamykałby cykl.
    """
    from apps.results.models import ResultsPublication

    return ResultsPublication.objects.filter(stage=stage).exists()


@transaction.atomic
def set_review_score(review: Review, score, *, actor=None, request=None, rationale: str = "") -> Review:
    """Koordynator wpisuje albo poprawia punkty pojedynczej recenzji.

    Różnica wobec ``submit_review``: nie ma tu bramki stanu recenzji ani zgłoszenia. Koordynator
    poprawia także ocenę już wystawioną i wpisuje ocenę za recenzenta, który jej nie oddał – to
    jest cel tego trybu. Jedyne, co obowiązuje bez wyjątku, to skala punktacji etapu: ocena spoza
    skali rozjeżdżałaby tabelę wyników i próg kwalifikacji.

    Ślad po tym, że punkty pochodzą od koordynatora, zostaje w dwóch miejscach: w audycie
    (``review.score_set_by_coordinator`` z wartością przed i po) oraz – dla recenzji, której nikt
    nie wystawił – w komentarzu wewnętrznym. Recenzent musi wiedzieć, skąd w jego recenzji wzięła
    się ocena, której nie wpisał.

    Po zmianie serwis ponawia rozstrzygnięcie rundy 1 (``_settle_round_one``), żeby konsensus
    i kolejka moderacji zgadzały się z nowym stanem ocen. Praca, która ma już ocenę uzgodnioną,
    jest z tego wyłączona: zmiana oceny końcowej po fakcie to osobna, świadoma czynność
    (``override_final_grade``) – wymaga uzasadnienia i ma własny tryb w tabeli wyników.
    """
    submission = _locked_submission(review.submission_id)
    review = Review.objects.select_related("reviewer", "reviewer__user").get(pk=review.pk)
    score = _assert_score_in_scale(submission.entry.stage, score, submission.problem)

    previous = review.score
    fields = ["score"]
    review.score = score
    if review.status != ReviewStatus.SUBMITTED:
        note = "[koordynator] Punkty wpisane przez koordynatora."
        if rationale:
            note = f"{note} {rationale}"
        review.comment_internal = _clean_comment(
            f"{review.comment_internal}\n{note}".strip() if review.comment_internal else note
        )
        review.status = ReviewStatus.SUBMITTED
        review.submitted_at = timezone.now()
        fields += ["comment_internal", "status", "submitted_at"]
    review.save(update_fields=fields)

    audit(
        actor,
        "review.score_set_by_coordinator",
        review,
        {
            "submission_id": submission.pk,
            "round": review.round,
            "from": previous,
            "to": score,
            "rationale": _clean_comment(rationale),
        },
        request=request,
    )
    if not FinalGrade.objects.filter(submission=submission).exists():
        _settle_round_one(submission, request=request)
    review.submission = submission
    return review


@transaction.atomic
def override_final_grade(submission: Submission, score, *, rationale: str, actor=None, request=None) -> dict:
    """Koordynator wpisuje albo zmienia ocenę końcową pracy – także pracy bez ani jednej recenzji.

    Tryb ostatniej instancji: praca, której nikt nie zrecenzował (brak chętnych, awaria, decyzja
    komisji o ocenie na posiedzeniu), oraz korekta oceny już ustalonej – konsensusem, moderacją
    czy trzecim recenzentem. Ocena dostaje własny tryb ``COORDINATOR_OVERRIDE``, żeby w aktach
    było widać, że wzięła się z decyzji organizatora, a nie z procedury oceniania.

    Uzasadnienie jest **obowiązkowe**: to jedyna rzecz, po której da się później odtworzyć, czemu
    ocena wygląda tak, a nie inaczej – a czyta ją komisja odwoławcza.

    Ogłoszona tabela wyników **nie jest** ruszana. Snapshot jest dokumentem z chwili publikacji;
    zmiana oceny po ogłoszeniu wchodzi do wyników dopiero przez ponowne przeliczenie i publikację.
    Zamiast cicho rozjeżdżać jedno z drugim, serwis zwraca ``results_stale=True`` i to panel mówi
    koordynatorowi, co jeszcze zostało do zrobienia.
    """
    locked = _locked_submission(submission.pk)
    stage = locked.entry.stage
    rationale = (rationale or "").strip()
    if len(rationale) < MIN_OVERRIDE_RATIONALE:
        raise _bad_request(
            f"Korekta oceny wymaga uzasadnienia (co najmniej {MIN_OVERRIDE_RATIONALE} znaków).",
            "RATIONALE_REQUIRED",
        )
    if not is_assignable(locked) and locked.status not in GRADABLE_STATUSES:
        raise _conflict(
            f"Rozwiązanie w stanie {locked.status} nie przyjmuje oceny końcowej.",
            "SUBMISSION_NOT_GRADABLE",
        )
    score = _assert_score_in_scale(stage, score, locked.problem)

    grade = FinalGrade.objects.filter(submission=locked).first()
    previous = grade.score if grade is not None else None
    previous_method = grade.method if grade is not None else None
    if grade is None:
        grade = FinalGrade(submission=locked)
    grade.score = score
    grade.method = GradeMethod.COORDINATOR_OVERRIDE
    grade.decided_by = actor if getattr(actor, "is_authenticated", False) else None
    grade.decided_at = timezone.now()
    grade.rationale = _clean_comment(rationale)
    grade.save()

    cancelled = _cancel_pending_reviews(locked, reason="COORDINATOR_OVERRIDE", actor=actor, request=request)
    # Praca już finalna zostaje finalna: cofnięcie jej do oceny wstępnej otwierałoby z powrotem
    # okno reklamacji dla sprawy, którą etap ma dawno za sobą.
    if locked.status != SubmissionStatus.FINAL:
        locked.status = SubmissionStatus.GRADED_PROVISIONAL
        locked.save(update_fields=["status"])

    results_stale = _results_published(stage)
    audit(
        actor,
        "grade.overridden",
        grade,
        {
            "submission_id": locked.pk,
            "from": previous,
            "from_method": previous_method,
            "to": score,
            "cancelled_reviews": cancelled,
            "results_stale": results_stale,
        },
        request=request,
    )
    logger.info(
        "Korekta oceny koordynatora: zgłoszenie %s %s → %s (wyniki nieaktualne: %s)",
        locked.pk,
        previous,
        score,
        results_stale,
    )
    return {"grade": grade, "results_stale": results_stale, "cancelled_reviews": cancelled}


# --- nowa wersja pracy unieważnia rozpoczętą ocenę ----------------------------------------------

#: Stany starszej wersji, które nowa wersja unieważnia: praca była już w obiegu oceniania.
SUPERSEDABLE_STATUSES = (
    SubmissionStatus.LOCKED,
    SubmissionStatus.IN_REVIEW,
    SubmissionStatus.MODERATION,
    SubmissionStatus.GRADED_PROVISIONAL,
)
#: Stany, w których praca ma etap za sobą i unieważnienie jej oceny nie wchodzi w grę.
FINALISED_STATUSES = (SubmissionStatus.APPEALED, SubmissionStatus.FINAL)


@transaction.atomic
def supersede_earlier_versions(entry, problem, new_submission: Submission, *, request=None) -> dict:
    """Nowa wersja rozwiązania unieważnia ocenę rozpoczętą na wersjach wcześniejszych.

    Reguła powstała razem z ocenianiem przed zamknięciem etapu (``submissions.lock_for_review``).
    Skoro komitet zaczyna czytać prace, zanim minie deadline, a uczestnik do deadline'u może
    wysyłać kolejne wersje, muszą istnieć obie rzeczy naraz: rozpoczęta ocena i otwarte okno
    uploadu. Rozstrzygnięcie jest po stronie uczestnika – nowa wersja kasuje dotychczasową ocenę,
    a praca wraca do kolejki i zostanie oceniona od nowa.

    Dla każdej wcześniejszej wersji w stanie z ``SUPERSEDABLE_STATUSES``:

    - nieanulowane recenzje (także **wystawione**) przechodzą w ``CANCELLED`` z powodem
      ``SUPERSEDED`` – recenzent zobaczy w panelu, co się stało, zamiast pustego miejsca,
    - ``FinalGrade`` znika (``grade.withdrawn``, powód ``SUPERSEDED``): ocena wystawiona wersji,
      której nikt już nie czyta, nie może wejść do tabeli wyników,
    - wersja wraca do ``SUBMITTED``. Wiersz zostaje jako historia, ale przestaje się liczyć –
      ``close_stage``, ``lockable_submission_ids`` i ``_assignable_submissions`` i tak wybierają
      najnowszą wersję, więc do oceniania wejdzie nowa.

    Praca **finalna, w reklamacji albo z ogłoszonymi wynikami** jest poza zasięgiem: takiej oceny
    nie da się cicho unieważnić, bo uczestnik i komisja odwoławcza już ją znają. Stan ten nie może
    powstać przy otwartym etapie (wyniki ogłasza się po zamknięciu reklamacji, a zamknięty etap nie
    przyjmuje uploadu), więc warunek jest wyłącznie bezpiecznikiem – ale odmawia jawnie,
    ``SUBMISSION_FINALISED``, zamiast po cichu skasować rozstrzygnięcie.

    Aktorem wpisów audytowych jest uczestnik: to jego wysyłka spowodowała unieważnienie, a wpis bez
    aktora sugerowałby decyzję systemu albo koordynatora.
    """
    earlier = list(
        Submission.objects.select_for_update(of=("self",))
        .filter(entry=entry, problem=problem, version__lt=new_submission.version)
        .order_by("version")
    )
    finalised = [item for item in earlier if item.status in FINALISED_STATUSES]
    touched = [item for item in earlier if item.status in SUPERSEDABLE_STATUSES]
    if not finalised and not touched:
        # Najczęstszy przypadek (poprzednie wersje zwyczajnie czekają na blokadę) kończy się tutaj,
        # bez dodatkowego pytania o publikację wyników.
        return {"superseded": 0, "cancelled_reviews": 0, "grades_withdrawn": 0}
    if finalised or _results_published(entry.stage):
        raise _conflict(
            "Ta praca ma już ocenę ostateczną – nowej wersji nie da się przyjąć.",
            "SUBMISSION_FINALISED",
        )

    actor = entry.participant.user
    cancelled_total = 0
    withdrawn_total = 0
    for submission in touched:
        cancelled = 0
        pending = Review.objects.filter(submission=submission).exclude(status=ReviewStatus.CANCELLED)
        for review in pending:
            _cancel_review(review, reason="SUPERSEDED")
            audit(
                actor,
                "review.cancelled",
                review,
                {"submission_id": submission.pk, "round": review.round, "reason": "SUPERSEDED"},
                request=request,
            )
            cancelled += 1
        grade = FinalGrade.objects.filter(submission=submission).first()
        grade_withdrawn = grade is not None
        if grade is not None:
            score, method = grade.score, grade.method
            grade.delete()
            # Celem wpisu jest zgłoszenie, a nie skasowana ocena: po ``delete()`` jej identyfikator
            # nie wskazuje już niczego, a punkty muszą zostać w aktach.
            audit(
                actor,
                "grade.withdrawn",
                submission,
                {
                    "submission_id": submission.pk,
                    "score": score,
                    "method": method,
                    "reason": "SUPERSEDED",
                },
                request=request,
            )
        submission.status = SubmissionStatus.SUBMITTED
        submission.save(update_fields=["status"])
        audit(
            actor,
            "submission.superseded",
            submission,
            {
                "old_id": submission.pk,
                "new_id": new_submission.pk,
                "cancelled_reviews": cancelled,
                "grade_withdrawn": grade_withdrawn,
            },
            request=request,
        )
        cancelled_total += cancelled
        withdrawn_total += int(grade_withdrawn)

    logger.info(
        "Zgłoszenie %s zastąpiło %s wcześniejszych wersji (anulowane recenzje: %s, zdjęte oceny: %s)",
        new_submission.pk,
        len(touched),
        cancelled_total,
        withdrawn_total,
    )
    return {
        "superseded": len(touched),
        "cancelled_reviews": cancelled_total,
        "grades_withdrawn": withdrawn_total,
    }


# --- moderacja --------------------------------------------------------------------------------


def _resolution_method(submission: Submission, actor) -> tuple[str, Review | None]:
    """Tryb rozstrzygnięcia dostępny dla tego aktora: koordynator albo trzeci recenzent."""
    if is_coordinator(actor):
        return GradeMethod.MODERATION, None
    member = active_reviewer_profile(actor)
    if member is not None:
        third = (
            Review.objects.filter(submission=submission, reviewer=member, round=ROUND_TIEBREAK)
            .exclude(status=ReviewStatus.CANCELLED)
            .first()
        )
        if third is not None:
            return GradeMethod.THIRD_REVIEW, third
    raise DomainError(
        "Rozstrzygnąć rozjazd może koordynator albo wyznaczony trzeci recenzent.",
        "NOT_ALLOWED_TO_RESOLVE",
        http.HTTP_403_FORBIDDEN,
    )


@transaction.atomic
def resolve_moderation(
    submission: Submission, actor, score, method: str | None = None, rationale: str = "", *, request=None
) -> FinalGrade:
    """Rozstrzyga rozjazd ocen: koordynator (MODERATION) albo trzeci recenzent (THIRD_REVIEW)."""
    locked = _locked_submission(submission.pk)
    allowed_method, third_review = _resolution_method(locked, actor)
    if method is not None and method != allowed_method:
        raise DomainError(
            f"Ten tryb rozstrzygnięcia nie jest dostępny dla tego konta (dozwolony: {allowed_method}).",
            "NOT_ALLOWED_TO_RESOLVE",
            http.HTTP_403_FORBIDDEN,
        )
    if locked.status != SubmissionStatus.MODERATION:
        raise _conflict("Rozwiązanie nie jest w moderacji.", "NOT_IN_MODERATION")
    score = _assert_score_in_scale(locked.entry.stage, score, locked.problem)

    if third_review is not None:
        # Rozstrzygnięcie trzeciego recenzenta *jest* jego oceną, więc idzie tą samą drogą co każda
        # inna: ``submit_review`` zapisze recenzję, zostawi wpis ``review.submitted`` w audycie
        # i sam utworzy ``FinalGrade(THIRD_REVIEW)``. Osobna ścieżka dawała tu inny ślad audytowy
        # dla tej samej czynności, w zależności od wywołanego endpointu.
        submit_review(third_review, score, _clean_comment(rationale), "", None, request=request)
        return FinalGrade.objects.get(submission=locked)

    return _create_final_grade(
        locked,
        score=score,
        method=allowed_method,
        decided_by=actor if getattr(actor, "is_authenticated", False) else None,
        rationale=rationale,
        request=request,
    )


# --- zapytania dla API ------------------------------------------------------------------------


def reviews_for_reviewer(member: CommitteeMember | None, competition=None):
    """Przydziały recenzenta. Filtr jest w queryseckie, nie w widoku (PROJEKT.md 2.3).

    Zakres konkursu idzie **przed** przydziałem (§ 3.5): recenzent w komitetach dwóch olimpiad ma
    pod domeną A widzieć wyłącznie kolejkę A. Samo ``for_reviewer`` tego nie załatwia – profil
    komitetu jest dziś jeden na konto, więc bez zawężenia obie kolejki zlałyby się w jedną.
    """
    from apps.competitions.scoping import scope_to_competition

    return (
        scope_to_competition(Review.objects.for_reviewer(member), competition)
        .select_related(
            "submission",
            "submission__entry",
            "submission__entry__participant",
            "submission__entry__stage",
            "submission__problem",
        )
        .prefetch_related(
            # Jawny Prefetch z posortowanym querysetem: ``Submission.latest_file`` korzysta wtedy
            # z cache'u prefetchu zamiast robić własne ``order_by`` per wiersz (N+1 na liście).
            models.Prefetch("submission__files", queryset=SubmissionFile.objects.order_by("-id"))
        )
        .order_by("submission__entry__stage_id", "submission__problem__number", "id")
    )


#: Nazwa pliku, pod którą recenzent dostaje swoją paczkę. Jedna dla panelu i dla API – recenzent,
#: który pobrał ją raz stamtąd, a raz stąd, ma mieć w katalogu ten sam plik, a nie dwa różne.
REVIEWER_ZIP_FILENAME = "moje-prace.zip"


#: Nazwa paczki recenzenta zawężonej do uczniów z potwierdzonym statusem ucznia – inna niż
#: :data:`REVIEWER_ZIP_FILENAME`, bo dwie paczki o różnej treści nie mogą w katalogu nosić jednej nazwy.
REVIEWER_VERIFIED_ZIP_FILENAME = "moje-prace-status-potwierdzony.zip"


def build_reviewer_zip(
    member: CommitteeMember | None, *, actor=None, request=None, verified_only: bool = False
):
    """Paczka ZIP ze wszystkimi pracami przydzielonymi recenzentowi (prośba organizatora).

    Zakres to dokładnie to, co recenzent widzi w panelu jako „do zrobienia i zrobione”: recenzje
    poza stanem ``CANCELLED``. Praca odebrana przez koordynatora albo unieważniona nową wersją
    rozwiązania nie wchodzi – recenzent nie ma już przy niej nic do zrobienia, a pobranie pliku,
    którego nie wolno mu ocenić, byłoby dostępem bez podstawy.

    Nazwy plików są anonimowe (``<kod>_zad<numer>_v<wersja>``), bo ocenianie jest ślepe.
    ``README.txt`` wiąże numer recenzji z plikiem: bez tego recenzent z kilkunastoma pracami nie
    ma jak odnaleźć w panelu tej, którą właśnie przeczytał.

    ``verified_only`` (prośba organizatora z 24.09.2026) zostawia w paczce wyłącznie prace uczniów
    z **zaakceptowanym** zaświadczeniem o statusie ucznia w edycji tej pracy. Anonimowość zostaje
    nietknięta: recenzent dostaje mniej plików o tych samych anonimowych nazwach, a nie skan,
    nazwisko czy szkołę – o tym, kto ma status potwierdzony, rozstrzyga zapytanie po stronie
    serwera, a do paczki trafia wyłącznie jego skutek. Jedyna wiedza, którą recenzent może z tego
    wyprowadzić, to „ten pseudonim ma potwierdzony status” – informacja o **pseudonimie**, która nie
    przybliża go do tożsamości autora.

    Pusta kolejka to 404, a nie pusty plik ZIP: „nie mam co pobierać” jest odpowiedzią, a archiwum
    z samym spisem treści wyglądałoby jak awaria pobierania.
    """
    from apps.submissions.packaging import build_zip

    reviews = list(reviews_for_reviewer(member).exclude(status=ReviewStatus.CANCELLED))
    submissions: list[Submission] = []
    review_ids: dict[int, list[int]] = {}
    for review in reviews:
        if review.submission_id not in review_ids:
            review_ids[review.submission_id] = []
            submissions.append(review.submission)
        review_ids[review.submission_id].append(review.pk)
    if verified_only:
        from apps.student_status.services import accepted_pairs

        accepted = accepted_pairs(
            {item.entry.participant_id for item in submissions if item.entry.participant_id}
        )
        submissions = [
            item
            for item in submissions
            if (item.entry.participant_id, item.entry.stage.edition_id) in accepted
        ]

    header = "Prace przydzielone do oceny. Nazwy plików są anonimowe – ocenianie jest ślepe."
    if verified_only:
        header += " Wyłącznie prace uczniów z potwierdzonym statusem ucznia."
    package = build_zip(
        submissions,
        readme_lines=lambda submission, name: (
            f"recenzja {', '.join(str(value) for value in review_ids[submission.pk])} → {name}"
        ),
        header=header,
    )
    if package.count == 0:
        package.stream.close()
        raise DomainError(
            "Brak przydzielonych prac do pobrania."
            + (" Żaden z autorów nie ma jeszcze potwierdzonego statusu ucznia." if verified_only else ""),
            "NO_ASSIGNED_SUBMISSIONS",
            http.HTTP_404_NOT_FOUND,
        )
    diff = {"count": package.count}
    if verified_only:
        # Klucz wyłącznie przy zawężeniu – wpis zwykłej paczki zostaje taki, jak przed tym wydaniem.
        diff["verified_only"] = True
    audit(actor, "review.downloaded_zip", member, diff, request=request)
    return package


def stage_problem_rules(stage: Stage) -> list[dict]:
    """Zadania etapu razem z regułami „z góry” – materiał na tabelę w panelu koordynatora."""
    rules: dict[int, list[ProblemReviewerRule]] = {}
    for rule in (
        ProblemReviewerRule.objects.filter(problem__stage=stage)
        .select_related("reviewer", "reviewer__user")
        .order_by("created_at", "id")
    ):
        rules.setdefault(rule.problem_id, []).append(rule)
    return [
        {"problem": problem, "rules": rules.get(problem.pk, [])}
        for problem in stage.problems.order_by("number", "id")
    ]


def _is_clean(submission: Submission) -> bool:
    """Czy najnowszy plik pracy przeszedł skan – jedyne kryterium pobrania dla nie-właściciela."""
    submission_file = submission.latest_file
    return submission_file is not None and submission_file.is_clean


#: Filtry statusu ekranu „Przydziały i oceny”: klucz adresu → (etykieta, statusy pracy).
#:
#: Grupy, a nie surowe statusy z modelu, bo koordynator myśli krokami obiegu („co czeka na
#: przydział”, „co już ocenione”), a nie nazwami stanów: „ocenione” to trzy różne stany
#: dokumentacji tej samej pracy (wstępna, w reklamacji, ostateczna) i rozbicie ich na trzy pozycje
#: filtra kazałoby klikać trzy razy, żeby zobaczyć jedną rzecz.
ASSIGNMENT_STATUS_FILTERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "submitted": ("oddane", (SubmissionStatus.SUBMITTED,)),
    # „Do przydziału” to dokładnie ``LOCKED``: pierwszy przydział przestawia pracę na ``IN_REVIEW``,
    # więc praca zablokowana bez recenzenta nie ma innego stanu, w którym mogłaby czekać.
    "to_assign": ("do przydziału", (SubmissionStatus.LOCKED,)),
    "in_review": ("w ocenie", (SubmissionStatus.IN_REVIEW,)),
    "moderation": ("moderacja", (SubmissionStatus.MODERATION,)),
    "graded": (
        "ocenione",
        (SubmissionStatus.GRADED_PROVISIONAL, SubmissionStatus.APPEALED, SubmissionStatus.FINAL),
    ),
}


def stage_assignment_rows(
    stage: Stage,
    query: str = "",
    *,
    problem_id: int | None = None,
    status: str = "",
    reviewer_id: int | None = None,
) -> list[dict]:
    """Prace etapu w obiegu oceniania: recenzenci, oceny cząstkowe i ocena końcowa w jednym wierszu.

    Lista jest szersza niż sam przydział (``_assignable_submissions``) i celowo: ekran odpowiada
    także za korektę ocen, a poprawia się najczęściej pracę **już ocenioną**. Wiersz niosący
    ``assignable=False`` pokazuje więc oceny, ale nie formularz przydziału – recenzenta do pracy
    zamkniętej i tak nie da się dopisać.

    Filtr ``query`` działa na kodzie publicznym **i** na nazwisku: koordynator jest jedyną rolą,
    która zna jedno i drugie, a szuka raz tak, raz tak (kod z listy pominiętych prac, nazwisko
    z telefonu od uczestnika). Sortowanie po kodzie uczestnika, a potem po numerze zadania –
    prace jednej osoby mają stać obok siebie.

    Recenzje ``CANCELLED`` są w wierszu widoczne: cofnięty przydział jest informacją („próbowaliśmy,
    cofnięto”), a nie stanem do ukrycia.

    Prace jeszcze **oddane** (``SUBMITTED``) też tu stoją, odkąd ocenianie może ruszyć przed
    zamknięciem etapu: koordynator musi widzieć, co czeka na wciągnięcie do oceny, i móc wciągnąć
    pojedynczą pracę (``lockable``) bez blokowania całego etapu. Przydziału ani oceny końcowej
    taki wiersz nie przyjmuje – najpierw blokada, potem recenzenci.

    Pozostałe filtry zawężają tę samą listę i składają się ze sobą (są koniunkcją, nie alternatywą):
    ``problem_id`` – jedno zadanie, ``status`` – krok obiegu z ``ASSIGNMENT_STATUS_FILTERS``,
    ``reviewer_id`` – prace, które ta osoba ma **w ręku** (recenzja nieanulowana; odebraną pracę
    widać w historii wiersza, ale nie w kolejce recenzenta).

    Status i recenzent są dobierane **po** wyborze najnowszej wersji pracy, a nie w zapytaniu:
    filtr w SQL wyciągnąłby starszą wersję o pasującym statusie i postawił ją w tabeli zamiast tej,
    którą naprawdę się ocenia. ``problem_id`` może iść do zapytania, bo zadanie jest częścią klucza,
    po którym wybieramy wersję.
    """
    rows_qs = (
        Submission.objects.filter(
            entry__stage=stage,
            status__in=(
                SubmissionStatus.SUBMITTED,
                SubmissionStatus.LOCKED,
                SubmissionStatus.IN_REVIEW,
                *GRADABLE_STATUSES,
            ),
        )
        .select_related("entry", "entry__participant", "entry__participant__user", "problem")
        # Plik doczytujemy razem z pracą: w każdym wierszu stoi odnośnik „Pobierz”, więc bez
        # prefetchu tabela robiłaby jedno zapytanie na wiersz (``Submission.latest_file``).
        .prefetch_related(models.Prefetch("files", queryset=SubmissionFile.objects.order_by("-id")))
        .order_by("entry_id", "problem_id", "-version")
    )
    if problem_id:
        rows_qs = rows_qs.filter(problem_id=problem_id)
    # Po jednej, najnowszej wersji na (wpis, zadanie) – ta sama zasada, co przy przydziale: starsza
    # wersja nie może stanąć w tabeli obok nowszej i kusić do wpisania oceny nie tam, gdzie trzeba.
    best: dict[tuple[int, int], Submission] = {}
    for submission in rows_qs:
        best.setdefault((submission.entry_id, submission.problem_id), submission)
    submissions = list(best.values())

    wanted = ASSIGNMENT_STATUS_FILTERS.get((status or "").strip())
    if wanted:
        submissions = [item for item in submissions if item.status in wanted[1]]

    text = (query or "").strip()
    if text:
        needle = text.casefold()
        submissions = [
            submission
            for submission in submissions
            if needle in submission.entry.participant.public_code.casefold()
            or needle in (submission.entry.participant.user.last_name or "").casefold()
        ]
    submission_ids = [item.pk for item in submissions]
    reviews: dict[int, list[Review]] = {}
    for review in (
        Review.objects.filter(submission_id__in=submission_ids)
        .select_related("reviewer", "reviewer__user")
        .order_by("round", "id")
    ):
        reviews.setdefault(review.submission_id, []).append(review)
    if reviewer_id:
        submissions = [
            submission
            for submission in submissions
            if any(
                review.reviewer_id == reviewer_id and review.status != ReviewStatus.CANCELLED
                for review in reviews.get(submission.pk, ())
            )
        ]
        submission_ids = [item.pk for item in submissions]
    grades = {
        grade.submission_id: grade for grade in FinalGrade.objects.filter(submission_id__in=submission_ids)
    }
    rows = [
        {
            "submission": submission,
            "reviews": reviews.get(submission.pk, []),
            "final_grade": grades.get(submission.pk),
            # Plik wolno pobrać dopiero po skanie antywirusowym – ta sama reguła, co w
            # ``SubmissionDownloadView``. Ekran nie powiela jej warunku, tylko czyta ten sam stan,
            # żeby odnośnik nie obiecywał pobrania, które i tak skończyłoby się odmową.
            "downloadable": _is_clean(submission),
            "scan_pending": submission.latest_file is not None and not _is_clean(submission),
            # Bez zapytania na wiersz: ``best`` trzyma już najnowszą wersję pracy, więc wystarczy
            # sam stan – nowszej wersji w LOCKED/IN_REVIEW z definicji nie ma.
            "assignable": submission.status in (SubmissionStatus.LOCKED, SubmissionStatus.IN_REVIEW),
            # Ten sam skrót: wiersz niesie najnowszą wersję, więc oddana praca jest tą, którą
            # ``lock_submission_for_review`` wciągnie do oceniania.
            "lockable": submission.status == SubmissionStatus.SUBMITTED,
        }
        for submission in submissions
    ]
    return sorted(
        rows,
        key=lambda row: (
            row["submission"].entry.participant.public_code,
            row["submission"].problem.number,
            row["submission"].pk,
        ),
    )


def moderation_queue(competition=None):
    """Rozwiązania w moderacji wraz z ocenami rundy 1 – widok wyłącznie dla koordynatora.

    „Wyłącznie dla koordynatora” znaczy odtąd „dla koordynatora **tego** konkursu”: rola jest rolą
    w konkursie, a kolejka rozjazdów niesie prace razem z ich autorami.
    """
    from apps.competitions.scoping import scope_to_competition

    return (
        scope_to_competition(Submission.objects.filter(status=SubmissionStatus.MODERATION), competition)
        .select_related("entry", "entry__participant", "entry__stage", "problem")
        .prefetch_related("reviews__reviewer__user")
        .order_by("entry__stage_id", "problem__number", "id")
    )


def dispute_context(review: Review) -> list[dict]:
    """Materiał rozjemczy dla trzeciego recenzenta: obie oceny rundy 1 **bez tożsamości autorów**.

    Rozjemca musi wiedzieć, na czym polega rozjazd (punkty i argumentacja wewnętrzna), ale nie
    może wiedzieć, kto co napisał – inaczej runda 2 przestaje być niezależna, a staje się
    arbitrażem między nazwiskami (PROJEKT.md 2.4, przegląd T-05).

    Dostępne wyłącznie dla recenzji rundy 2; dla rundy 1 leci 404, bo materiał rozjemczy dla
    zwykłego recenzenta nie istnieje (a 403 potwierdzałoby, że coś takiego jest).

    Kolejność jest po ``score``, nie po ``id`` przydziału: numer recenzji rośnie z kolejnością
    przydzielania, więc sortowanie po nim korelowałoby wiersze z pulą recenzentów.
    """
    if review.round != ROUND_TIEBREAK:
        raise DomainError(
            "Materiał rozjemczy jest dostępny wyłącznie dla recenzji rundy 2.",
            "NOT_A_TIEBREAK_REVIEW",
            http.HTTP_404_NOT_FOUND,
        )
    rows = Review.objects.filter(
        submission_id=review.submission_id,
        round=ROUND_BLIND,
        status=ReviewStatus.SUBMITTED,
    ).order_by("score", "id")
    return [{"score": row.score, "comment_internal": row.comment_internal} for row in rows]
