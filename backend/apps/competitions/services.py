"""Logika domenowa zawodów.

Widoki tylko orkiestrują: walidacja reguł biznesowych, tworzenie obiektów zależnych i błędy
domenowe (``DomainError``) żyją tutaj. Czas zawsze przez ``timezone.now()``.
"""

from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status

from apps.accounts.models import Participant
from apps.core.api import DomainError

from .models import (
    DEFAULT_MAX_VALUE,
    Edition,
    InterviewBooking,
    InterviewSlot,
    Problem,
    QualificationMode,
    QualificationRule,
    ScoringScale,
    Stage,
    StageEntry,
    StageEntryStatus,
    StageFormat,
    StageKind,
    default_scoring_values,
)

#: Pola osi czasu etapu, którymi koordynator zarządza z panelu. ``results_published_at`` i
#: ``closed_at`` są **poza** tą listą świadomie: pierwsze nakłada publikacja wyników, drugie –
#: zamknięcie etapu. Obie wartości są śladem zdarzenia, które już zaszło, a nie planem; ręczne
#: przestawienie ich w formularzu cofałoby skutek operacji, nie zmieniając niczego, co z niej wynikło.
#: ``name`` i ``format`` stoją na początku, bo w takiej kolejności formularz je pokazuje: najpierw
#: „czym jest ten etap”, potem jego oś czasu.
STAGE_EDITABLE_FIELDS = (
    "name",
    "format",
    "location",
    "opens_at",
    "deadline_at",
    "grace_seconds",
    "review_deadline_at",
    "appeal_window_opens_at",
    "appeal_window_closes_at",
)

#: Co wolno zmienić po zamknięciu etapu. Terminy oddania rozwiązań są wtedy faktem historycznym –
#: upload jest zablokowany, a wersje mają status ``LOCKED``, więc przesunięcie ``opens_at`` czy
#: ``deadline_at`` opisywałoby przebieg, który się nie wydarzył. Recenzje i okno reklamacji dopiero
#: przed etapem stoją, a to właśnie one bywają przedłużane (choroba recenzenta, spór o termin).
#: ``name`` jest na tej liście, a ``format`` nie: przemianowanie zamkniętego etapu niczego nie
#: cofa (podpis w archiwum wolno poprawić), natomiast zmiana formy opisywałaby inny przebieg
#: zawodów niż ten, który się odbył.
STAGE_FIELDS_EDITABLE_AFTER_CLOSE = (
    "name",
    "location",
    "review_deadline_at",
    "appeal_window_opens_at",
    "appeal_window_closes_at",
)

#: Pola zadania, którymi zarządza panel. ``statement_pdf`` idzie osobno – jest plikiem.
PROBLEM_EDITABLE_FIELDS = ("number", "title", "allowed_formats", "max_file_mb")

#: Okno rejestracji uczestników – jedyne pola ``Edition``, które panel koordynatora zmienia.
#: ``is_current`` i ``year_label`` zostają w ``/admin/``: przełączenie bieżącej edycji jest
#: operacją na całym serwisie (etapy, wyniki, harmonogram), a nie ustawieniem rejestracji.
REGISTRATION_EDITABLE_FIELDS = (
    "registration_enabled",
    "registration_opens_at",
    "registration_closes_at",
)


def current_edition() -> Edition | None:
    """Bieżąca edycja albo ``None``. Unikalność ``is_current`` gwarantuje constraint w bazie."""
    return Edition.objects.filter(is_current=True).first()


def current_stage(edition: Edition, now=None) -> Stage | None:
    """Etap „na teraz”: otwarty, a jeśli żaden nie jest otwarty – najbliższy przyszły, inaczej ostatni.

    Kolejność jest wyznaczana po ``opens_at``, nie po rodzaju etapu – terminy są jedynym źródłem prawdy.
    """
    now = now or timezone.now()
    stages = list(edition.stages.order_by("opens_at", "id"))
    if not stages:
        return None
    for stage in stages:
        if stage.is_open_for_submissions(now):
            return stage
    upcoming = [stage for stage in stages if stage.opens_at > now]
    if upcoming:
        return upcoming[0]
    return stages[-1]


@transaction.atomic
def create_stage(
    *,
    edition: Edition,
    kind: str,
    opens_at,
    deadline_at,
    review_deadline_at,
    appeal_window_opens_at,
    appeal_window_closes_at,
    grace_seconds: int = 0,
    location: str = "",
    name: str = "",
    format: str = StageFormat.SUBMISSIONS,
    scoring_values: list[dict] | None = None,
    max_value: int | None = None,
    qualification_mode: str = QualificationMode.MIN_POINTS,
    min_points: int | None = 0,
    top_n: int | None = None,
) -> Stage:
    """Tworzy etap wraz z domyślną skalą punktacji i progiem kwalifikacji.

    Obiekty zależne powstają tu, a nie w sygnale ``post_save`` – dzięki temu fabryki testowe
    tworzą dokładnie to, co deklarują, a zachowanie serwisu jest jawne i testowalne.
    """
    stage = Stage(
        edition=edition,
        kind=kind,
        name=name,
        format=format,
        location=location,
        opens_at=opens_at,
        deadline_at=deadline_at,
        grace_seconds=grace_seconds,
        review_deadline_at=review_deadline_at,
        appeal_window_opens_at=appeal_window_opens_at,
        appeal_window_closes_at=appeal_window_closes_at,
    )
    stage.full_clean()
    stage.save()

    values = scoring_values if scoring_values is not None else default_scoring_values()
    scale = ScoringScale(
        stage=stage,
        values=values,
        max_value=max_value if max_value is not None else DEFAULT_MAX_VALUE,
    )
    scale.full_clean()
    scale.save()

    rule = QualificationRule(stage=stage, mode=qualification_mode, min_points=min_points, top_n=top_n)
    rule.full_clean()
    rule.save()
    return stage


def ensure_stage_defaults(stage: Stage) -> None:
    """Dopina domyślną skalę i próg do etapu utworzonego z pominięciem ``create_stage`` (np. admin)."""
    ScoringScale.objects.get_or_create(
        stage=stage, defaults={"values": default_scoring_values(), "max_value": DEFAULT_MAX_VALUE}
    )
    QualificationRule.objects.get_or_create(
        stage=stage, defaults={"mode": QualificationMode.MIN_POINTS, "min_points": 0}
    )


def _registration_closed() -> DomainError:
    return DomainError(
        "Rejestracja do tego etapu jest zamknięta.",
        "REGISTRATION_CLOSED",
        status.HTTP_403_FORBIDDEN,
    )


def _already_registered() -> DomainError:
    return DomainError(
        "Uczestnik jest już zarejestrowany do tego etapu.",
        "ALREADY_REGISTERED",
        status.HTTP_409_CONFLICT,
    )


@transaction.atomic
def register_for_stage(participant: Participant, stage: Stage, *, now=None) -> StageEntry:
    """Samodzielna rejestracja uczestnika do etapu eliminacyjnego.

    Do etapu okręgowego i finału wpisy tworzy wyłącznie kwalifikacja (T-07) – ręczna próba
    kończy się ``STAGE_NOT_OPEN_FOR_REGISTRATION``.
    """
    now = now or timezone.now()
    if stage.kind != StageKind.ELIM:
        raise DomainError(
            "Do tego etapu wpisy tworzy kwalifikacja, nie rejestracja.",
            "STAGE_NOT_OPEN_FOR_REGISTRATION",
            status.HTTP_403_FORBIDDEN,
        )
    if not stage.edition.is_current or not stage.is_open_for_submissions(now):
        # Etap edycji archiwalnej nie przyjmuje rejestracji, nawet jeśli jego terminy są "otwarte".
        raise _registration_closed()
    if StageEntry.objects.filter(participant=participant, stage=stage).exists():
        raise _already_registered()
    try:
        # Savepoint: kolizja unikalności przy wyścigu nie może unieważnić całej transakcji żądania.
        with transaction.atomic():
            return StageEntry.objects.create(
                participant=participant, stage=stage, status=StageEntryStatus.REGISTERED
            )
    except IntegrityError as exc:
        raise _already_registered() from exc


def entries_for_user(user):
    """Wpisy widoczne dla użytkownika. Filtr jest w querysecie, nie w widoku (PROJEKT.md 2.3)."""
    return (
        StageEntry.objects.select_related("stage", "stage__edition", "participant")
        .for_user(user)
        .order_by("stage__opens_at", "id")
    )


# --- zarządzanie etapami z panelu koordynatora ------------------------------------------------


def missing_stage_kinds(edition: Edition) -> list[tuple[str, str]]:
    """Rodzaje etapów, których edycja jeszcze nie ma – lista wyboru przy dodawaniu etapu.

    Para (edycja, rodzaj) jest unikalna w bazie, więc formularz z pełną listą kończyłby się
    ``IntegrityError`` na czwartej próbie. Kolejność jest kolejnością z ``StageKind``.
    """
    taken = set(Stage.objects.filter(edition=edition).values_list("kind", flat=True))
    return [(value, label) for value, label in StageKind.choices if value not in taken]


def stage_has_submissions(stage: Stage) -> bool:
    """Czy do etapu wpłynęło choć jedno rozwiązanie (dowolnej wersji i dowolnego statusu).

    Import jest lokalny: ``apps.submissions`` zaciąga ``apps.competitions`` przy starcie, więc
    zależność w drugą stronę na poziomie modułu byłaby cyklem.
    """
    from apps.submissions.models import Submission

    return Submission.objects.filter(entry__stage=stage).exists()


def stage_has_interview_bookings(stage: Stage) -> bool:
    """Czy ktokolwiek zapisał się już na rozmowę w tym etapie."""
    return InterviewBooking.objects.filter(slot__stage=stage).exists()


def slots_outside_window(stage: Stage, opens_at, deadline_at) -> bool:
    """Czy któryś termin rozmowy wypadłby poza oknem ``[opens_at, deadline_at]``.

    Pytanie zadajemy przez negację (``exclude`` warunku „mieści się w całości”), bo termin
    wystający oknem choćby o minutę – z jednej albo z drugiej strony – jest tak samo zły:
    ``create_slots`` nie pozwoliłby go założyć, więc przesunięcie okna nie może go po cichu
    stworzyć.
    """
    return (
        InterviewSlot.objects.filter(stage=stage)
        .exclude(starts_at__gte=opens_at, ends_at__lte=deadline_at)
        .exists()
    )


def _audit_value(value):
    """Wartość do wpisu audytowego: daty w ISO, reszta bez zmian (``diff`` jest JSON-em)."""
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _stage_closed_error(fields: list[str]) -> DomainError:
    return DomainError(
        "Etap jest zamknięty – można w nim zmienić już tylko termin recenzji, okno reklamacji "
        f"i miejsce zawodów. Zablokowane pola: {', '.join(fields)}.",
        "STAGE_CLOSED",
        status.HTTP_409_CONFLICT,
    )


@transaction.atomic
def update_stage(stage: Stage, actor, *, request=None, now=None, **fields) -> Stage:
    """Zmiana osi czasu etapu z panelu koordynatora. Zwraca etap po zapisie.

    Reguły, których nie da się wyrazić w ``Stage.clean()``, bo zależą od stanu innych tabel
    i od zegara:

    - **etap zamknięty** (``closed_at``) przyjmuje już tylko terminy, które go poprzedzają w czasie:
      recenzje, okno reklamacji i miejsce (patrz ``STAGE_FIELDS_EDITABLE_AFTER_CLOSE``),
    - **deadline nie cofa się w przeszłość, gdy są zgłoszenia.** Uczestnik, który oddał pracę
      zgodnie z ogłoszonym terminem, nie może po fakcie znaleźć się „po deadline”; a gdyby etap
      zamknął się w tej samej chwili, jego wersje dostałyby ``LOCKED`` z datą, której nie było
      na stronie w chwili uploadu,
    - **formy nie zmienia się w etapie, który już się toczy.** Przestawienie „rozwiązania pisemne”
      na „rozmowa” w etapie z oddanymi pracami unieważniałoby te prace bez śladu, a odwrotna
      zmiana zostawiałaby zapisy na terminy, których nikt już nie obsłuży. Tak samo blokują
      **zadania**: etap w formie rozmowy zadań mieć nie może (``create_problem`` odmawia), więc
      zmiana formy zostawiłaby arkusz-sierotę, niewidoczny w panelu i nieusuwalny z niego,
    - **okna etapu nie zawęża się pod wyznaczonymi terminami rozmów.** Termin poza oknem etapu to
      godzina, której nie ma na harmonogramie – a uczestnik zapisany na 9:00 nie dowiedziałby się,
      że etap „kończy się” o 8:00.

    Kolejność terminów sprawdza ``full_clean()`` – ta sama reguła, co w formularzu i w bazie.
    Blokada ``select_for_update`` szereguje dwa równoległe zapisy: bez niej druga transakcja
    czytałaby stan sprzed pierwszej i zapisywała diff względem nieaktualnych wartości.
    """
    now = now or timezone.now()
    unknown = sorted(set(fields) - set(STAGE_EDITABLE_FIELDS))
    if unknown:  # pragma: no cover - błąd programisty, nie danych
        raise ValueError(f"Pola spoza zakresu edycji etapu: {', '.join(unknown)}.")

    locked = Stage.objects.select_for_update().get(pk=stage.pk)
    changed = {name: value for name, value in fields.items() if getattr(locked, name) != value}
    if not changed:
        return locked

    if locked.closed_at is not None:
        blocked = sorted(set(changed) - set(STAGE_FIELDS_EDITABLE_AFTER_CLOSE))
        if blocked:
            raise _stage_closed_error(blocked)

    if "deadline_at" in changed and changed["deadline_at"] < now and stage_has_submissions(locked):
        raise DomainError(
            "Do etapu wpłynęły już rozwiązania – terminu oddania nie można cofnąć w przeszłość. "
            "Wybierz termin w przyszłości albo zamknij etap ręcznie.",
            "STAGE_DEADLINE_IN_PAST",
            status.HTTP_409_CONFLICT,
        )

    if "format" in changed and (
        stage_has_submissions(locked) or stage_has_interview_bookings(locked) or locked.problems.exists()
    ):
        raise DomainError(
            "Etap ma już oddane rozwiązania, zapisy na rozmowy albo zadania – formy nie można "
            "zmienić. Utwórz nowy etap, jeżeli zawody mają się odbyć inaczej.",
            "STAGE_FORMAT_LOCKED",
            status.HTTP_409_CONFLICT,
        )

    if locked.is_interview and ("opens_at" in changed or "deadline_at" in changed):
        new_opens = changed.get("opens_at", locked.opens_at)
        new_deadline = changed.get("deadline_at", locked.deadline_at)
        if slots_outside_window(locked, new_opens, new_deadline):
            raise DomainError(
                "Etap ma wyznaczone terminy rozmów, które nie zmieściłyby się w nowym oknie "
                f"({timezone.localtime(new_opens):%Y-%m-%d %H:%M} – "
                f"{timezone.localtime(new_deadline):%Y-%m-%d %H:%M}, czas polski). Najpierw usuń "
                "albo przenieś te terminy.",
                "STAGE_WINDOW_HAS_SLOTS",
                status.HTTP_409_CONFLICT,
            )

    diff = {
        name: {"from": _audit_value(getattr(locked, name)), "to": _audit_value(value)}
        for name, value in changed.items()
    }
    for name, value in changed.items():
        setattr(locked, name, value)
    locked.full_clean()
    locked.save(update_fields=list(changed))

    from apps.core.models import audit

    audit(actor, "stage.updated", locked, diff, request=request)
    for name, value in changed.items():
        setattr(stage, name, value)
    return locked


# --- okno rejestracji uczestników --------------------------------------------------------------


@transaction.atomic
def update_registration_window(edition: Edition, *, actor, request=None, **fields) -> Edition:
    """Zmiana okna rejestracji uczestników z panelu koordynatora. Zwraca edycję po zapisie.

    Reguła jest jedna i wyrażalna w modelu (otwarcie przed zamknięciem), więc pilnuje jej
    ``full_clean()`` – ten sam warunek, co constraint w bazie i co komunikat pod polem formularza.
    Serwis dokłada do tego trzy rzeczy, których formularz dać nie może: blokadę wiersza (dwa
    równoległe zapisy nie mogą policzyć różnicy względem nieaktualnego stanu), wpis audytowy
    z różnicą pól i jedno wejście dla ewentualnych innych wywołujących.

    Wyłączenie rejestracji **nie rusza** zapisanych terminów: koordynator, który zatrzymuje zapisy
    na godzinę, ma po ponownym włączeniu odzyskać to samo okno, a nie puste pola.
    """
    unknown = sorted(set(fields) - set(REGISTRATION_EDITABLE_FIELDS))
    if unknown:  # pragma: no cover - błąd programisty, nie danych
        raise ValueError(f"Pola spoza zakresu rejestracji: {', '.join(unknown)}.")

    locked = Edition.objects.select_for_update().get(pk=edition.pk)
    changed = {name: value for name, value in fields.items() if getattr(locked, name) != value}
    if not changed:
        return locked

    diff = {
        name: {"from": _audit_value(getattr(locked, name)), "to": _audit_value(value)}
        for name, value in changed.items()
    }
    for name, value in changed.items():
        setattr(locked, name, value)
    try:
        locked.full_clean()
    except ValidationError as exc:
        raise DomainError(
            "; ".join(exc.messages), "REGISTRATION_WINDOW_INVALID", status.HTTP_400_BAD_REQUEST
        ) from exc
    locked.save(update_fields=list(changed))

    from apps.core.models import audit

    audit(actor, "edition.registration_updated", locked, diff, request=request)
    for name, value in changed.items():
        setattr(edition, name, value)
    return locked


# --- zarządzanie zadaniami z panelu koordynatora ----------------------------------------------


def _duplicate_number(number) -> DomainError:
    return DomainError(
        f"Zadanie o numerze {number} już istnieje w tym etapie.",
        "PROBLEM_NUMBER_TAKEN",
        status.HTTP_409_CONFLICT,
    )


def _validation_error(exc: ValidationError) -> DomainError:
    """``ValidationError`` modelu → błąd domenowy z czytelnym komunikatem dla panelu."""
    return DomainError("; ".join(exc.messages), "PROBLEM_INVALID", status.HTTP_400_BAD_REQUEST)


@transaction.atomic
def create_problem(*, stage: Stage, actor, statement=None, request=None, **fields) -> Problem:
    """Nowe zadanie etapu. ``statement`` to plik z formularza albo ``None``.

    Etap w formie rozmowy zadań nie ma i mieć nie może: nie ma czego oddać, więc arkusz zadań
    byłby treścią bez odbiorcy, a uczestnik zobaczyłby w panelu upload, którego serwis i tak by
    nie przyjął (``submissions.create_submission``). Odmowa jest jednym warunkiem – lepsza niż
    ukrywanie przycisku, bo to samo żądanie wysłane skryptem też musi dostać 409.
    """
    unknown = sorted(set(fields) - set(PROBLEM_EDITABLE_FIELDS))
    if unknown:  # pragma: no cover - błąd programisty, nie danych
        raise ValueError(f"Pola spoza zakresu zadania: {', '.join(unknown)}.")
    if stage.is_interview:
        raise DomainError(
            "Ten etap ma formę rozmowy kwalifikacyjnej – nie ma w nim zadań do oddania. "
            "Terminy rozmów wyznaczasz na osobnym ekranie.",
            "STAGE_NOT_ACCEPTING_PROBLEMS",
            status.HTTP_409_CONFLICT,
        )

    problem = Problem(stage=stage, **fields)
    if statement is not None:
        problem.statement_pdf = statement
    try:
        problem.full_clean()
    except ValidationError as exc:
        raise _validation_error(exc) from exc
    try:
        # Savepoint: wyścig o ten sam numer nie może unieważnić całej transakcji żądania.
        with transaction.atomic():
            problem.save()
    except IntegrityError as exc:
        raise _duplicate_number(problem.number) from exc

    from apps.core.models import audit

    audit(
        actor,
        "problem.created",
        problem,
        {
            "stage": stage.pk,
            "number": problem.number,
            "allowed_formats": list(problem.allowed_formats or []),
            "max_file_mb": problem.max_file_mb,
            "has_statement": bool(problem.statement_pdf),
        },
        request=request,
    )
    return problem


@transaction.atomic
def update_problem(
    problem: Problem,
    actor,
    *,
    statement=None,
    confirm_open_stage: bool = False,
    request=None,
    now=None,
    **fields,
) -> Problem:
    """Zmiana zadania. Podmiana treści po otwarciu etapu wymaga jawnego potwierdzenia.

    Uczestnik, który pobrał PDF w pierwszej godzinie etapu, rozwiązuje **tę** wersję zadania:
    cicha podmiana pliku dzieli zawodników na dwie grupy z różnym poleceniem. Potwierdzenie nie
    zabrania operacji (bywa konieczna – literówka w treści), tylko wymusza świadomą decyzję,
    po której koordynator ogłasza erratę.
    """
    now = now or timezone.now()
    unknown = sorted(set(fields) - set(PROBLEM_EDITABLE_FIELDS))
    if unknown:  # pragma: no cover - błąd programisty, nie danych
        raise ValueError(f"Pola spoza zakresu zadania: {', '.join(unknown)}.")

    locked = Problem.objects.select_for_update().select_related("stage").get(pk=problem.pk)
    if statement is not None and locked.stage.has_opened(now) and not confirm_open_stage:
        raise DomainError(
            "Etap jest już otwarty, a uczestnicy widzą treść tego zadania. Podmiana pliku wymaga "
            "potwierdzenia w formularzu.",
            "STATEMENT_CHANGE_NEEDS_CONFIRMATION",
            status.HTTP_400_BAD_REQUEST,
        )

    changed = {name: value for name, value in fields.items() if getattr(locked, name) != value}
    diff = {
        name: {"from": _audit_value(getattr(locked, name)), "to": _audit_value(value)}
        for name, value in changed.items()
    }
    for name, value in changed.items():
        setattr(locked, name, value)
    if statement is not None:
        # Przypisanie nowego pliku do ``FileField`` zapisuje go pod nową nazwą; stary plik zostaje
        # w storage (kasowanie jest poza zakresem – w prywatnym buckecie nic go nie wystawia).
        diff["statement_pdf"] = {"from": locked.statement_pdf.name or "", "to": statement.name}
        locked.statement_pdf = statement
    if not diff:
        return locked

    try:
        locked.full_clean()
    except ValidationError as exc:
        raise _validation_error(exc) from exc
    try:
        with transaction.atomic():
            locked.save()
    except IntegrityError as exc:
        raise _duplicate_number(locked.number) from exc

    from apps.core.models import audit

    audit(actor, "problem.updated", locked, diff, request=request)
    return locked


@transaction.atomic
def delete_problem(problem: Problem, actor, *, request=None) -> None:
    """Usunięcie zadania. Możliwe wyłącznie, dopóki nikt nie oddał do niego rozwiązania.

    ``Submission.problem`` jest kluczem z ``CASCADE``, więc skasowanie zadania z pracami zabrałoby
    ze sobą rozwiązania, recenzje i oceny – a te są dowodem przebiegu zawodów.
    """
    from apps.submissions.models import Submission

    if Submission.objects.filter(problem=problem).exists():
        raise DomainError(
            "Do zadania wpłynęły rozwiązania – nie można go usunąć.",
            "PROBLEM_HAS_SUBMISSIONS",
            status.HTTP_409_CONFLICT,
        )

    from apps.core.models import audit

    # Audyt przed skasowaniem: po ``delete()`` obiekt nie ma już ``pk``, więc wpis wskazywałby
    # na ``None`` i nie dałoby się go połączyć z historią zadania.
    audit(
        actor,
        "problem.deleted",
        problem,
        {"stage": problem.stage_id, "number": problem.number, "title": problem.title},
        request=request,
    )
    problem.delete()
