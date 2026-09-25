"""Logika domenowa testów online. Widoki wyłącznie orkiestrują (PROJEKT.md 2.3).

Podział odpowiedzialności w tej aplikacji:

- ``grading.py`` – **ile punktów** należy się za odpowiedź. Czyste funkcje, bez bazy,
- ``services.py`` (ten moduł) – **kiedy i komu** wolno coś zrobić: start podejścia, zapis
  odpowiedzi, termin, ocena, przeliczenie, wsad do wyników etapu,
- ``imports.py`` – wczytanie pytań z pliku.

Reguły przechodzące przez cały moduł:

- czas zawsze przez ``timezone.now()`` i zawsze **z serwera**. Ani jedna decyzja o terminie nie
  bierze daty z żądania: przeglądarka uczestnika pokazuje licznik, ale to jest wyłącznie
  uprzejmość – o tym, czy odpowiedź wpłynęła na czas, rozstrzyga wyłącznie ``deadline_at``,
- ``grade_attempt`` jest **idempotentna**. Wolno ją wołać po zakończeniu podejścia, po zmianie
  klucza odpowiedzi i dwa razy z rzędu; wynik zależy wyłącznie od zapisanych odpowiedzi i od
  aktualnego klucza, nigdy od tego, ile razy już biegła,
- klucz odpowiedzi **nie opuszcza serwera** przed zakończeniem podejścia. Funkcje budujące
  kontekst dla uczestnika (``attempt_view_rows``) nie przepisują ``is_correct`` ani ``settings``.
"""

from __future__ import annotations

import logging
import random
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from rest_framework import status as http

from apps.competitions.models import Stage, StageEntry, StageFormat
from apps.core.api import DomainError
from apps.core.models import audit
from apps.core.points import POINTS_QUANTUM, WHOLE_POINTS, points_csv, round_points

from . import grading
from .models import (
    SUBMIT_GRACE_SECONDS,
    AttemptStatus,
    QuestionKind,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizOption,
    QuizQuestion,
    ShowResultsAfter,
)

logger = logging.getLogger(__name__)

#: Pola ustawień testu, które widok przekazuje do ``save_quiz_settings``. Lista jawna, bo
#: ``**fields`` z formularza trafia wprost na model – bez niej dołożenie pola do formularza
#: zapisywałoby je bez zastanowienia, także wtedy, gdy nie powinno dać się go zmienić.
QUIZ_SETTINGS_FIELDS = (
    "title",
    "instructions",
    "duration_minutes",
    "opens_at",
    "closes_at",
    "attempts_allowed",
    "shuffle_questions",
    "shuffle_options",
    "questions_per_attempt",
    "show_results_after",
    "negative_floor",
)

#: Pola pytania przekazywane przez edytor.
QUESTION_FIELDS = ("pool", "order", "kind", "text", "points", "negative_points")

#: Losowanie zestawu i kolejności. ``SystemRandom`` zamiast ziarna wyliczonego z identyfikatora
#: podejścia: zestaw byłby wtedy przewidywalny dla każdego, kto zna swój numer podejścia, a numery
#: idą po kolei. Powtarzalność, o którą tu chodzi, bierze się z **zapisu** (``question_order``),
#: a nie z ziarna – odświeżenie strony czyta gotowy porządek z bazy i nigdy nie losuje ponownie.
_random = random.SystemRandom()


def _conflict(detail: str, code: str) -> DomainError:
    """Jedyny rodzaj odmowy, jaki wychodzi z tego modułu – zawsze 409 (konflikt ze stanem).

    Odmowy „nie wolno ci” (403) tu nie ma i to nie jest przeoczenie: o tym, kto ma prawo dotknąć
    testu, rozstrzygają mixiny ról i filtr właściciela w widokach (``apps.web.views.quiz``),
    zanim żądanie dojdzie do serwisu. Wszystko, co zostaje dla tej warstwy, to konflikt ze stanem
    zawodów: test zamknięty, podejścia wyczerpane, czas minął, arkusz zamrożony podejściami.
    """
    return DomainError(detail, code, http.HTTP_409_CONFLICT)


# --- edytor testu -----------------------------------------------------------------------------


def quiz_for_stage(stage: Stage) -> Quiz | None:
    """Test etapu albo ``None``. Jedno miejsce z ``related_name``, żeby literał nie krążył po kodzie."""
    return getattr(stage, "quiz", None)


@transaction.atomic
def save_quiz_settings(*, stage: Stage, actor=None, request=None, **fields) -> Quiz:
    """Zapis ustawień testu – zakłada test, gdy etap jeszcze go nie ma.

    Jedna funkcja na „utwórz” i „zmień”, bo z punktu widzenia koordynatora jest to jeden ekran:
    wchodzi na ``/coordinator/stages/<id>/quiz/``, wypełnia formularz i zapisuje. Rozdzielenie na
    dwie ścieżki znaczyłoby dwa adresy i pytanie „czy ten etap ma już test”, na które koordynator
    nie ma powodu odpowiadać.

    Forma etapu **nie** jest tu zmieniana. Test wolno przygotować przy etapie w dowolnej formie
    (np. zawczasu, zanim zapadnie decyzja regulaminowa), a to, że uczestnik go zobaczy, zależy od
    ``Stage.format`` ustawionej na ekranie etapu. Gdyby zapis testu przestawiał formę, jedno
    wejście „na próbę” zabierałoby uczestnikom upload rozwiązań.
    """
    unknown = sorted(set(fields) - set(QUIZ_SETTINGS_FIELDS))
    if unknown:  # pragma: no cover - błąd programisty, nie danych
        raise ValueError(f"Pola spoza zakresu ustawień testu: {', '.join(unknown)}.")
    quiz = quiz_for_stage(stage)
    created = quiz is None
    if quiz is None:
        quiz = Quiz(stage=stage)
    diff = {}
    for name, value in fields.items():
        previous = getattr(quiz, name, None)
        if previous != value:
            diff[name] = [str(previous), str(value)]
        setattr(quiz, name, value)
    quiz.full_clean(exclude=["stage"])
    quiz.save()
    if diff or created:
        audit(actor, "quiz.settings_created" if created else "quiz.settings_updated", quiz, diff, request)
    return quiz


@transaction.atomic
def save_question(
    *,
    quiz: Quiz,
    question: QuizQuestion | None = None,
    options: list[dict] | None = None,
    settings: dict | None = None,
    actor=None,
    request=None,
    image=None,
    clear_image: bool = False,
    **fields,
) -> QuizQuestion:
    """Dodanie albo zmiana pytania razem z jego wariantami i kluczem odpowiedzi.

    Warianty przychodzą jako **cała lista**, a nie jako różnica, i cała lista zastępuje poprzednią.
    Powód jest praktyczny: edytor pokazuje warianty jako zestaw pól na jednym ekranie, więc
    „stan po zapisie” jest dokładnie tym, co koordynator widzi – a synchronizacja po
    identyfikatorach wymagałaby ukrytych pól, które przy dopisaniu wiersza w środku listy
    potrafią pomieszać treści z zaznaczeniami „poprawny”.

    Skutek uboczny jest świadomy: zmiana wariantów pytania **unieważnia** odpowiedzi już na nie
    udzielone (wskazują na skasowane wiersze). Dlatego edytor odmawia zmian w teście, do którego
    ktoś już podchodził – patrz ``assert_editable``.
    """
    unknown = sorted(set(fields) - set(QUESTION_FIELDS))
    if unknown:  # pragma: no cover - błąd programisty, nie danych
        raise ValueError(f"Pola spoza zakresu pytania: {', '.join(unknown)}.")
    created = question is None
    if question is None:
        question = QuizQuestion(quiz=quiz)
    for name, value in fields.items():
        setattr(question, name, value)
    # ``settings`` przechodzi przez bramę walidacji rodzaju: to jedyna droga tych danych do bazy.
    question.settings = grading.validate_question_settings(question.kind, settings or {})
    if clear_image:
        question.image = None
    elif image is not None:
        question.image = image
    question.full_clean(exclude=["quiz"])
    question.save()

    if question.has_options:
        _replace_options(question, options or [])
    else:
        # Pytanie, które przestało być pytaniem wyboru, nie może zostawić po sobie wariantów:
        # nie byłyby widoczne w edytorze, a sprawdzanie dalej by je widziało.
        question.options.all().delete()
    audit(actor, "quiz.question_created" if created else "quiz.question_updated", question, None, request)
    return question


def _replace_options(question: QuizQuestion, options: list[dict]) -> None:
    """Warianty pytania od nowa. Walidacja „musi być poprawny wariant” stoi tutaj, bo potrzebuje listy."""
    cleaned = [
        {"text": str(item.get("text") or "").strip(), "is_correct": bool(item.get("is_correct"))}
        for item in options
        if str(item.get("text") or "").strip()
    ]
    if len(cleaned) < 2:
        raise DomainError("Pytanie wyboru wymaga co najmniej dwóch wariantów.", "QUIZ_OPTIONS_TOO_FEW")
    correct = [item for item in cleaned if item["is_correct"]]
    if not correct:
        raise DomainError(
            "Zaznacz co najmniej jeden poprawny wariant – inaczej pytania nie da się zaliczyć.",
            "QUIZ_OPTIONS_NO_CORRECT",
        )
    if question.kind == QuestionKind.SINGLE_CHOICE and len(correct) > 1:
        raise DomainError(
            "Pytanie jednokrotnego wyboru może mieć tylko jeden poprawny wariant.",
            "QUIZ_OPTIONS_TOO_MANY_CORRECT",
        )
    question.options.all().delete()
    QuizOption.objects.bulk_create(
        [
            QuizOption(question=question, order=index, text=item["text"], is_correct=item["is_correct"])
            for index, item in enumerate(cleaned, start=1)
        ]
    )


@transaction.atomic
def delete_question(*, question: QuizQuestion, actor=None, request=None) -> None:
    """Usunięcie pytania. Dopuszczalne wyłącznie w teście bez podejść – patrz ``assert_editable``."""
    assert_editable(question.quiz)
    audit(actor, "quiz.question_deleted", question, {"text": ["", question.text[:120]]}, request)
    question.delete()


def has_attempts(quiz: Quiz) -> bool:
    return quiz.attempts.exists()


def assert_editable(quiz: Quiz) -> None:
    """Brama edytora: treści testu nie wolno ruszać, gdy ktoś już do niego podchodził.

    To nie jest ostrożność, tylko warunek sensowności wyników. Zestawy są losowane i **zapisane**
    przy podejściu: dopisanie pytania zmienia to, co wylosuje się następnym osobom, a skasowanie
    pytania zostawia w cudzym zapisanym zestawie identyfikator, którego już nie ma. Dwie osoby
    w tym samym etapie pisałyby wtedy dwa różne testy, nie wiedząc o tym.

    Poprawka **klucza odpowiedzi** to osobna sprawa i jest dozwolona: klucz bywa błędny i to
    wychodzi dopiero z wyników. Tę drogę otwiera ``regrade_quiz`` (przycisk „Przelicz punkty”),
    który po zmianie klucza uczciwie przelicza **wszystkie** podejścia i zostawia ślad w audycie.
    """
    if has_attempts(quiz):
        raise _conflict(
            "Do tego testu są już podejścia – nie można zmieniać zestawu pytań. Poprawkę klucza "
            "odpowiedzi zapisz i użyj przycisku „Przelicz punkty”.",
            "QUIZ_HAS_ATTEMPTS",
        )


def draw_warnings(quiz: Quiz) -> list[str]:
    """Co w tym teście jest formalnie poprawne, ale najpewniej nie jest tym, o co chodziło.

    Ostrzeżenia, a nie błędy: każdy z tych stanów da się zapisać i każdy bywa przejściowy (test
    w budowie ma pytania dokładane po kolei). Odmowa zapisu zmuszałaby do układania testu
    w jednej kolejności; milczenie kosztowałoby wynikami. Lista pokazuje się na ekranie ustawień.
    """
    warnings: list[str] = []
    pools = quiz.pools
    if not pools:
        return ["Test nie ma jeszcze ani jednego pytania."]
    draw = quiz.questions_per_attempt
    for pool, questions in sorted(pools.items()):
        name = pool or "pula domyślna"
        if draw is not None and len(questions) < draw:
            warnings.append(
                f"Pula „{name}” ma {len(questions)} pyt. – mniej niż {draw} losowane z każdej puli. "
                "Wszyscy dostaną z niej te same pytania."
            )
        if draw is not None and len({question.points for question in questions}) > 1:
            warnings.append(
                f"Pytania w puli „{name}” są warte różnie, a zestaw jest losowany – o maksymalnym "
                "wyniku decydowałoby wtedy losowanie, nie wiedza. Wyrównaj punkty w tej puli."
            )
    return warnings


# --- podejście uczestnika ---------------------------------------------------------------------


def _questions_with_options(quiz: Quiz) -> list[QuizQuestion]:
    """Pytania testu razem z wariantami – dwa zapytania na cały test, nie po jednym na pytanie."""
    return list(
        quiz.questions.prefetch_related(
            Prefetch("options", queryset=QuizOption.objects.order_by("order", "id"))
        ).order_by("pool", "order", "id")
    )


def draw_question_order(quiz: Quiz, questions: list[QuizQuestion]) -> dict:
    """Zestaw i kolejność dla jednego podejścia – losowane raz, potem zapisane na stałe.

    Trzy niezależne rzeczy dzieją się tutaj i każda ma własny wyłącznik w ustawieniach:

    1. **dobór pytań** (``questions_per_attempt``) – z każdej puli ciągniemy zadaną liczbę pytań.
       Pula mniejsza niż limit oddaje wszystko, co ma; to jest sytuacja, o której mówi
       ``draw_warnings``, ale nie jest błędem czasu wykonania,
    2. **kolejność pytań** (``shuffle_questions``) – tasowanie całości. Bez niego zostaje
       kolejność z edytora, czyli pula po puli, a w puli po ``order``. To jest istotne: test
       z pytaniami uporządkowanymi tematycznie bywa świadomym zamysłem autora,
    3. **kolejność wariantów** (``shuffle_options``) – osobno dla każdego pytania. Tasowanie
       wariantów robi serwer i zapisuje wynik, bo przetasowanie ich w przeglądarce znaczyłoby,
       że prawdziwa kolejność (a więc i to, który wariant jest który) jest w HTML-u.
    """
    grouped: dict[str, list[QuizQuestion]] = {}
    for question in questions:
        grouped.setdefault(question.pool, []).append(question)

    drawn: list[QuizQuestion] = []
    for pool in sorted(grouped):
        items = grouped[pool]
        if quiz.questions_per_attempt is None or len(items) <= quiz.questions_per_attempt:
            drawn.extend(items)
            continue
        # ``sample`` zamiast ``shuffle`` + cięcie: chodzi o podzbiór, a nie o kolejność, i tę
        # drugą i tak rozstrzyga punkt 2 niżej.
        picked = _random.sample(items, quiz.questions_per_attempt)
        drawn.extend(sorted(picked, key=lambda item: (item.order, item.pk)))

    if quiz.shuffle_questions:
        _random.shuffle(drawn)

    options: dict[str, list[int]] = {}
    for question in drawn:
        if not question.has_options:
            continue
        ids = [option.pk for option in question.options.all()]
        if quiz.shuffle_options:
            _random.shuffle(ids)
        options[str(question.pk)] = ids

    return {
        QuizAttempt.ORDER_QUESTIONS: [question.pk for question in drawn],
        QuizAttempt.ORDER_OPTIONS: options,
    }


def active_attempt(quiz: Quiz, entry: StageEntry) -> QuizAttempt | None:
    """Podejście, które wciąż trwa – najwyżej jedno (constraint ``quiz_attempt_single_active``)."""
    return quiz.attempts.filter(entry=entry, status=AttemptStatus.IN_PROGRESS).first()


def attempts_used(quiz: Quiz, entry: StageEntry) -> int:
    return quiz.attempts.filter(entry=entry).count()


def attempts_left(quiz: Quiz, entry: StageEntry) -> int:
    return max(0, quiz.attempts_allowed - attempts_used(quiz, entry))


@transaction.atomic
def start_attempt(*, quiz: Quiz, entry: StageEntry, now=None, request=None) -> QuizAttempt:
    """Rozpoczęcie podejścia: losowanie zestawu, wyliczenie terminu, zapis.

    Kolejność bramek nie jest przypadkowa – idzie od najbardziej ogólnej do najbardziej osobistej,
    żeby komunikat mówił o tym, co uczestnik może z tym zrobić: najpierw „test nie ma pytań”
    (to jest problem organizatora), potem „poza oknem” (można wrócić później), na końcu „limit
    podejść” (już nic się nie da).

    Podejście przeterminowane, ale wciąż otwarte, jest tu **domykane**, a nie omijane: ktoś, kto
    zamknął kartę na ostatnim pytaniu i wraca po godzinie, ma zobaczyć swój wynik, a nie pustą
    stronę startową z licznikiem, który dawno minął.
    """
    now = now or timezone.now()
    questions = _questions_with_options(quiz)
    if not questions:
        raise _conflict("Ten test nie ma jeszcze pytań.", "QUIZ_EMPTY")

    open_attempt = active_attempt(quiz, entry)
    if open_attempt is not None:
        if open_attempt.accepts_answers_at(now):
            # Powrót do trwającego podejścia, a nie drugie podejście. Ta gałąź jest powodem,
            # dla którego „Rozpocznij” wolno kliknąć dwa razy bez szkody.
            return open_attempt
        expire_attempt(open_attempt, now=now)

    if not quiz.is_open(now):
        opens, closes = quiz.window
        raise _conflict(
            "Test jest zamknięty."
            if now >= closes
            else f"Test otwiera się {timezone.localtime(opens):%Y-%m-%d %H:%M}.",
            "QUIZ_CLOSED",
        )
    if attempts_left(quiz, entry) <= 0:
        raise _conflict(
            f"Wykorzystano wszystkie podejścia ({quiz.attempts_allowed}).", "QUIZ_NO_ATTEMPTS_LEFT"
        )

    # Termin podejścia to wcześniejszy z dwóch: czas trwania testu i koniec okna. Bez drugiego
    # członu podejście rozpoczęte pięć minut przed zamknięciem trwałoby pełną godzinę – i dawałoby
    # przewagę osobie, która zaczęła najpóźniej.
    _, closes = quiz.window
    deadline = min(now + timedelta(minutes=quiz.duration_minutes), closes)
    attempt = QuizAttempt.objects.create(
        quiz=quiz,
        entry=entry,
        started_at=now,
        deadline_at=deadline,
        question_order=draw_question_order(quiz, questions),
        ip=_client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT", "") if request else "")[:255],
    )
    logger.info("quiz.attempt_started quiz=%s entry=%s attempt=%s", quiz.pk, entry.pk, attempt.pk)
    return attempt


def _client_ip(request) -> str | None:
    """Adres klienta z żądania. ``REMOTE_ADDR``, bo za odwrotnym proxy ustawia go ``X-Forwarded-For``.

    Nagłówka nie czytamy tutaj wprost: nagłówek od klienta da się dopisać samemu, a jedynym
    miejscem, które wie, ilu proxy wolno zaufać, jest konfiguracja wdrożenia. ``REMOTE_ADDR``
    w produkcji jest już przepisany przez warstwę serwera.
    """
    if request is None:
        return None
    return request.META.get("REMOTE_ADDR") or None


def attempt_questions(attempt: QuizAttempt) -> list[QuizQuestion]:
    """Pytania podejścia w **zapisanej** kolejności, z wariantami w zapisanej kolejności.

    Jedno zapytanie po pytania i jedno po warianty, a porządek składamy w Pythonie: kolejność
    wylosowana per podejście nie da się wyrazić w ``ORDER BY``, a sortowanie po ``IN (…)`` po
    stronie bazy byłoby zapytaniem, którego nikt później nie przeczyta.

    Pytanie, które zniknęło z testu po rozpoczęciu podejścia (edytor tego broni, ale kasowanie
    z ``/admin/`` już nie), po prostu wypada z listy – zamiast wywracać cudze podejście.
    """
    ids = attempt.drawn_question_ids
    by_id = {
        question.pk: question
        for question in QuizQuestion.objects.filter(pk__in=ids).prefetch_related(
            Prefetch("options", queryset=QuizOption.objects.order_by("order", "id"))
        )
    }
    result = []
    for question_id in ids:
        question = by_id.get(question_id)
        if question is None:
            continue
        # Warianty w kolejności zapisanej przy starcie. ``option_order_for`` oddaje pustą listę
        # dla pytań bez tasowania i wtedy zostaje kolejność z edytora.
        order = attempt.option_order_for(question_id)
        if order:
            options = {option.pk: option for option in question.options.all()}
            question.drawn_options = [options[pk] for pk in order if pk in options]
        else:
            question.drawn_options = list(question.options.all())
        result.append(question)
    return result


def save_answers(*, attempt: QuizAttempt, answers: dict, now=None) -> int:
    """Zapis (albo nadpisanie) odpowiedzi trwającego podejścia. Zwraca liczbę zapisanych pytań.

    Wołane z dwóch stron i **to jest ta sama funkcja** dla obu: autozapis z przeglądarki co
    dwadzieścia sekund i zwykły POST formularza kogoś, kto ma wyłączony JavaScript. Gdyby były to
    dwie ścieżki, wystarczyłoby, żeby jedna z nich zapomniała o terminie albo o filtrowaniu
    wariantów, aby powstała droga obejścia.

    Po terminie (z tolerancją ``SUBMIT_GRACE_SECONDS``) zapis jest **odrzucany**, a podejście
    domykane jako przeterminowane wraz z tym, co zdążyło się zapisać. Wyjątek niesie kod
    ``QUIZ_ATTEMPT_EXPIRED``, po którym przeglądarka przestaje ponawiać i przechodzi na wynik.

    Uwaga o transakcjach, która jest tu **istotą poprawności**: domknięcie po terminie stoi
    świadomie **poza** blokiem atomowym, a atomowy jest wyłącznie sam zapis odpowiedzi. Gdyby cała
    funkcja była jedną transakcją, wyjątek rzucony po ``expire_attempt`` wycofałby także jego
    zapis – podejście zostałoby „w trakcie” na zawsze, a razem z nim wynik, którego nikt by nie
    policzył. To jest ten rzadki przypadek, w którym skutek uboczny **musi** przeżyć błąd.
    """
    now = now or timezone.now()
    if not attempt.accepts_answers_at(now):
        if attempt.is_open:
            expire_attempt(attempt, now=now)
        raise _conflict("Czas na rozwiązanie testu minął.", "QUIZ_ATTEMPT_EXPIRED")

    questions = {question.pk: question for question in attempt_questions(attempt)}
    saved = 0
    with transaction.atomic():
        for raw_id, payload in (answers or {}).items():
            try:
                question_id = int(raw_id)
            except TypeError, ValueError:
                continue
            question = questions.get(question_id)
            if question is None:
                # Odpowiedź na pytanie spoza **własnego** wylosowanego zestawu. Cicho pomijana,
                # a nie odrzucana z błędem: wylosowany zestaw jest po stronie serwera i nie ma
                # powodu informować nadawcy, czy takie pytanie w ogóle istnieje.
                continue
            cleaned = _clean_payload(question, payload)
            QuizAnswer.objects.update_or_create(
                attempt=attempt,
                question=question,
                # Nadpisanie zeruje werdykt: odpowiedź zmieniona po ocenie (zdarza się przy
                # przeliczaniu w trakcie) nie może zostawić po sobie punktów za poprzednią treść.
                defaults={"payload": cleaned, "is_correct": None, "points_awarded": None},
            )
            saved += 1
    return saved


def _clean_payload(question: QuizQuestion, payload) -> dict:
    """Odpowiedź sprowadzona do kształtu, który rozumie ``grading`` – i tylko do niego.

    To jest granica zaufania: dalej w głąb systemu nie idzie nic, czego nie ma na tej liście.
    Dla pytań wyboru zostają wyłącznie identyfikatory wariantów **tego** pytania, więc cudzy
    (albo zmyślony) identyfikator nie zapisze się nawet w ``payload`` i nie będzie go w eksporcie.
    """
    if not isinstance(payload, dict):
        return {}
    if question.has_options:
        allowed = {option.pk for option in question.options.all()}
        selected = [pk for pk in grading.selected_option_ids(payload) if pk in allowed]
        if question.kind == QuestionKind.SINGLE_CHOICE:
            selected = selected[:1]
        return {"options": selected}
    if question.kind == QuestionKind.SHORT_TEXT:
        # Obcięcie długości jest po stronie zapisu, a nie sprawdzania: krótka odpowiedź jest
        # krótka z definicji, a bez limitu autozapis przyjąłby megabajt tekstu na pytanie.
        return {"text": str(payload.get("text") or "")[:500]}
    return {"value": str(payload.get("value") or "")[:80]}


@transaction.atomic
def submit_attempt(*, attempt: QuizAttempt, now=None) -> QuizAttempt:
    """Zakończenie podejścia przez uczestnika („Zakończ test”) razem z oceną.

    Idempotentne: ponowne wysłanie tego samego formularza (podwójne kliknięcie, przycisk „wstecz”
    i jeszcze raz „Zakończ”) oddaje podejście już zakończone, zamiast rzucać błędem w twarz komuś,
    kto właśnie skończył zawody.
    """
    now = now or timezone.now()
    if not attempt.is_open:
        return attempt
    if not attempt.accepts_answers_at(now):
        return expire_attempt(attempt, now=now)
    attempt.status = AttemptStatus.SUBMITTED
    attempt.submitted_at = now
    attempt.save(update_fields=["status", "submitted_at"])
    grade_attempt(attempt)
    logger.info("quiz.attempt_submitted attempt=%s", attempt.pk)
    return attempt


@transaction.atomic
def expire_attempt(attempt: QuizAttempt, *, now=None) -> QuizAttempt:
    """Domknięcie podejścia, w którym skończył się czas – z oceną tego, co zdążyło się zapisać.

    ``submitted_at`` dostaje **termin podejścia**, a nie chwilę obecną. Ta data jest odpowiedzią
    na pytanie „kiedy przestał pisać”, a nie „kiedy serwer to zauważył”; ta druga bywa o dobę
    późniejsza, bo podejście porzucone domyka się dopiero przy przeliczaniu wyników.
    """
    now = now or timezone.now()
    if not attempt.is_open:
        return attempt
    attempt.status = AttemptStatus.EXPIRED
    attempt.submitted_at = min(now, attempt.deadline_at)
    attempt.save(update_fields=["status", "submitted_at"])
    grade_attempt(attempt)
    logger.info("quiz.attempt_expired attempt=%s", attempt.pk)
    return attempt


def finalise_overdue(*, quiz: Quiz | None = None, stage: Stage | None = None, now=None) -> int:
    """Domknięcie wszystkich porzuconych podejść, którym minął czas. Zwraca ich liczbę.

    Potrzebne, bo podejście kończy się na dwa sposoby, a tylko jeden z nich generuje żądanie:
    kliknięcie „Zakończ” przychodzi na serwer, a zamknięcie laptopa – nie. Bez tego przejścia
    praca kogoś, kto stracił łącze, zostawałaby w stanie „w trakcie” na zawsze, czyli w stanie,
    który ``stage_scores`` pomija.

    Wołane w trzech miejscach: przy wejściu uczestnika na test (domyka **jego** poprzednie
    podejście), przy ekranie wyników koordynatora i przy przeliczaniu wyników etapu. Jest
    idempotentne i tanie, gdy nie ma czego domykać.
    """
    now = now or timezone.now()
    queryset = QuizAttempt.objects.filter(status=AttemptStatus.IN_PROGRESS)
    if quiz is not None:
        queryset = queryset.filter(quiz=quiz)
    if stage is not None:
        queryset = queryset.filter(quiz__stage=stage)
    overdue = list(queryset.filter(deadline_at__lt=now - timedelta(seconds=SUBMIT_GRACE_SECONDS)))
    for attempt in overdue:
        expire_attempt(attempt, now=now)
    return len(overdue)


# --- ocena ------------------------------------------------------------------------------------


@transaction.atomic
def grade_attempt(attempt: QuizAttempt) -> Decimal:
    """Ocena całego podejścia. **Idempotentna**: wynik zależy od odpowiedzi i klucza, nie od historii.

    Liczone są wszystkie pytania **wylosowanego zestawu**, także te bez odpowiedzi – bo to one
    ustalają maksimum (``QuizAttempt.max_points``) i bo brak odpowiedzi jest informacją
    w statystyce pytania. Odpowiedzi na pytania spoza zestawu (możliwe po ręcznej ingerencji
    w bazę) nie są brane pod uwagę.

    Zapis idzie hurtem: jedno ``bulk_update`` na odpowiedzi i jeden ``save`` na podejście,
    niezależnie od liczby pytań.
    """
    quiz = attempt.quiz
    questions = attempt_questions(attempt)
    answers = {answer.question_id: answer for answer in attempt.answers.all()}

    awarded: list[Decimal] = []
    changed: list[QuizAnswer] = []
    maximum = Decimal("0")
    for question in questions:
        maximum += question.points
        answer = answers.get(question.pk)
        payload = answer.payload if answer is not None else {}
        correct_ids = {option.pk for option in question.options.all() if option.is_correct}
        all_ids = {option.pk for option in question.options.all()}
        verdict = grading.score_question(
            kind=question.kind,
            settings=question.settings,
            payload=payload,
            correct_ids=correct_ids,
            all_ids=all_ids,
        )
        verdict = grading.award_points(
            verdict,
            points=question.points,
            negative_points=question.negative_points,
            floor=quiz.negative_floor,
        )
        awarded.append(verdict.points)
        if answer is not None:
            answer.is_correct = verdict.is_correct
            answer.points_awarded = verdict.points
            changed.append(answer)

    if changed:
        QuizAnswer.objects.bulk_update(changed, ["is_correct", "points_awarded"])
    attempt.score = grading.total_score(awarded, floor=quiz.negative_floor)
    attempt.max_points = maximum
    attempt.graded_at = timezone.now()
    attempt.save(update_fields=["score", "max_points", "graded_at"])
    return attempt.score


@transaction.atomic
def regrade_quiz(*, quiz: Quiz, actor=None, request=None) -> dict:
    """„Przelicz punkty”: ponowna ocena **wszystkich** zakończonych podejść po zmianie klucza.

    Po co osobny przycisk, skoro ``grade_attempt`` jest idempotentna: bo poprawka klucza jest
    zdarzeniem, o którym musi zostać ślad. Klucz bywa błędny i to wychodzi dopiero z wyników –
    a wtedy ktoś zmienia punktację **po** zawodach i przed ogłoszeniem. Wpis audytowy z liczbą
    podejść i sumą zmian jest jedynym dokumentem, którym organizator wytłumaczy się z tego, że
    wyniki wyglądają inaczej niż wczoraj. W ``diff`` nie ma ani jednej danej osobowej – liczniki
    i identyfikator testu, tak samo jak w ``apps.results.services``.

    Podejścia wciąż trwające są najpierw domykane: inaczej „przelicz wszystko” zostawiałoby poza
    przeliczeniem dokładnie te, które są najświeższe.
    """
    finalise_overdue(quiz=quiz)
    attempts = list(quiz.attempts.exclude(status=AttemptStatus.IN_PROGRESS))
    changed = 0
    for attempt in attempts:
        before = attempt.score
        after = grade_attempt(attempt)
        if before != after:
            changed += 1
    diff = {"attempts": ["", str(len(attempts))], "changed": ["", str(changed)]}
    audit(actor, "quiz.regraded", quiz, diff, request)
    logger.info("quiz.regraded quiz=%s attempts=%s changed=%s", quiz.pk, len(attempts), changed)
    return {"attempts": len(attempts), "changed": changed}


# --- wsad do wyników etapu ---------------------------------------------------------------------


def stage_scores(stage: Stage) -> dict[int, Decimal]:
    """Punkty z testu per wpis do etapu – **jedyne** wejście testów do tabeli wyników.

    Kontrakt jest celowo wąski: ``{StageEntry.pk: punkty}``. ``apps.results.services`` nie wie
    o istnieniu pytań, podejść ani odpowiedzi i nie ma powodu wiedzieć – dla niej etap w formie
    testu jest etapem, w którym sumę punktów podaje inny moduł niż suma ocen recenzentów.
    Dzięki temu zmiana czegokolwiek w tej aplikacji nie dotyka przeliczania wyników.

    Trzy decyzje zapisane w tej funkcji:

    - **liczy się najlepsze podejście**. Przy ``attempts_allowed > 1`` każde kolejne podejście ma
      sens tylko wtedy, gdy może poprawić wynik; liczenie ostatniego karałoby za sprawdzenie się
      jeszcze raz, a średnia nie odpowiadałaby na żadne pytanie regulaminu,
    - podejścia **przeterminowane liczą się normalnie** (patrz ``AttemptStatus``); pomijamy
      wyłącznie te wciąż trwające, bo nie są jeszcze ocenione,
    - wynik idzie do tabeli z dokładnością **trybu etapu** (``ScoringScale.free_values``) –
      ``ROUND_HALF_UP`` (``apps.core.points.round_points``), ta sama metoda i ten sam przełącznik,
      co suma ważona etapu pisemnego (``competitions.services.StageScoring.quantum``):

      * etap „tylko ze skali” (stan każdego etapu, którego organizator nie przełączył) – **pełne
        punkty**, połówka w górę, dokładnie jak od pierwszej edycji: 7,5 → 8, 7,49 → 7,
      * etap z dowolnymi wartościami – **co 0,01**: 7,5 zostaje 7,5. Do wydania 0.35.0 włącznie
        test zaokrąglał zawsze do pełnych punktów, więc przełącznik „dowolne wartości” nie działał
        na etap w formie testu, choć organizator ustawiał go na tym samym ekranie skali, co dla
        etapu pisemnego (prośba „ułamki w teście”, po wydaniu 0.35.0). Wynik podejścia ma już dwa
        miejsca po przecinku (``grading.total_score``), więc w tym trybie nic się nie zaokrągla.

      Wartości są ``Decimal`` w obu trybach – tak jak suma etapu pisemnego – i trafiają do tej
      samej kolumny ``StageEntry.total_points`` (``numeric(10, 2)``). Wynik dokładny, z częściami
      setnymi, zostaje na ekranie wyników testu także w trybie skali.

    Tryb czyta jedno pole skali etapu (``stage.scoring_scale``) – zapytanie, którego nie ma, gdy
    wołający załadował skalę razem z etapem (``select_related``).
    """
    from apps.competitions.scoring import stage_free_values

    quiz = quiz_for_stage(stage)
    if quiz is None:
        return {}
    quantum = POINTS_QUANTUM if stage_free_values(stage) else WHOLE_POINTS
    best: dict[int, Decimal] = {}
    rows = quiz.attempts.exclude(status=AttemptStatus.IN_PROGRESS).values_list("entry_id", "score")
    for entry_id, score in rows:
        if score is None:
            continue
        if entry_id not in best or score > best[entry_id]:
            best[entry_id] = score
    return {entry_id: round_points(score, quantum) for entry_id, score in best.items()}


def is_quiz_stage(stage: Stage) -> bool:
    """Czy punkty tego etapu pochodzą z testu online. Pytanie zadawane przez ``apps.results``."""
    return stage.format == StageFormat.QUIZ


# --- ekrany koordynatora ------------------------------------------------------------------------


def attempt_rows(quiz: Quiz) -> list[dict]:
    """Wyniki testu per uczestnik: najlepsze podejście, liczba podejść, procent.

    Wiersze są **pełne** (kod uczestnika i nazwisko) – to materiał dla koordynatora, nigdy
    odpowiedź publiczna. Publikacja idzie zwykłą drogą, przez ``apps.results.publish_results``,
    która anonimizuje tabelę tak samo jak dla etapu pisemnego.
    """
    attempts = (
        quiz.attempts.exclude(status=AttemptStatus.IN_PROGRESS)
        .select_related("entry", "entry__participant", "entry__participant__user")
        .order_by("entry_id", "-score", "id")
    )
    best: dict[int, QuizAttempt] = {}
    counts: dict[int, int] = {}
    for attempt in attempts:
        counts[attempt.entry_id] = counts.get(attempt.entry_id, 0) + 1
        current = best.get(attempt.entry_id)
        if current is None or (attempt.score or Decimal("0")) > (current.score or Decimal("0")):
            best[attempt.entry_id] = attempt
    rows = []
    for attempt in best.values():
        participant = attempt.entry.participant
        maximum = attempt.max_points or Decimal("0")
        rows.append(
            {
                "entry_id": attempt.entry_id,
                "public_code": participant.public_code,
                "first_name": participant.user.first_name,
                "last_name": participant.user.last_name,
                "score": attempt.score,
                "max_points": maximum,
                "percent": (float(attempt.score) * 100 / float(maximum)) if maximum else 0.0,
                "attempts": counts[attempt.entry_id],
                "status": attempt.get_status_display(),
                "submitted_at": attempt.submitted_at,
            }
        )
    rows.sort(key=lambda row: (-(row["score"] or Decimal("0")), row["public_code"]))
    return rows


def question_stats(quiz: Quiz) -> list[dict]:
    """Trudność i moc różnicująca każdego pytania – po to, żeby dało się poprawić następny test.

    Dwie liczby, obie standardowe w analizie testów i obie potrzebne, bo mówią co innego:

    - **trudność** (``p``) to odsetek odpowiedzi w pełni poprawnych wśród **udzielonych**. Pytanie
      z ``p`` bliskim 1 nie różnicuje nikogo (umieli wszyscy), z ``p`` bliskim 0 zwykle jest
      wadliwe albo dotyczy materiału spoza zakresu,
    - **moc różnicująca** (``d``) to różnica trudności między lepszą i słabszą połową uczestników
      (metoda grup skrajnych na medianie wyniku całego testu). Wartość bliska zeru albo ujemna
      znaczy, że pytanie odpowiada **odwrotnie** niż reszta testu – najczęściej jest to błąd
      w kluczu albo dwuznaczna treść, i to jest jedyny sygnał, po którym da się to wykryć bez
      czytania wszystkich odpowiedzi.

    Osobno liczymy „bez odpowiedzi”: pytanie pominięte przez połowę uczestników mówi o czasie
    trwania testu, a nie o trudności, i nie może zaniżać ``p``.

    Przy mniej niż dwóch podejściach obie liczby są nieoznaczone i funkcja oddaje ``None`` –
    pół procenta wyliczone z jednego podejścia byłoby liczbą udającą wiedzę.
    """
    attempts = list(quiz.attempts.exclude(status=AttemptStatus.IN_PROGRESS).order_by("-score", "id"))
    answers = list(
        QuizAnswer.objects.filter(attempt__in=attempts).values_list("attempt_id", "question_id", "is_correct")
    )
    by_question: dict[int, dict[int, bool | None]] = {}
    for attempt_id, question_id, is_correct in answers:
        by_question.setdefault(question_id, {})[attempt_id] = is_correct

    # Grupy skrajne: górna i dolna połowa listy posortowanej po wyniku. Przy nieparzystej liczbie
    # podejść środkowe wypada z obu grup – tak, żeby grupy miały równą liczność.
    ranked = [attempt.pk for attempt in attempts]
    half = len(ranked) // 2
    top: set[int] = set(ranked[:half])
    bottom: set[int] = set(ranked[len(ranked) - half :]) if half else set()

    rows = []
    for question in quiz.questions.order_by("pool", "order", "id"):
        verdicts = by_question.get(question.pk, {})
        answered = {key: value for key, value in verdicts.items() if value is not None}
        in_top = {key: value for key, value in answered.items() if key in top}
        in_bottom = {key: value for key, value in answered.items() if key in bottom}
        rows.append(
            {
                "question": question,
                "answered": len(answered),
                "blank": len(attempts) - len(answered),
                "difficulty": _share_correct(answered) if answered else None,
                # Nieoznaczona, dopóki pytanie nie ma odpowiedzi w **obu** grupach skrajnych:
                # różnica liczona z pustej grupy byłaby różnicą względem zera, czyli liczbą
                # mówiącą o liczności grup, a nie o pytaniu.
                "discrimination": (
                    _share_correct(in_top) - _share_correct(in_bottom) if in_top and in_bottom else None
                ),
            }
        )
    return rows


def _share_correct(verdicts: dict) -> float:
    """Odsetek odpowiedzi w pełni poprawnych w podanym zestawie werdyktów."""
    if not verdicts:
        return 0.0
    return sum(1 for value in verdicts.values() if value) / len(verdicts)


#: Kolumny eksportu wyników testu – te, o które pyta organizator przy protokole.
RESULTS_CSV_HEADER = ["kod", "imię", "nazwisko", "wynik", "maksimum", "procent", "podejścia", "oddano"]


def results_csv_rows(quiz: Quiz) -> list[list]:
    """Wiersze eksportu wyników testu (bez nagłówka – ten stoi w ``RESULTS_CSV_HEADER``).

    Eksport idzie **z pełnymi danymi**, bo jest dokumentem wewnętrznym pobieranym z panelu
    koordynatora; publiczna tabela powstaje osobno i przechodzi przez anonimizację
    (``apps.results.services.publish_results``). Fakt pobrania zapisuje audyt – tak samo jak przy
    pozostałych eksportach panelu.
    """
    rows: list[list] = []
    for row in attempt_rows(quiz):
        rows.append(
            [
                row["public_code"],
                row["first_name"],
                row["last_name"],
                # Punkty jak w każdym eksporcie od wydania 0.35.0 (``points_csv``): kropka
                # dziesiętna, bez zbędnych zer – „7”, „7.5”, a nie „7.00”.
                points_csv(row["score"]),
                points_csv(row["max_points"]),
                f"{row['percent']:.1f}",
                row["attempts"],
                timezone.localtime(row["submitted_at"]).strftime("%Y-%m-%d %H:%M")
                if row["submitted_at"]
                else "",
            ]
        )
    return rows


def may_show_result(quiz: Quiz, attempt: QuizAttempt, now=None) -> bool:
    """Czy uczestnikowi wolno **teraz** zobaczyć swój wynik – reguła ``show_results_after``.

    Jedno miejsce dla tej decyzji, bo pytają o nią dwa ekrany (strona po zakończeniu i powrót do
    testu później) i jedna gałąź szablonu. Rozjazd między nimi znaczyłby, że wynik wycieka jedną
    z dróg – a przy ``NEVER`` jest to wyciek wyniku zawodów przed ogłoszeniem.
    """
    if attempt.score is None:
        return False
    if quiz.show_results_after == ShowResultsAfter.IMMEDIATELY:
        return True
    if quiz.show_results_after == ShowResultsAfter.AFTER_CLOSE:
        _, closes = quiz.window
        return (now or timezone.now()) >= closes
    return False


__all__ = [
    "active_attempt",
    "assert_editable",
    "attempt_questions",
    "attempt_rows",
    "attempts_left",
    "delete_question",
    "draw_question_order",
    "draw_warnings",
    "expire_attempt",
    "finalise_overdue",
    "grade_attempt",
    "is_quiz_stage",
    "may_show_result",
    "RESULTS_CSV_HEADER",
    "question_stats",
    "quiz_for_stage",
    "regrade_quiz",
    "results_csv_rows",
    "save_answers",
    "save_question",
    "save_quiz_settings",
    "stage_scores",
    "start_attempt",
    "submit_attempt",
]
